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
