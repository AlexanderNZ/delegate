"""The fixture of the tests of `delegate runs`: a state root, a machine name, a zone and a clock of the case."""

from __future__ import annotations

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
