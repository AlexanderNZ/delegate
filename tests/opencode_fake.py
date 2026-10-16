"""A stand-in for the `opencode` command, for the tests of the OpenCode adapter.

It replays a recorded stream from `tests/fixtures/opencode`. See `harness_fake`.
"""

from __future__ import annotations

from pathlib import Path

from . import harness_fake
from .harness_fake import FakeHarness as FakeOpenCode

FIXTURES: Path = Path(__file__).parent / "fixtures" / "opencode"


def install(
    tmp_path: Path, monkeypatch, fixture: str, *, exit_status: int = 0, stderr: str = "", drip_seconds: float = 0,
    hold_seconds: float = 0,
) -> FakeOpenCode:
    """Put an `opencode` command that replays `fixture` first on PATH. See `harness_fake.install` for the options."""
    return harness_fake.install(
        tmp_path, monkeypatch, "opencode", FIXTURES, fixture, exit_status=exit_status, stderr=stderr,
        drip_seconds=drip_seconds, hold_seconds=hold_seconds,
    )
