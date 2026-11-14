"""The fixture of the tests of `delegate runs`: a state root, a machine name, a zone and a clock of the case."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from .journals import NOW


@pytest.fixture(autouse=True)
def machine(tmp_path, monkeypatch, clock) -> Path:
    """The state root of the case, the name of the machine, the zone UTC, the UTF-8 locale, a terminal of 80 columns and 1000 lines, and the clock at `NOW`. Return the state root."""
    root = tmp_path / "state-root"
    root.mkdir()
    monkeypatch.setenv("DELEGATE_STATE_ROOT", str(root))
    monkeypatch.setenv("DELEGATE_HOST", "laptop")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "1000")
    time.tzset()
    clock.at = NOW.timestamp()
    yield root
    monkeypatch.undo()
    time.tzset()


class SshShim:
    """The `ssh` of the case: a command shim on `PATH` that stands for the other hosts of the run index (#318).

    It writes each call, with its arguments, to `calls`. A host with `serves` prints its document and exits 0. A host with `exits` prints nothing and exits with
    that status. A host with `says` writes that text on standard error. A host with `hangs` waits 30 seconds. A host with `delays` waits some seconds, then prints its document. Any other host exits 255, as `ssh` does when it cannot reach a host.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        script = directory / "bin" / "ssh"
        script.parent.mkdir(parents=True)
        script.write_text(
            "#!/bin/sh\n"
            f'dir="{directory}"\n'
            'host="$5"\n'
            'printf "%s\\n" "$*" >> "$dir/calls"\n'
            'if [ -f "$dir/$host.delay" ]; then\n'
            '  printf "start %s\\n" "$host" >> "$dir/order"\n'
            '  sleep "$(cat "$dir/$host.delay")"\n'
            '  printf "end %s\\n" "$host" >> "$dir/order"\n'
            "fi\n"
            '[ -f "$dir/$host.hang" ] && sleep 30\n'
            '[ -f "$dir/$host.err" ] && cat "$dir/$host.err" >&2\n'
            '[ -f "$dir/$host.code" ] && exit "$(cat "$dir/$host.code")"\n'
            '[ -f "$dir/$host.out" ] || exit 255\n'
            'cat "$dir/$host.out"\n'
        )
        script.chmod(0o755)

    @property
    def bin(self) -> Path:
        return self.directory / "bin"

    def serves(self, host: str, document: object) -> None:
        """`ssh <host> ...` prints `document` as JSON."""
        (self.directory / f"{host}.out").write_text(json.dumps(document))

    def prints(self, host: str, text: str) -> None:
        """`ssh <host> ...` prints `text` as it is."""
        (self.directory / f"{host}.out").write_text(text)

    def exits(self, host: str, code: int) -> None:
        """`ssh <host> ...` exits with `code`, whatever it printed."""
        (self.directory / f"{host}.code").write_text(str(code))

    def says(self, host: str, text: str) -> None:
        """`ssh <host> ...` writes `text` on standard error."""
        (self.directory / f"{host}.err").write_text(text)

    def hangs(self, host: str) -> None:
        """`ssh <host> ...` waits 30 seconds."""
        (self.directory / f"{host}.hang").write_text("")

    def delays(self, host: str, seconds: float) -> None:
        """`ssh <host> ...` waits `seconds`, then prints its document."""
        (self.directory / f"{host}.delay").write_text(str(seconds))

    @property
    def calls(self) -> list[list[str]]:
        """The arguments of each call of the shim, in the order of the calls."""
        path = self.directory / "calls"
        return [line.split() for line in path.read_text().splitlines()] if path.exists() else []

    @property
    def order(self) -> list[str]:
        """`start <host>` and `end <host>` for each call of a host with `delays`, in the order that they happened."""
        path = self.directory / "order"
        return path.read_text().splitlines() if path.exists() else []


@pytest.fixture(autouse=True)
def ssh(tmp_path, monkeypatch) -> SshShim:
    """The `ssh` of the case, first on `PATH`. A host that a case does not serve is unreachable, as with no network: no case starts the real `ssh`."""
    shim = SshShim(tmp_path / "ssh-shim")
    monkeypatch.setenv("PATH", f"{shim.bin}{os.pathsep}{os.environ['PATH']}")
    return shim
