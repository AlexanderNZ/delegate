"""The runs of this machine: the use case of `delegate runs` (#307).

The command reads each state directory under the state root of the machine (`runs.machine_states`), so it shows each
run of each repository in one table. The spec is `design/docs/components.md`, the table C25 (frames W8 and E15–E18).
This slice has the table of one host and the columns `host repo run state ended ETA slip new cache trend alert do`, and under it the legend (C27, #309). Above the table are the
two fleet lines (C24, #308): the sums of the runs with no `run-end`, from the rules of `fleet_rules`. The column `trend` and the end of the second
line are the sparklines of the tickets that ended in each hour of the window (#310).

With `timeline` (`delegate runs --timeline`, #314), `fleet_lines` has the same two fleet lines and then one lane for each run instead of the table (C26, frame W9): the
life of the run, the tickets that ended built, its end or its projection to the ETA, and the column of now, over the window and on to the latest ETA (`fleet_rules.lane`).

A run is listed when it has no `run-end` (running, pausing, paused, halted or stopped), or when its `run-end` is
in the window, the last `WINDOW_SECONDS`. A run that failed at start (`runs.FailedStart`) and whose log last changed
in the window is listed with the state `failed`. An older run that ended is not listed.

The table fits the terminal (C25, #312): `fleet_lines` drops the columns of `FIT_STEPS` until the table fits the width, and folds the
rows of the ended runs, then a host with no alert and no command, until the lines fit the height.

`fleet_of` reads the journals and gives the `Fleet`. With the same `JournalReads` at each call, as `delegate runs --watch` does (#313), a call reads
each journal only from the position where the call before stopped, so a redraw does not read each journal from the start. `Fleet.to_json` is the contract of `delegate runs --json`, and
`fleet_lines` is the table. A journal that cannot be read is skipped, and `Fleet.unreadable` names it with the reason,
as `delegate status` does.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby
from datetime import datetime, timedelta
from pathlib import Path

from . import domain, fleet_rules, output
from .journal import ABANDONED, JournalError, newest_run_end, read_new_events
from .runs import RunsError, all_journals, failed_starts, machine_states
from .live import IDLE_WARNING_MINUTES
from .status import (
    BAR_CELLS, BAR_COLUMNS, BUILT, FAILED, HALTED, PAUSED, PAUSING, RUNNING, STOPPED, ReaderOf, format_duration, format_tokens,
    snapshot_at, snapshot_of,
)

# The window of the runs that ended: the last 6 hours.
WINDOW_SECONDS: int = 6 * 3600

# The stall limit of `delegate watch`, in seconds: an agent idle for this time or more is an alert.
IDLE_LIMIT_SECONDS: int = int(IDLE_WARNING_MINUTES * 60)

# The version of the contract of `--json`. A new field does not change it; a change to a field needs a new version.
FORMAT: int = 1

# The columns of the table, in order. `new` and `cache` are numbers and align right.
COLUMNS: tuple[str, ...] = ("host", "repo", "run", "state", "ended", "ETA", "slip", "new", "cache", "trend", "alert", "do")
NUMBERS: frozenset[int] = frozenset({7, 8})

# The steps of the fit to the width (components.md, C25), in order: the columns that drop at each step. The bar and the legend
# drop first, by `BAR_COLUMNS`. Then `new` and `cache` drop, then `trend`, then `slip`, then `repo`.
FIT_STEPS: tuple[frozenset[str], ...] = (
    frozenset(), frozenset({"new", "cache"}), frozenset({"new", "cache", "trend"}), frozenset({"new", "cache", "trend", "slip"}),
    frozenset({"new", "cache", "trend", "slip", "repo"}),
)

# The hosts in the line of a machine with no run (copy deck W8.empty): this machine is the one host, as no run index exists.
HOSTS: int = 1

# The command of each word of the column `do`, for the legend (copy deck W8.do.*). The placeholders stay as the copy deck writes them.
COMMANDS: dict[str, str] = {
    fleet_rules.WATCH: "delegate watch <run>", fleet_rules.RESUME: "delegate run --resume <run>",
    fleet_rules.RETRY: "delegate run --resume <run> --retry <ticket>", fleet_rules.MERGE: "git merge --ff-only <run branch>",
}

# The header of the columns after the lane in the timeline (copy deck W9.table.header).
W9_HEADER: tuple[str, ...] = ("ended", "ETA", "state", "alert")

# The state directory of a repository is `<name>-<key>`, with a key of 12 hex digits (`engine.state_directory`).
KEY = re.compile(r"-[0-9a-f]{12}$")

# The line over the table while no run index exists (copy deck W8.noIndex).
NO_INDEX: str = "no run index is set up: these are the runs of this machine"


@dataclass(frozen=True)
class RunRow:
    """One listed run: its id, its repository, its host, its state, and when it started and ended.

    `ended` is None for a run with no `run-end`. A run that failed at start has no `run-start`: its `started` and its
    `ended` are the time of the last change of its log, and it has 0 of 0 tickets. `eta` is set only while the run
    runs. The two token counts are None when the command has no reader for the streams of the run.

    The last four fields are for the fleet lines and the two last columns, and `to_json` leaves them out. `failed_start`
    is True for a run that failed at start. `idle_seconds` is the longest time that an agent of the run is idle, or None
    when no agent runs. `fixup` is the round and the limit of a fix-up round that runs now, or None. `trend` is the
    tickets that ended built in each hour of the window, oldest first (`fleet_rules.trend_cells`); `to_json` has it. `slip_seconds` is
    the slip of the ETA, or None when it does not show (`fleet_rules.slip_seconds`, #311); `to_json` has it too. `built` is the time of
    each ticket that ended built (`fleet_rules.built_times`) and `last_event` is the time of the newest event of the journal, for the lane of the
    timeline (#314); `to_json` leaves them out.
    """

    run_id: str
    repo: str
    host: str
    state: str
    started: datetime
    ended: datetime | None
    tickets_ended: int
    tickets_total: int
    eta: datetime | None
    tokens_new: int | None
    tokens_cache: int | None
    failed_start: bool = False
    idle_seconds: int | None = None
    fixup: tuple[int, int] | None = None
    trend: tuple[int, ...] = (0,) * fleet_rules.TREND_HOURS
    slip_seconds: int | None = None
    built: tuple[datetime, ...] = ()
    last_event: datetime | None = None

    def facts(self) -> fleet_rules.RunFacts:
        """The run as the rules of the fleet read it."""
        return fleet_rules.RunFacts(
            self.state, self.started, self.ended is not None, self.failed_start, self.tickets_ended, self.tickets_total, self.eta,
            self.tokens_new, self.tokens_cache, self.idle_seconds, self.fixup, self.trend,
        )

    def alert(self) -> fleet_rules.Alert | None:
        """The one alert of the run (`fleet_rules.alert`), with the stall limit of `delegate watch`."""
        return fleet_rules.alert(self.facts(), IDLE_LIMIT_SECONDS)

    def do(self) -> str | None:
        """The word of the column `do` (`fleet_rules.next_command`)."""
        return fleet_rules.next_command(self.facts(), self.alert() is not None)

    def to_json(self) -> dict[str, object]:
        found = self.alert()
        return {
            "run_id": self.run_id, "repo": self.repo, "host": self.host, "state": self.state,
            "started": self.started.isoformat(), "ended": self.ended.isoformat() if self.ended else None,
            "tickets_ended": self.tickets_ended, "tickets_total": self.tickets_total,
            "eta": self.eta.isoformat() if self.eta else None, "tokens_new": self.tokens_new, "tokens_cache": self.tokens_cache,
            "alert": None if found is None else alert_text(found, output.UNICODE.idle), "do": self.do(), "trend": list(self.trend),
            "slip_seconds": self.slip_seconds,
        }


@dataclass(frozen=True)
class Fleet:
    """The runs of this machine: its name, the time of the command, the listed runs in the order of the table, and each
    journal that the listing skipped with the reason."""

    host: str
    time: datetime
    runs: tuple[RunRow, ...]
    unreadable: tuple[tuple[Path, str], ...] = ()

    def unreadable_lines(self, command: str) -> list[str]:
        """One line for each journal that the listing skipped, for stderr: the command, the journal and the reason."""
        return [f"{command}: skipped the journal {path}: {reason}" for path, reason in self.unreadable]

    def to_json(self) -> dict[str, object]:
        """The object of `delegate runs --json`. The reference page of the command documents it: a new field comes last."""
        return {
            "format": FORMAT, "host": self.host, "time": self.time.isoformat(), "window_seconds": WINDOW_SECONDS,
            "runs": [row.to_json() for row in self.runs],
        }


def repository_name(state: Path) -> str:
    """The name of the repository of a state directory: its `<name>`, the directory name without the `-<key>` at its end."""
    return KEY.sub("", state.name)


def _time(event: dict[str, object]) -> datetime:
    return datetime.fromisoformat(str(event["time"]))


def _eta_then(events: list[dict[str, object]], journal: Path, now: datetime) -> datetime | None:
    """The ETA that the run had `fleet_rules.SLIP_WINDOW_SECONDS` before `now` (#311), worked out from its journal.

    The engine records no ETA. The snapshot of that time is the events up to it, that time, a live process and no pause
    request, with no reader of the streams: the ETA comes from the events alone. None when the run had not started then.
    """
    then = now - timedelta(seconds=fleet_rules.SLIP_WINDOW_SECONDS)
    until = [event for event in events if _time(event) <= then]
    if not until:
        return None
    return snapshot_at(until, journal, lambda adapter: None, then, live_process=True, pause_request=False).eta


def _in_window(ended: datetime, now: datetime) -> bool:
    return (now - ended).total_seconds() <= WINDOW_SECONDS


class JournalReads:
    """The events that `fleet_of` read from each journal so far, and the byte position after the last of them (#313).

    A new call of `events` for a journal reads only the bytes after that position, and adds the new events to the old ones. A journal
    that cannot be read keeps its old position, so the next call reads from it again, not from the start.
    """

    def __init__(self) -> None:
        self._seen: dict[Path, tuple[list[dict[str, object]], int]] = {}

    def events(self, journal: Path) -> list[dict[str, object]]:
        """All the events of the journal. Raise JournalError as `journal.read_new_events` does."""
        events, offset = self._seen.get(journal, ([], 0))
        new, offset = read_new_events(journal, offset, len(events) + 1)
        events.extend(new)
        self._seen[journal] = (events, offset)
        return events

    def keep_only(self, journals: set[Path]) -> None:
        """Forget each journal that is not in `journals`: a run that was cleaned away."""
        for journal in set(self._seen) - journals:
            del self._seen[journal]


def fleet_of(root: Path, host: str, now: datetime, reader_of: ReaderOf, reads: JournalReads | None = None) -> Fleet:
    """The runs of the machine under the state root `root`. `host` is the name of the machine, and `now` is the time of the clock, in UTC.

    `reads` holds what an earlier call read (`JournalReads`); with None, each journal is read from the start.

    The order is the order of the table: the runs with no `run-end`, newest start first, then the runs that ended,
    newest end first. Raise RunsError when a lock file cannot be read.
    """
    reads = JournalReads() if reads is None else reads
    rows: list[RunRow] = []
    unreadable: list[tuple[Path, str]] = []
    listed: set[Path] = set()
    for state in machine_states(root):
        repo = repository_name(state)
        for journal in all_journals([state]):
            listed.add(journal)
            try:
                events = reads.events(journal)
                end = newest_run_end(events)
                if end is not None and not _in_window(_time(end), now):
                    continue  # an older run that ended is not listed, and its streams are not read
                snap = snapshot_of(events, journal, reader_of, now)
                eta = snap.eta if snap.state in (RUNNING, PAUSING) else None
                slip = fleet_rules.slip_seconds(eta, None if eta is None else _eta_then(events, journal, now), _time(events[0]), now)
            except (JournalError, RunsError) as error:
                unreadable.append((journal, str(error)))
                continue
            built_times = fleet_rules.built_times(events)
            idle = [ticket.idle_seconds for ticket in snap.tickets if ticket.idle_seconds is not None]
            rounds = [t.fixup_round for t in snap.tickets if t.phase == domain.PHASE_FIXUP] + [
                c.fixup_round for c in snap.chains if c.phase == domain.PHASE_FIXUP
            ]
            fixup = (max(rounds), domain.fixup_round_limit(snap.mode)) if rounds else None
            rows.append(RunRow(
                snap.run_id, repo, host, snap.state, _time(events[0]), None if end is None else _time(end), snap.tickets_ended,
                snap.tickets_total, eta, snap.tokens_new, snap.tokens_cache,
                idle_seconds=max(idle) if idle else None, fixup=fixup,
                trend=fleet_rules.trend_cells(built_times, now), slip_seconds=slip, built=tuple(built_times), last_event=_time(events[-1]),
            ))
        for start in failed_starts([state]):
            if _in_window(start.changed, now):
                rows.append(RunRow(start.run_id, repo, host, FAILED, start.changed, start.changed, 0, 0, None, None, None, failed_start=True))
    reads.keep_only(listed)
    working = sorted((row for row in rows if row.ended is None), key=lambda row: row.started, reverse=True)
    ended = sorted((row for row in rows if row.ended is not None), key=lambda row: row.ended or row.started, reverse=True)
    return Fleet(host, now, tuple(working + ended), tuple(unreadable))


# The role of the state word in the table (components.md, C25). A state that has no entry has no role.
STATE_ROLES: dict[str, str] = {
    RUNNING: "now bold", PAUSING: "warn", PAUSED: "warn", STOPPED: "bad", HALTED: "bad bold", BUILT: "ok bold", FAILED: "bad bold",
    ABANDONED: "dim",
}


def alert_text(found: fleet_rules.Alert, idle_glyph: str) -> str:
    """The words of an alert (copy deck W8.alert.idle and W8.alert.fixup): `⚠ idle 6m40s`, or `fix-up 1/2`."""
    if found.kind == fleet_rules.IDLE:
        return f"{idle_glyph} idle {format_duration(found.seconds)}"
    return f"{found.kind} {found.round}/{found.limit}"


def _clock(eta: datetime) -> str:
    """The local clock time of an ETA, `HH:MM`."""
    return eta.astimezone().strftime("%H:%M")


def slip_text(seconds: int) -> str:
    """The slip as the copy deck writes it (W8.slip): `+55m`, whole minutes, or `+1h05m` from an hour."""
    minutes = seconds // 60
    return "+" + (f"{minutes}m" if minutes < 60 else format_duration(minutes * 60))


def sparkline(cells: Sequence[int], largest: int, glyph: output.Glyphs) -> str:
    """The sparkline of the cells (tokens.md): one glyph for each cell, scaled to `largest`, and a space for a cell with no ticket."""
    return "".join(" " if not level else glyph.spark[level - 1] for level in (fleet_rules.spark_level(cell, largest) for cell in cells))


def _cells(row: RunRow, glyph: output.Glyphs, largest: int) -> list[str]:
    """The cells of the row of one run, in the order of `COLUMNS`. A cell with no value is empty.

    The ETA is a clock time, or `-` while the run runs and has no estimate; it is empty for a run that is paused,
    halted, stopped or ended. The ended count is dim for a run that ended. `trend` is dim, on the scale `largest`. `alert` is in `warn`, and `do` is plain.
    """
    eta = "" if row.state not in (RUNNING, PAUSING) else _clock(row.eta) if row.eta else "-"
    slip = "" if row.slip_seconds is None else output.styled("warn", slip_text(row.slip_seconds))
    counted = f"{row.tickets_ended}/{row.tickets_total}"
    found = row.alert()
    return [
        output.styled("plan", glyph.rail), row.repo, output.styled("bold", output.short_run_id(row.run_id)),
        output.styled(STATE_ROLES.get(row.state, ""), row.state), output.styled("dim", counted) if row.ended else counted, eta, slip,
        "" if row.tokens_new is None else output.styled("dim", format_tokens(row.tokens_new)),
        "" if row.tokens_cache is None else output.styled("dim", format_tokens(row.tokens_cache)),
        output.styled("dim", sparkline(row.trend, largest, glyph)),
        "" if found is None else output.styled("warn", alert_text(found, glyph.idle)), row.do() or "",
    ]


def _totals(fleet: Fleet) -> list[str]:
    """The cells of the first row of the host group: its name, and the totals of its runs with no `run-end` (`fleet_rules.totals`).

    The totals are the ended count summed, the latest ETA, and the tokens summed. A run that ended is out of every total.
    """
    total = fleet_rules.totals([row.facts() for row in fleet.runs])
    has_runs = any(row.ended is None for row in fleet.runs)
    return [
        fleet.host, "", "", "", f"{total.tickets_ended}/{total.tickets_total}" if has_runs else "", _clock(total.eta) if total.eta else "", "",
        "" if total.tokens_new is None else output.styled("dim", format_tokens(total.tokens_new)),
        "" if total.tokens_cache is None else output.styled("dim", format_tokens(total.tokens_cache)), "", "", "",
    ]


def window_label() -> str:
    """The window in hours, as the copy deck writes it: `6h`."""
    return f"{WINDOW_SECONDS // 3600}h"


def _fleet_line(fleet: Fleet, width: int, glyph: output.Glyphs) -> str:
    """The first fleet line (C24): the runs that are reachable, the bar, the percentage, the tickets that ended, the latest ETA, and the tokens.

    The bar is the bar of the run line of `delegate watch`: 16 cells, and only on a terminal of `BAR_COLUMNS` or more. The
    ETA is `-` when no run has one, and the runs with no ETA are counted after it, when there is one. The tokens are
    two sums, `new` and `cache`, and no cost. They drop when the line is wider than `width` (#312), as the columns
    `new` and `cache` of the table do.
    """
    runs = [row.facts() for row in fleet.runs]
    total = fleet_rules.totals(runs)
    count, listed = fleet_rules.reachable(runs)
    head = f"runs  {count} of {listed} reachable"
    if not count:
        return head
    percent = fleet_rules.percent(total)
    progress = f"{percent}%"
    if width >= BAR_COLUMNS:
        filled = percent * BAR_CELLS // 100
        progress = output.styled("ok", glyph.fill * filled) + output.styled("dim", glyph.empty * (BAR_CELLS - filled)) + " " + progress
    dot = f" {glyph.pending} "
    eta = f"ETA {_clock(total.eta) if total.eta else '-'}" + (f"{dot}{total.without_eta} with no ETA" if total.without_eta else "")
    parts = [head, progress, f"{total.tickets_ended}/{total.tickets_total} ended", eta]
    line = output.GAP.join(parts)
    if total.tokens_new is not None and total.tokens_cache is not None:
        tokens = output.styled("dim", f"{format_tokens(total.tokens_new)} new{dot}{format_tokens(total.tokens_cache)} cache")
        if output.width(line + output.GAP + tokens) <= width:
            line += output.GAP + tokens
    return line


def _span_line(fleet: Fleet, width: int, glyph: output.Glyphs, timeline: bool = False) -> str | None:
    """The second fleet line (C24): the earliest start of the runs of the sums to now, and the runs in trouble. None with no run in the sums.

    The line ends with the trend (#310): the words `trend: tickets ended an hour, last 6h`, the sparkline of all the runs of the
    sums on a scale of its own largest cell, and the trend word when there is one. The limit of idle time is the stall limit of `delegate watch`.
    When the line is wider than `width`, the words and the sparkline drop and the trend word stays (#312), as the column `trend` of the table drops.

    With `timeline` (#314), the line has no sparkline: it ends with the trend word and the legend of the lanes (`_legend`).
    """
    runs = [row.facts() for row in fleet.runs]
    start = fleet_rules.earliest_start(runs)
    if start is None:
        return None
    parts = [output.styled("dim", f"{_clock(start)}{glyph.dash}{_clock(fleet.time)}")]
    trouble = fleet_rules.in_trouble(runs, IDLE_LIMIT_SECONDS)
    if trouble:
        parts.append(output.styled("warn", f"{glyph.idle} {trouble} in trouble"))
    cells = fleet_rules.trend_sum(runs)
    word = fleet_rules.trend_word(cells)
    if timeline:
        return output.GAP.join([*parts, *([output.styled("dim", word)] if word else []), output.styled("dim", _legend(width, glyph))])
    trend = f"trend: tickets ended an hour, last {window_label()} {glyph.pending} all {sparkline(cells, max(cells), glyph)}" + (f" {word}" if word else "")
    line = output.GAP.join([*parts, output.styled("dim", trend.rstrip(" "))])
    if output.width(line) > width:
        line = output.GAP.join([*parts, *([output.styled("dim", word)] if word else [])])
    return line


def _legend(width: int, glyph: output.Glyphs) -> str:
    """The legend of the lanes (copy deck W9.fleet.legend): what a ticket and the projection look like, and the minutes of one cell.

    The glyphs drop under `BAR_COLUMNS`, as the legend of the table does (C27).
    """
    cell = f"one cell is {fleet_rules.cell_minutes(width)}m"
    dot = f" {glyph.pending} "
    return f"{glyph.node} a ticket ended{dot}{glyph.empty} to the ETA{dot}{cell}" if width >= BAR_COLUMNS else cell


def legend_lines(runs: Sequence[RunRow]) -> list[str]:
    """The legend (C27): one line for each word of the column `do` that the rows hold, once, with its command (copy deck W8.do.*)."""
    present = {row.do() for row in runs}
    return [line.rstrip(" ") for line in output.columns([[word, COMMANDS[word]] for word in fleet_rules.DO_WORDS if word in present])]


@dataclass(frozen=True)
class _Ended:
    """The one row that holds the runs that ended, when the lines do not fit the height (C25): `✓ 1 built`, `✗ 1 failed`."""

    runs: tuple[RunRow, ...]

    def text(self, glyph: output.Glyphs) -> str:
        """The glyph of the first state and the count of each state, as the fold row of `delegate watch` writes them: `✓ 1 built, 1 failed`.

        `built` comes first, then `failed`, then each other state by name. The glyph is `ok` for built and `bad` for failed.
        """
        counts = Counter(row.state for row in self.runs)
        order = [state for state in (BUILT, FAILED) if counts[state]] + sorted(state for state in counts if state not in (BUILT, FAILED))
        role, mark = {BUILT: ("ok", glyph.built), FAILED: ("bad", glyph.failed)}.get(order[0], ("dim", glyph.skipped))
        return output.styled(role, mark) + output.styled("dim", " " + ", ".join(f"{counts[state]} {state}" for state in order))


def _candidates(fleet: Fleet) -> list[list[RunRow | _Ended]]:
    """The rows of the table for each step of the fit to the height (C25), from the most to the fewest.

    First each run has its row. Then the runs that ended fold into one row. Then, when the runs that remain have no alert and
    no command, the host folds to its totals row: no run row.
    """
    working = [row for row in fleet.runs if row.ended is None]
    ended = [row for row in fleet.runs if row.ended is not None]
    folded: list[RunRow | _Ended] = [*working, *([_Ended(tuple(ended))] if ended else [])]
    quiet = bool(working) and not any(row.alert() or row.do() for row in working)
    return [[*working, *ended], folded, [] if quiet else folded]


def _table(fleet: Fleet, items: Sequence[RunRow | _Ended], dropped: frozenset[str], glyph: output.Glyphs, largest: int) -> tuple[list[str], int]:
    """The lines of the table without the columns of `dropped`, and the width of the widest line that is not the row of the ended runs.

    The row of the ended runs has its text from the column `state`, over the cells that are blank on it.
    """
    keep = [index for index, name in enumerate(COLUMNS) if name not in dropped]
    right = {place for place, index in enumerate(keep) if index in NUMBERS}
    rows = [[output.styled("dim", COLUMNS[index]) for index in keep], [_totals(fleet)[index] for index in keep]]
    rail = output.styled("plan", glyph.rail)
    rows += [[_cells(item, glyph, largest)[index] for index in keep] if isinstance(item, RunRow) else [rail, *[""] * (len(keep) - 1)] for item in items]
    lines = [line.rstrip(" ") for line in output.columns(rows, right=right)]
    widths = [max(output.width(row[place]) for row in rows) for place in range(len(keep))]
    state = keep.index(COLUMNS.index("state"))
    start = sum(widths[:state]) + len(output.GAP) * state
    measured = 0
    for place, item in enumerate(items, start=2):
        if isinstance(item, _Ended):
            lines[place] = rail + " " * (start - output.width(rail)) + item.text(glyph)
        else:
            measured = max(measured, output.width(lines[place]))
    return lines, max(measured, *(output.width(line) for line in lines[:2]))


# The role and the glyph of each kind of cell of a lane (components.md C26). The life of the run has no role.
def _lane_marks(glyph: output.Glyphs) -> dict[str, tuple[str, str]]:
    return {
        fleet_rules.LIFE: ("", glyph.rule), fleet_rules.TICKET: ("ok", glyph.node), fleet_rules.DONE: ("ok", glyph.built),
        fleet_rules.FAIL: ("bad", glyph.failed), fleet_rules.NOW: ("now", glyph.now), fleet_rules.PROJECTION: ("dim", glyph.empty),
    }


def _lane_text(row: RunRow, fleet: Fleet, minutes: int, length: int, glyph: output.Glyphs) -> str:
    """The lane of one run, `length` cells wide (C26, #314): see `fleet_rules.lane`.

    A run that works (`fleet_rules.WORKING_STATES`) shows `▶` in the column of now, in `warn` when it has an alert, else in `now`. A run that
    ended ends with `✓` when it is built and `✗` when it is not. A stopped or halted run ends with `✗` at its newest event, and a
    paused run ends at its newest event with no mark. A run that failed at start is one `✗` in the cell of its log.
    """
    running = row.ended is None and row.state in fleet_rules.WORKING_STATES
    finish = row.ended or row.last_event
    outcome = None
    if row.ended is not None:
        outcome = fleet_rules.DONE if row.state == BUILT else fleet_rules.FAIL
    elif row.state in fleet_rules.TROUBLE_STATES:
        outcome = fleet_rules.FAIL
    kinds = fleet_rules.lane(
        now=fleet.time, minutes=minutes, length=length, started=row.started, tickets=row.built, finish=finish, outcome=outcome,
        running=running, eta=row.eta,
    )
    marks = _lane_marks(glyph)
    marks[fleet_rules.RUNNING] = ("warn" if row.alert() else "now", glyph.running)
    marks[""] = ("", " ")
    return "".join(output.styled(marks[kind][0], marks[kind][1] * len(list(group))) for kind, group in groupby(kinds))


def _timeline(fleet: Fleet, width: int, glyph: output.Glyphs) -> list[str]:
    """The lines under the fleet lines of the timeline (C26, #314): the header with the axis label, a blank line, and one lane for each run.

    A line is the label of the run (`host repo run`, with the host on the first row and the rail under it), the lane, and
    `ended ETA state alert` as in the table. The lane is as long as the window, the column of now, and the projection to the latest ETA of the runs.
    """
    minutes = fleet_rules.cell_minutes(width)
    latest = fleet_rules.totals([row.facts() for row in fleet.runs]).eta
    length = fleet_rules.window_cells(minutes) + 1 + fleet_rules.projection_cells(latest, fleet.time, minutes)
    rail = output.styled("plan", glyph.rail)
    cells = [_cells(row, glyph, 0) for row in fleet.runs]
    labels = output.columns([[fleet.host if index == 0 else rail, found[1], found[2]] for index, found in enumerate(cells)])
    label_width = max((output.width(label) for label in labels), default=0)
    body = output.columns([
        [output.styled("dim", name) for name in ("", *W9_HEADER)],
        *[[_lane_text(row, fleet, minutes, length, glyph), found[4], found[5], found[3], found[10]] for row, found in zip(fleet.runs, cells)],
    ])
    axis = output.styled("dim", f"last {window_label()}, to the latest ETA")
    lane_end = label_width + len(output.GAP) + length
    right = body[0][length:]  # the header row from the gap before `ended`
    header = axis + " " * (lane_end - output.width(axis)) + right
    return [header, "", *[label + " " * (label_width - output.width(label)) + output.GAP + line for label, line in zip(labels, body[1:])]]


def fleet_lines(fleet: Fleet, width: int = BAR_COLUMNS, height: int | None = None, timeline: bool = False) -> list[str]:
    """The lines of `delegate runs`: the line of the missing index, the two fleet lines, a blank line, the table, and the legend.

    `width` and `height` are the size of the terminal. The bar of the first fleet line and the legend need `BAR_COLUMNS`. When a
    line of the table is wider than `width`, the columns drop in the order of `FIT_STEPS`. Each line is cut to `width`. When the
    lines are more than the lines of `height`, less one for the prompt, the rows fold (`_candidates`). With no `height`, no row folds.
    The table has the header, the row of the host with its totals, and one row for each listed run, with the rail under the name
    of the host. The legend has a blank line over it, and no line when the table has no command. With no listed run, there are no
    fleet lines, and the rest is the two lines of an empty machine (copy deck W8.empty and W8.empty.ended).

    With `timeline` (#314), the table and the legend are the lanes of `_timeline`; the empty machine has the same lines, and no row folds.
    """
    if not fleet.runs:
        return [output.cut(line, max(width, 1)) for line in (
            NO_INDEX, f"runs  0 of {HOSTS} hosts have an active run", f"no run ended in the last {window_label()}",
        )]
    glyph = output.glyphs()
    head = [line for line in (NO_INDEX, _fleet_line(fleet, width, glyph), _span_line(fleet, width, glyph, timeline)) if line is not None]
    if timeline:
        return [output.cut(line.rstrip(" "), max(width, 1)) for line in [*head, "", *_timeline(fleet, width, glyph)]]
    largest = max((cell for row in fleet.runs for cell in row.trend), default=0)
    candidates = _candidates(fleet)
    for items in candidates:
        legend = legend_lines([item for item in items if isinstance(item, RunRow)]) if width >= BAR_COLUMNS else []
        if height is None or len(head) + 1 + 2 + len(items) + (1 + len(legend) if legend else 0) <= height - 1:
            break
    for dropped in FIT_STEPS:
        table, measured = _table(fleet, items, dropped, glyph, largest)
        if measured <= width:
            break
    return [output.cut(line, max(width, 1)) for line in [*head, "", *table, *([""] if legend else []), *legend]]
