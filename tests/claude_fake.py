"""A stand-in for the `claude` command, for the tests of the Claude Code adapter.

It replays a recorded stream from `tests/fixtures/claude-code`. See `harness_fake`.
"""

from __future__ import annotations

from pathlib import Path

from . import harness_fake
from .harness_fake import FakeHarness as FakeClaude

FIXTURES: Path = Path(__file__).parent / "fixtures" / "claude-code"


def install(
    tmp_path: Path, monkeypatch, fixture: str, *, exit_status: int = 0, stderr: str = "", drip_seconds: float = 0,
    hold_seconds: float = 0, start_seconds: float = 0,
) -> FakeClaude:
    """Put a `claude` command that replays `fixture` first on PATH. See `harness_fake.install` for the options."""
    return harness_fake.install(
        tmp_path, monkeypatch, "claude", FIXTURES, fixture, exit_status=exit_status, stderr=stderr,
        drip_seconds=drip_seconds, hold_seconds=hold_seconds, start_seconds=start_seconds,
    )


VERSION_SCRIPT = """\
#!{python}
import sys
if sys.argv[1:] == ["--version"]:
    print("{version} (Claude Code)")
    sys.exit(0)
sys.exit(1)
"""


def install_version(tmp_path: Path, monkeypatch, version: str = "2.1.287") -> None:
    """Put a `claude` command first on PATH that answers `--version` with `version`, and fails for any other argument.

    The seal of the Claude Code adapter asks the harness for its version. The command stands in for the harness
    at that boundary, so a test needs no `claude` on the machine.
    """
    import os
    import stat
    import sys

    bin_dir = tmp_path / "version-bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / "claude"
    executable.write_text(VERSION_SCRIPT.format(python=sys.executable, version=version))
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
