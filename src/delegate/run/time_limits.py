"""The time limit of a stack, in the units that the engine and the reasons use (#341)."""

from __future__ import annotations


def seconds(minutes: float | None) -> float | None:
    """A time limit of a stack, given in minutes, in seconds. None stays None: no limit."""
    return None if minutes is None else minutes * 60


def minutes_text(minutes: float | None) -> str:
    """A time limit in minutes as the reasons and the notes write it: `10` for 10.0, `0.05` for 0.05."""
    return f"{minutes:g}"
