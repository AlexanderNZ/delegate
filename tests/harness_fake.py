"""A stand-in for a harness command, for the tests of the harness adapters.

The harness process is the system boundary of an adapter. The stand-in is a
real executable on PATH: it records its arguments, its standard input and its
working directory, then replays a recorded stream and exits with a status that
the test sets. The adapter starts it as a subprocess, as it starts the real
command.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT = """\
#!{python}
import json, os, sys
with open(os.environ["FAKE_HARNESS_LOG"], "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd()}}) + "\\n")
with open(os.environ["FAKE_HARNESS_STREAM"]) as stream:
    sys.stdout.write(stream.read())
sys.stderr.write(os.environ.get("FAKE_HARNESS_STDERR", ""))
sys.exit(int(os.environ.get("FAKE_HARNESS_EXIT", "0")))
"""


@dataclass(frozen=True)
class FakeHarness:
    """The installed stand-in. `calls()` returns what each start of the command received."""

    log: Path

    def calls(self) -> list[dict[str, object]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


def install(
    tmp_path: Path, monkeypatch, command: str, fixtures: Path, fixture: str, *, exit_status: int = 0, stderr: str = ""
) -> FakeHarness:
    """Put a `command` that replays `fixture` first on PATH. An empty `fixture` replays nothing."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    executable = bin_dir / command
    executable.write_text(SCRIPT.format(python=sys.executable))
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "fake-harness.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_HARNESS_LOG", str(log))
    monkeypatch.setenv("FAKE_HARNESS_STREAM", str(fixtures / fixture) if fixture else os.devnull)
    monkeypatch.setenv("FAKE_HARNESS_EXIT", str(exit_status))
    monkeypatch.setenv("FAKE_HARNESS_STDERR", stderr)
    return FakeHarness(log)
