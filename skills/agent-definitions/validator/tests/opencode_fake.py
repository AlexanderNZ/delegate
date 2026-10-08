"""A stand-in for the `opencode` command, for the tests of the OpenCode adapter.

It replays a recorded stream from `tests/fixtures/opencode`. See `harness_fake`.
"""

from __future__ import annotations

from pathlib import Path

from . import harness_fake
from .harness_fake import FakeHarness as FakeOpenCode

FIXTURES: Path = Path(__file__).parent / "fixtures" / "opencode"


def install(tmp_path: Path, monkeypatch, fixture: str, *, exit_status: int = 0, stderr: str = "") -> FakeOpenCode:
    """Put an `opencode` command that replays `fixture` first on PATH."""
    return harness_fake.install(tmp_path, monkeypatch, "opencode", FIXTURES, fixture, exit_status=exit_status, stderr=stderr)
