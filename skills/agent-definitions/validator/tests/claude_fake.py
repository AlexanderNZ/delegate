"""A stand-in for the `claude` command, for the tests of the Claude Code adapter.

The harness process is the system boundary of the adapter. The stand-in is a
real executable on PATH: it records its arguments and its standard input, then
replays a recorded stream from `tests/fixtures/claude-code` and exits with a
status that the test sets. The adapter starts it as a subprocess, as it starts
the real command.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

FIXTURES: Path = Path(__file__).parent / "fixtures" / "claude-code"

SCRIPT = """\
#!{python}
import json, os, sys
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd()}}) + "\\n")
with open(os.environ["FAKE_CLAUDE_STREAM"]) as stream:
    sys.stdout.write(stream.read())
sys.stderr.write(os.environ.get("FAKE_CLAUDE_STDERR", ""))
sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
"""


@dataclass(frozen=True)
class FakeClaude:
    """The installed stand-in. `calls()` returns what each start of the command received."""

    log: Path

    def calls(self) -> list[dict[str, object]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


def install(tmp_path: Path, monkeypatch, fixture: str, *, exit_status: int = 0, stderr: str = "") -> FakeClaude:
    """Put a `claude` command that replays `fixture` first on PATH."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    command = bin_dir / "claude"
    command.write_text(SCRIPT.format(python=sys.executable))
    command.chmod(command.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "fake-claude.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_STREAM", str(FIXTURES / fixture) if fixture else os.devnull)
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", str(exit_status))
    monkeypatch.setenv("FAKE_CLAUDE_STDERR", stderr)
    return FakeClaude(log)
