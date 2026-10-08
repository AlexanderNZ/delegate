"""A stand-in for the `claude` command, for the tests of the Claude Code adapter.

It replays a recorded stream from `tests/fixtures/claude-code`. See `harness_fake`.
"""

from __future__ import annotations

from pathlib import Path

from . import harness_fake
from .harness_fake import FakeHarness as FakeClaude

FIXTURES: Path = Path(__file__).parent / "fixtures" / "claude-code"


def install(tmp_path: Path, monkeypatch, fixture: str, *, exit_status: int = 0, stderr: str = "") -> FakeClaude:
    """Put a `claude` command that replays `fixture` first on PATH."""
    return harness_fake.install(tmp_path, monkeypatch, "claude", FIXTURES, fixture, exit_status=exit_status, stderr=stderr)
