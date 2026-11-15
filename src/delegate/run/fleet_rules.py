"""The rules of the fleet lines and of the totals of a host in `delegate runs` (#308): pure functions of plain values.

The spec is `design/docs/components.md`, C24 and C25. This module imports nothing from the package and does no I/O
(`tests/test_architecture.py` checks it), so a rule never reads a journal, a clock or a terminal. The use case
(`fleet.py`) turns each listed run into a `RunFacts`, calls the rules, and writes the words and the glyphs.

The trend rules (#310) are here too: the cells of a run, the level of a cell, and the word of the trend.

The runs of the sums are the runs that have no `run-end`. A run that ended, a run that failed at start, and a run of an
unreachable host (#317) are out of each sum. The sums are over the runs that the caller gives, so a host total gives the runs of one host, and the fleet
line gives the runs of the machine.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

# The window of the trend, in hours, and so the cells of a trend: one cell for each hour, oldest first.
TREND_HOURS: int = 6

# The levels of a sparkline glyph: a cell with a ticket has a level from 1 to this number. A cell with no ticket has level 0.
SPARK_LEVELS: int = 7

# The trend word compares the tickets of the last `TREND_SPAN` cells with the tickets of the `TREND_SPAN` cells before them.
TREND_SPAN: int = 2

# More than this many times as many tickets is `speeding up`, and fewer than this many times is `slowing` (copy deck W8.fleet.trend).
SPEEDING_RATIO: float = 1.25
SLOWING_RATIO: float = 0.75
SPEEDING_UP: str = "speeding up"
SLOWING: str = "slowing"
STEADY: str = "steady"

# The window of the runs that ended: the last 6 hours. A run with a `run-end` before the window is not listed (#307).
WINDOW_SECONDS: int = 6 * 3600


def in_window(ended: datetime, now: datetime) -> bool:
    """True when a run that ended at `ended` is in the window that ends at `now`."""
    return (now - ended).total_seconds() <= WINDOW_SECONDS


# The slip of a run (#311) is how much later its ETA is now than it was `SLIP_WINDOW_SECONDS` ago (2 hours). It shows from
# `SLIP_LIMIT_SECONDS` (30 minutes, a provisional threshold of the design) and not before.
SLIP_WINDOW_SECONDS: int = 2 * 3600
SLIP_LIMIT_SECONDS: int = 30 * 60

# The states of a run that are trouble by themselves: a run that stopped with no `run-end`, and a run that halted.
TROUBLE_STATES: frozenset[str] = frozenset({"stopped", "halted"})

# The states of a run that has an agent at work: only such a run can have an alert (#309).
WORKING_STATES: frozenset[str] = frozenset({"running", "pausing"})

# The kinds of an alert, and the words of the column `do` (copy deck W8.do.*).
IDLE: str = "idle"
FIXUP: str = "fix-up"
WATCH: str = "watch"
RESUME: str = "resume"
RETRY: str = "retry"
MERGE: str = "merge"
CHECK_HOST: str = "check host"

# The kinds of an alert that say why a run is in trouble (#322). They show in the cell `alert`, and they are not in the document of `--json`.
STOPPED: str = "stopped"
HALTED: str = "halted"
FAILED_START: str = "failed at start"
TROUBLE_KINDS: frozenset[str] = frozenset({IDLE, STOPPED, HALTED, FAILED_START})
DO_WORDS: tuple[str, ...] = (WATCH, RESUME, RETRY, MERGE, CHECK_HOST)


@dataclass(frozen=True)
class RunFacts:
    """What the rules need to know of one listed run.

    `ended` is True for a run with a `run-end`, and for a run that failed at start (`failed_start`). `eta` is None for a
    run that has no estimate and for a run that does not run. `idle_seconds` is the longest time that an agent of the
    run is idle, or None when no agent runs or the run has no reader of its streams. The two token counts are None
    when the run has none. `fixup` is the round and the limit of a fix-up round of a ticket or of a chain that runs now (#309), or None.
    `trend` is the tickets that ended built in each hour of the window, oldest first (`trend_cells`, #310). `unreachable` is True
    for a run of a host that this machine cannot read (#317): its values are the last known, and it is in no sum.
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
    fixup: tuple[int, int] | None = None
    trend: tuple[int, ...] = (0,) * TREND_HOURS
    unreachable: bool = False


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
    """The runs of the sums: the runs with no `run-end` whose host is reachable."""
    return [run for run in runs if not run.ended and not run.unreachable]


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
    """The runs in trouble (#322): the runs whose cell `alert` (`shown_alert`) says `⚠ idle`, `stopped`, `halted` or `failed at start`.

    A paused run is not in trouble. A run that ended is not, unless it failed at start. A run of an unreachable host is not: its host is.
    """
    return sum(1 for run in runs if (found := shown_alert(run, idle_limit)) is not None and found.kind in TROUBLE_KINDS)


def reachable(runs: Sequence[RunFacts]) -> tuple[int, int]:
    """The runs that are reachable, and the runs that are listed with no `run-end` (#317). Each run of this machine is reachable; a run of an unreachable host is only in the second count."""
    return len(counted(runs)), sum(1 for run in runs if not run.ended)


@dataclass(frozen=True)
class Alert:
    """The one alert of a run: `IDLE` with the seconds that an agent is idle, or `FIXUP` with the round and the limit.

    `shown_alert` adds the kinds `STOPPED`, `HALTED` and `FAILED_START`, which have no number.
    """

    kind: str
    seconds: int = 0
    round: int = 0
    limit: int = 0


def alert(run: RunFacts, idle_limit: int) -> Alert | None:
    """The alert of a run, at most one (#309): an agent idle for `idle_limit` seconds or more, then a fix-up round that runs. None otherwise.

    Only a run that works (`WORKING_STATES`) has an alert. A paused, halted or stopped run, and a run that ended, has none:
    its command is `resume`, `retry` or `merge`, and `delegate watch` shows no live agent for it.
    """
    if run.ended or run.unreachable or run.state not in WORKING_STATES:
        return None
    if run.idle_seconds is not None and run.idle_seconds >= idle_limit:
        return Alert(IDLE, seconds=run.idle_seconds)
    if run.fixup is not None:
        return Alert(FIXUP, round=run.fixup[0], limit=run.fixup[1])
    return None


def shown_alert(run: RunFacts, idle_limit: int) -> Alert | None:
    """The alert that the cell `alert` of a run shows (#322): the alert of `alert`, or for a run in trouble the reason.

    A stopped run gives `STOPPED`, a halted run gives `HALTED`, and a run that failed at start gives `FAILED_START`. The document of `--json` and the
    word `do` use `alert`, which has none of these three, so a stopped run has `resume` and not `watch`. A run of an unreachable host has none.
    """
    if run.unreachable:
        return None
    if run.failed_start:
        return Alert(FAILED_START)
    if not run.ended and run.state in TROUBLE_STATES:
        return Alert(run.state)
    return alert(run, idle_limit)


def next_command(run: RunFacts, alerted: bool) -> str | None:
    """The word of the column `do` of a run (#309), or None when nothing is needed.

    `watch` for a run with an alert; `resume` for a run that is paused, stopped or halted; `retry` for a run that ended
    `failed`; `merge` for a run that ended `built`. A run that failed at start has no ticket to retry, so it has none. A run of an
    unreachable host has none: the command is on the row of its host (`CHECK_HOST`, #317).
    """
    if run.unreachable:
        return None
    if alerted:
        return WATCH
    if run.failed_start:
        return None
    if run.state in ("paused", "stopped", "halted") and not run.ended:
        return RESUME
    if run.state == "failed":
        return RETRY
    if run.state == "built":
        return MERGE
    return None


def slip_seconds(eta: datetime | None, eta_then: datetime | None, started: datetime, now: datetime) -> int | None:
    """The slip of a run (#311): the ETA now minus the ETA of `SLIP_WINDOW_SECONDS` ago, in whole seconds, or None when it does not show.

    `eta_then` is the ETA that the run had at `now` minus `SLIP_WINDOW_SECONDS`. There is no slip when one of the two ETAs is
    missing, when the run started less than `SLIP_WINDOW_SECONDS` ago, and when the slip is less than `SLIP_LIMIT_SECONDS`.
    """
    if eta is None or eta_then is None or (now - started).total_seconds() < SLIP_WINDOW_SECONDS:
        return None
    slip = int((eta - eta_then).total_seconds())
    return slip if slip >= SLIP_LIMIT_SECONDS else None


def built_times(events: Sequence[dict[str, object]]) -> list[datetime]:
    """The time of each ticket that ended built (#310): the time of its first `step-end` with the state `built`.

    A ticket counts once. A ticket that a `retry` revived has a later `step-end` built, and that one is not counted.
    """
    first: dict[object, datetime] = {}
    for event in events:
        if event.get("event") == "step-end" and event.get("state") == "built" and event.get("ticket") not in first:
            first[event.get("ticket")] = datetime.fromisoformat(str(event["time"]))
    return list(first.values())


def trend_cells(times: Sequence[datetime], now: datetime) -> tuple[int, ...]:
    """The trend of a run (#310): `TREND_HOURS` cells, oldest first, each the number of the tickets that ended in one hour.

    The last cell is the hour that ends at `now`. A ticket that ended before the window is in no cell. A ticket with a
    time after `now` is in the last cell.
    """
    cells = [0] * TREND_HOURS
    for time in times:
        hours_ago = max(0.0, (now - time).total_seconds() / 3600)
        if hours_ago < TREND_HOURS:
            cells[TREND_HOURS - 1 - int(hours_ago)] += 1
    return tuple(cells)


def trend_sum(runs: Sequence[RunFacts]) -> tuple[int, ...]:
    """The trend of all runs (#310): the sum of the cells of the runs with no `run-end`."""
    active = counted(runs)
    return tuple(sum(run.trend[index] for run in active) for index in range(TREND_HOURS))


def spark_level(count: int, largest: int) -> int:
    """The level of a cell of `count` tickets on a scale whose largest cell has `largest` tickets: 0 for no ticket, else 1 to `SPARK_LEVELS`.

    A cell with a ticket never has level 0, and the largest cell has the top level.
    """
    return 0 if count <= 0 or largest <= 0 else -(-min(count, largest) * SPARK_LEVELS // largest)


def trend_word(cells: Sequence[int]) -> str | None:
    """The trend word of the cells (copy deck W8.fleet.trend): the tickets of the last `TREND_SPAN` cells against those of the `TREND_SPAN` cells before.

    More than `SPEEDING_RATIO` times as many is `speeding up`. Fewer than `SLOWING_RATIO` times as many is `slowing`.
    Else `steady`. None when both counts are 0.
    """
    recent = sum(cells[len(cells) - TREND_SPAN:])
    before = sum(cells[len(cells) - 2 * TREND_SPAN:len(cells) - TREND_SPAN])
    if not recent and not before:
        return None
    if recent > SPEEDING_RATIO * before:
        return SPEEDING_UP
    if recent < SLOWING_RATIO * before:
        return SLOWING
    return STEADY


# The lanes of the timeline (components.md C26, #314). One cell is `CELL_MINUTES_WIDE` minutes on a terminal of `WIDE_COLUMNS` or more,
# and `CELL_MINUTES_NARROW` minutes on a narrower one. The window has `window_cells` cells, the cell after them is the column of
# now, and the cells after that are the projection to the latest ETA.
WIDE_COLUMNS: int = 120
CELL_MINUTES_WIDE: int = 10
CELL_MINUTES_NARROW: int = 12

# The kinds of a cell of a lane. A cell with no mark is the empty string.
LIFE: str = "life"
TICKET: str = "ticket"
DONE: str = "done"
FAIL: str = "fail"
NOW: str = "now"
RUNNING: str = "running"
PROJECTION: str = "projection"


def cell_minutes(width: int) -> int:
    """The minutes in one cell of a lane on a terminal of `width` columns: 10 from `WIDE_COLUMNS`, else 12."""
    return CELL_MINUTES_WIDE if width >= WIDE_COLUMNS else CELL_MINUTES_NARROW


def window_cells(minutes: int) -> int:
    """The cells of the window of `TREND_HOURS` hours when one cell is `minutes` minutes. The column of now has this index."""
    return TREND_HOURS * 60 // minutes


def cell_of(time: datetime, now: datetime, minutes: int) -> int:
    """The index of the cell that holds `time`: cell 0 starts at the start of the window, `now` minus `TREND_HOURS` hours.

    A time before the window is in cell 0, and a time at or after `now` is in the last cell of the window.
    """
    seconds = (time - (now - timedelta(hours=TREND_HOURS))).total_seconds()
    return min(max(int(seconds // (minutes * 60)), 0), window_cells(minutes) - 1)


def ticket_cells(times: Sequence[datetime], now: datetime, minutes: int) -> list[int]:
    """The tickets in each cell of the window when one cell is `minutes` minutes (#315): `window_cells(minutes)` counts, oldest cell first.

    A time is in the cell that holds it (`cell_of`).
    """
    cells = [0] * window_cells(minutes)
    for time in times:
        cells[cell_of(time, now, minutes)] += 1
    return cells


def projection_cells(eta: datetime | None, now: datetime, minutes: int) -> int:
    """The whole cells from `now` to `eta`; 0 with no ETA, and for an ETA that is not after `now`."""
    return 0 if eta is None else max(int((eta - now).total_seconds() // (minutes * 60)), 0)


def lane(
    *, now: datetime, minutes: int, length: int, started: datetime, tickets: Sequence[datetime], finish: datetime | None,
    outcome: str | None, running: bool, eta: datetime | None,
) -> list[str]:
    """The `length` cells of the lane of one run (C26), each a kind: `LIFE`, `TICKET`, `DONE`, `FAIL`, `NOW`, `RUNNING`, `PROJECTION`, or "".

    The life of the run goes from the cell of `started` to the cell of `finish`, or to the last cell of the window while the run
    runs (`running`). A ticket of `tickets`, the time of a ticket that ended built, is in its cell. `outcome` (`DONE`, `FAIL` or None) is the
    end of a run that ended or stopped, in the cell of `finish`. The column of now is `RUNNING` for a run that runs and `NOW` for the
    other runs. A run that runs and has an `eta` has the projection from now to it.
    """
    cells = [""] * length
    present = window_cells(minutes)
    first = cell_of(started, now, minutes)
    last = present - 1 if running or finish is None else max(cell_of(finish, now, minutes), first)
    for index in range(first, last + 1):
        cells[index] = LIFE
    for time in tickets:
        cells[cell_of(time, now, minutes)] = TICKET
    if outcome is not None:
        cells[last] = outcome
    cells[present] = RUNNING if running else NOW
    if running:
        for index in range(present + 1, min(present + 1 + projection_cells(eta, now, minutes), length)):
            cells[index] = PROJECTION
    return cells
