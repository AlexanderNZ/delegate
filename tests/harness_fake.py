"""A stand-in for a harness command, for the tests of the harness adapters.

The harness process is the system boundary of an adapter. The stand-in is a
real executable on PATH: it records its arguments, its standard input, its
working directory, its environment and its session id, then replays a recorded stream and exits with a status that
the test sets. The adapter starts it as a subprocess, as it starts the real
command.

The stand-in answers `--version` with `FAKE_HARNESS_VERSION` (by default `2.1.287 (Claude Code)`), and records nothing
for that call.

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
import json, os, subprocess, sys, time
if sys.argv[1:] == ["--version"]:
    # The version query of an adapter that seals an agent: no session, no log entry, and no stream.
    print(os.environ.get("FAKE_HARNESS_VERSION", "2.1.287 (Claude Code)"))
    sys.exit(0)
stdin = sys.stdin.read()
probe = os.environ.get("FAKE_HARNESS_PROBE")
if probe and "touch delegate-guard-probe" in stdin:
    # A guard probe (#219): a separate log, and a recorded stream of its own, whatever the options of the test say.
    with open(os.environ["FAKE_HARNESS_PROBE_LOG"], "a") as log:
        log.write(json.dumps({{"argv": sys.argv[1:], "stdin": stdin, "cwd": os.getcwd()}}) + "\\n")
    if os.environ.get("FAKE_HARNESS_PROBE_TOUCH"):
        open("delegate-guard-probe", "w").close()
    # A list of streams, joined with the path separator, gives one stream for each probe session; the last one repeats.
    streams = probe.split(os.pathsep)
    with open(os.environ["FAKE_HARNESS_PROBE_LOG"]) as log:
        number = len(log.readlines())
    with open(streams[min(number, len(streams)) - 1]) as stream:
        sys.stdout.write(stream.read())
    sys.exit(0)
time.sleep(float(os.environ.get("FAKE_HARNESS_START", "0")))
with open(os.environ["FAKE_HARNESS_LOG"], "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "stdin": stdin, "cwd": os.getcwd(), "env": dict(os.environ), "sid": os.getsid(0), "pid": os.getpid()}}) + "\\n")
if os.environ.get("FAKE_HARNESS_CHILD"):
    # A child of the harness, as a tool process of a real harness is: it stays alive until something ends its group.
    with open(os.environ["FAKE_HARNESS_CHILD"], "w") as child_pid:
        child_pid.write(str(subprocess.Popen(["sleep", "300"]).pid))
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
    probe_log: Path | None = None

    def calls(self) -> list[dict[str, object]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def probe_calls(self) -> list[dict[str, object]]:
        """The guard probes (#219) that the command received: its arguments, its standard input and its working directory."""
        if self.probe_log is None or not self.probe_log.exists():
            return []
        return [json.loads(line) for line in self.probe_log.read_text().splitlines()]


def install(
    tmp_path: Path, monkeypatch, command: str, fixtures: Path, fixture: str, *, exit_status: int = 0, stderr: str = "",
    drip_seconds: float = 0, hold_seconds: float = 0, start_seconds: float = 0, probe: str = "",
    probe_touches: bool = False,
) -> FakeHarness:
    """Put a `command` that replays `fixture` first on PATH. An empty `fixture` replays nothing.

    A session whose prompt asks for the guard probe command (#219) replays the stream file `probe` (an absolute path) instead, and
    exits 0. It writes the probe file into its working directory when `probe_touches` is set, as a harness whose guard did
    not run does. An empty `probe`, or a file that does not exist, makes the stand-in treat a probe as any
    other session.
    """
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
    probe_log = tmp_path / "fake-harness-probe.log"
    if probe and Path(probe).is_file():
        monkeypatch.setenv("FAKE_HARNESS_PROBE", probe)
        monkeypatch.setenv("FAKE_HARNESS_PROBE_LOG", str(probe_log))
        monkeypatch.setenv("FAKE_HARNESS_PROBE_TOUCH", "1" if probe_touches else "")
    return FakeHarness(log, probe_log)
