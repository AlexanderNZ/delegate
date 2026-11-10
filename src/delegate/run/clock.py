"""The clock of `delegate watch` and `delegate status`: the wall time, the monotonic time, and the wait (#297).

The watch and the status read the time and wait only through a `Clock`. The commands use the clock of the process
(`current`), which is the system clock (`SYSTEM_CLOCK`) unless `use` sets another clock. A test gives its own clock
with `use`, so it does not replace a function of the module `time`: such a replacement also changes the time and the
waits of each library that the command calls, as the wait of `subprocess` for a git command with a time limit.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """A source of time, and a wait."""

    def now(self) -> float:
        """The wall time, in seconds since the epoch, as `time.time`."""
        ...

    def monotonic(self) -> float:
        """A time in seconds that never goes back, as `time.monotonic`. Only the difference of two values has a meaning."""
        ...

    def sleep(self, seconds: float) -> None:
        """Wait `seconds`, as `time.sleep`."""
        ...


class SystemClock:
    """The clock of the system: the functions of the module `time`."""

    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


SYSTEM_CLOCK: Clock = SystemClock()

_current: Clock = SYSTEM_CLOCK


def current() -> Clock:
    """The clock of the commands of this process."""
    return _current


@contextmanager
def use(clock: Clock) -> Iterator[Clock]:
    """Make `clock` the clock of the commands of this process until the block ends, then put back the clock before it."""
    global _current
    before, _current = _current, clock
    try:
        yield clock
    finally:
        _current = before


def utc_now(clock: Clock) -> datetime:
    """The wall time of `clock`, in UTC."""
    return datetime.fromtimestamp(clock.now(), timezone.utc)
