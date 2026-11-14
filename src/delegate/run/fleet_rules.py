"""The rules of the fleet lines and of the totals of a host in `delegate runs` (#308): pure functions of plain values.

The spec is `design/docs/components.md`, C24 and C25. This module imports nothing from the package and does no I/O
(`tests/test_architecture.py` checks it), so a rule never reads a journal, a clock or a terminal. The use case
(`fleet.py`) turns each listed run into a `RunFacts`, calls the rules, and writes the words and the glyphs.

The runs of the sums are the runs that have no `run-end`. A run that ended, and a run that failed at start, are out of
each sum. The sums are over the runs that the caller gives, so a host total gives the runs of one host, and the fleet
line gives the runs of the machine.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

# The states of a run that are trouble by themselves: a run that stopped with no `run-end`, and a run that halted.
TROUBLE_STATES: frozenset[str] = frozenset({"stopped", "halted"})


@dataclass(frozen=True)
class RunFacts:
    """What the rules need to know of one listed run.

    `ended` is True for a run with a `run-end`, and for a run that failed at start (`failed_start`). `eta` is None for a
    run that has no estimate and for a run that does not run. `idle_seconds` is the longest time that an agent of the
    run is idle, or None when no agent runs or the run has no reader of its streams. The two token counts are None
    when the run has none.
    """

    state: str
    started: datetime
    ended: bool
    failed_start: bool
    tickets_ended: int
    tickets_total: int
    eta: datetime | None
    tokens_new: int | None
    tokens_cache: int | None
    idle_seconds: int | None


@dataclass(frozen=True)
class Totals:
    """The sums of the runs with no `run-end`: the tickets that ended, the tickets, the latest ETA, the runs with no ETA, and each token count.

    `tokens_new` and `tokens_cache` are two sums, never added to each other. Each is None when no run has the count.
    """

    tickets_ended: int
    tickets_total: int
    eta: datetime | None
    without_eta: int
    tokens_new: int | None
    tokens_cache: int | None


def counted(runs: Sequence[RunFacts]) -> list[RunFacts]:
    """The runs of the sums: the runs with no `run-end`."""
    return [run for run in runs if not run.ended]


def _sum(counts: Sequence[int | None]) -> int | None:
    """The sum of the counts that exist, or None when none exists."""
    present = [count for count in counts if count is not None]
    return sum(present) if present else None


def totals(runs: Sequence[RunFacts]) -> Totals:
    """The sums of the runs of `runs` that have no `run-end`. An ended run is out of each sum."""
    active = counted(runs)
    etas = [run.eta for run in active if run.eta is not None]
    return Totals(
        sum(run.tickets_ended for run in active), sum(run.tickets_total for run in active), max(etas) if etas else None,
        len(active) - len(etas), _sum([run.tokens_new for run in active]), _sum([run.tokens_cache for run in active]),
    )


def percent(total: Totals) -> int:
    """The progress of the runs: the tickets that ended over the tickets, as a whole number rounded down. Never the mean of the percentages of the runs.

    No ticket gives 0: with no run to count, there is no progress to show.
    """
    return total.tickets_ended * 100 // total.tickets_total if total.tickets_total else 0


def earliest_start(runs: Sequence[RunFacts]) -> datetime | None:
    """The earliest start of the runs of the sums, or None when no run is in them. It is not the sum of the times of the runs."""
    starts = [run.started for run in counted(runs)]
    return min(starts) if starts else None


def in_trouble(runs: Sequence[RunFacts], idle_limit: int) -> int:
    """The runs in trouble: a run with an agent idle for `idle_limit` seconds or more, a stopped run, a halted run, and a run that failed at start.

    A paused run is not in trouble. A run that ended is not, unless it failed at start.
    """
    return sum(
        1 for run in runs
        if run.failed_start or (not run.ended and (run.state in TROUBLE_STATES or (run.idle_seconds is not None and run.idle_seconds >= idle_limit)))
    )


def reachable(runs: Sequence[RunFacts]) -> tuple[int, int]:
    """The runs that are reachable, and the runs that are listed with no `run-end`. Each run of this machine is reachable, so the two are the same."""
    count = len(counted(runs))
    return count, count
