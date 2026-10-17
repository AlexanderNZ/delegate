"""A stand-in for a harness command, for the tests of the harness adapters.

The harness process is the system boundary of an adapter. The stand-in is a
real executable on PATH: it records its arguments, its standard input, its
working directory, its environment and its session id, then replays a recorded stream and exits with a status that
the test sets. The adapter starts it as a subprocess, as it starts the real
command.

A test can make the stand-in write one line of the stream each `drip_seconds`,
as a harness that works writes its events, and stay alive for `hold_seconds`
after the stream, as an idle session that does not exit does. With
`start_seconds`, the stand-in waits before it does anything, as a process that
starts slowly on a machine with a high load does.
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
import json, os, sys, time
time.sleep(float(os.environ.get("FAKE_HARNESS_START", "0")))
with open(os.environ["FAKE_HARNESS_LOG"], "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd(), "env": dict(os.environ), "sid": os.getsid(0)}}) + "\\n")
drip = float(os.environ.get("FAKE_HARNESS_DRIP", "0"))
with open(os.environ["FAKE_HARNESS_STREAM"]) as stream:
    for line in stream:
        sys.stdout.write(line)
        sys.stdout.flush()
        time.sleep(drip)
sys.stderr.write(os.environ.get("FAKE_HARNESS_STDERR", ""))
time.sleep(float(os.environ.get("FAKE_HARNESS_HOLD", "0")))
sys.exit(int(os.environ.get("FAKE_HARNESS_EXIT", "0")))
"""


def wrote(stream: Path, text: str):
    """A report check that is true when the event stream starts with `text`.

    A real session writes its report while it works, so its report becomes
    valid after the session wrote events. A check that is true before the
    process writes anything lets the idle timeout end a process that only
    starts slowly.
    """
    return lambda: stream.is_file() and stream.read_text().startswith(text)


@dataclass(frozen=True)
class FakeHarness:
    """The installed stand-in. `calls()` returns what each start of the command received."""

    log: Path

    def calls(self) -> list[dict[str, object]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


def install(
    tmp_path: Path, monkeypatch, command: str, fixtures: Path, fixture: str, *, exit_status: int = 0, stderr: str = "",
    drip_seconds: float = 0, hold_seconds: float = 0, start_seconds: float = 0,
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
    monkeypatch.setenv("FAKE_HARNESS_DRIP", str(drip_seconds))
    monkeypatch.setenv("FAKE_HARNESS_HOLD", str(hold_seconds))
    monkeypatch.setenv("FAKE_HARNESS_START", str(start_seconds))
    return FakeHarness(log)
