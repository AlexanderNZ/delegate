"""The runs of this machine: the use case of `delegate runs` (#307).

The command reads each state directory under the state root of the machine (`runs.machine_states`), so it shows each
run of each repository in one table. The spec is `design/docs/components.md`, the table C25 (frames W8 and E15–E18).
This slice has the table of one host and the columns `host repo run state ended ETA slip new cache trend alert do`, and under it the legend (C27, #309). Above the table are the
two fleet lines (C24, #308): the sums of the runs with no `run-end`, from the rules of `fleet_rules`. The column `trend` and the end of the second
line are the sparklines of the tickets that ended in each hour of the window (#310).

With `timeline` (`delegate runs --timeline`, #314), `fleet_lines` has the same two fleet lines and then one lane for each run instead of the table (C26, frame W9): the
life of the run, the tickets that ended built, its end or its projection to the ETA, and the column of now, over the window and on to the latest ETA (`fleet_rules.lane`).
The first lane is `all` (#315): the sparkline of the tickets that ended in each cell, over the runs with no `run-end`. Under `BAR_COLUMNS` the label is the run id, the host is a line over its lanes, and `ETA` and the footer drop.

A run is listed when it has no `run-end` (running, pausing, paused, halted or stopped), or when its `run-end` is
in the window, the last `WINDOW_SECONDS`. A run that failed at start (`runs.FailedStart`) and whose log last changed
in the window is listed with the state `failed`. An older run that ended is not listed.

The table fits the terminal (C25, #312): `fleet_lines` drops the columns of `FIT_STEPS` until the table fits the width, and folds the
rows of the ended runs, then a host with no alert and no command, until the lines fit the height.

`fleet_of` reads the journals and gives the `Fleet`. With the same `JournalReads` at each call, as `delegate runs --watch` does (#313), a call reads
each journal only from the position where the call before stopped, so a redraw does not read each journal from the start. `Fleet.to_json` is the contract of `delegate runs --json`, and
`fleet_lines` is the table. A journal that cannot be read is skipped, and `Fleet.unreadable` names it with the reason,
as `delegate status` does.

With a run index (#317, ADR 0008), `fleet_of` adds a group for each other host of the index, from the records of the index. The runs of this
machine come from its journals, and its records in the index are not used. Until a later ticket reads other hosts, each other host is
`unreachable` (C25): its row says so, with `heard <duration> ago` and the command `check host`, and its runs are the last-known values of their
records, dim, with no ETA, no tokens and no trend. A record whose run ended before the window is not listed. An unreachable run is in no sum of
the fleet lines, and the first line counts it in the total of `N of M reachable`. `--json` describes this machine only.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby
from datetime import datetime, timedelta
from pathlib import Path

from ..ports.index import RunIndex, RunIndexError, RunRecord
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

# The command of each word of the column `do`, for the legend (copy deck W8.do.*). The placeholders stay as the copy deck writes them.
COMMANDS: dict[str, str] = {
    fleet_rules.WATCH: "delegate watch <run>", fleet_rules.RESUME: "delegate run --resume <run>",
    fleet_rules.RETRY: "delegate run --resume <run> --retry <ticket>", fleet_rules.MERGE: "git merge --ff-only <run branch>",
    fleet_rules.CHECK_HOST: "ssh <host>; on another host, put ssh <host> in front of each command",
}

# The state word of a host that this machine cannot read (copy deck W8.state.unreachable).
UNREACHABLE: str = "unreachable"

# The states of a run that has no `run-end`. The state of a record of the index that is not one of them is the result of a `run-end`.
OPEN_STATES: frozenset[str] = frozenset({RUNNING, PAUSING, PAUSED, HALTED, STOPPED})

# The header of the columns after the lane in the timeline (copy deck W9.table.header).
W9_HEADER: tuple[str, ...] = ("ended", "ETA", "state", "alert")

# The label of the first lane of the timeline (copy deck W9.lane.all), and the line under the lanes (copy deck W9.footer). The placeholders stay as the copy deck writes them.
ALL: str = "all"
FOOTER: str = "detail  delegate watch <run>; on another host, ssh <host> delegate watch <run>"

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

    `unreachable` is True for a run of another host, from its record in the run index (#317): its values are the last known, and `heard` is the time
    of the write of the record. Such a run has no ETA, no tokens, no trend, no alert and no command, and `ended` is `heard` when its state is the
    result of a `run-end`.
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
    unreachable: bool = False
    heard: datetime | None = None

    def facts(self) -> fleet_rules.RunFacts:
        """The run as the rules of the fleet read it."""
        return fleet_rules.RunFacts(
            self.state, self.started, self.ended is not None, self.failed_start, self.tickets_ended, self.tickets_total, self.eta,
            self.tokens_new, self.tokens_cache, self.idle_seconds, self.fixup, self.trend,
            self.unreachable,
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
    """The runs of this machine and of the other hosts of the run index: the name of this machine, the time of the command, the listed runs
    in the order of the table, and each journal that the listing skipped with the reason.

    The runs of this machine come first, then the runs of each other host, in the order of the names of the hosts (`RunRow.unreachable`).
    `indexed` is True when a run index is set up, and then the line of the missing index does not show. `host_count` is this machine and
    each other host that the index has a record of, whether the record is listed or not. `index_problem` is the reason that the index
    could not be read, or None.
    """

    host: str
    time: datetime
    runs: tuple[RunRow, ...]
    unreadable: tuple[tuple[Path, str], ...] = ()
    indexed: bool = False
    host_count: int = 1
    index_problem: str | None = None

    def unreadable_lines(self, command: str) -> list[str]:
        """One line for each journal that the listing skipped, and one for an index that could not be read, for stderr."""
        lines = [f"{command}: skipped the journal {path}: {reason}" for path, reason in self.unreadable]
        return lines + ([f"{command}: {self.index_problem}"] if self.index_problem else [])

    def hosts(self) -> list[tuple[str, tuple[RunRow, ...]]]:
        """The host groups of the table: this machine first, then each other host with a listed run, with the rows of each."""
        return [(host, tuple(rows)) for host, rows in groupby(self.runs, key=lambda row: row.host)]

    def to_json(self) -> dict[str, object]:
        """The object of `delegate runs --json`. The reference page of the command documents it: a new field comes last."""
        return {
            "format": FORMAT, "host": self.host, "time": self.time.isoformat(), "window_seconds": WINDOW_SECONDS,
            "runs": [row.to_json() for row in self.runs if not row.unreachable],
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


def _remote(record: RunRecord, now: datetime) -> RunRow | None:
    """The row of the run that a record of the index describes, or None when the record is not listed (#317).

    The run is unreachable: its values are the last known. A run whose state is the result of a `run-end` ended when the record was written
    (`heard`), and it is not listed when that time is out of the window. A record with a time that cannot be read is not listed.
    """
    try:
        started, heard = datetime.fromisoformat(record.started), datetime.fromisoformat(record.heard)
    except ValueError:
        return None
    ended = record.state not in OPEN_STATES
    if ended and not _in_window(heard, now):
        return None
    return RunRow(
        record.run_id, record.repo, record.host, record.state, started, heard if ended else None, record.tickets_ended, record.tickets_total,
        None, None, None, last_event=heard, unreachable=True, heard=heard,
    )


def _others(index: RunIndex, host: str, now: datetime) -> tuple[list[RunRow], int]:
    """The rows of the runs of the other hosts of the index, and the number of those hosts. Raise RunIndexError when the index cannot be read.

    The records of `host`, this machine, are left out. The rows are in the order of the table within each host: the runs with no `run-end`,
    newest start first, then the runs that ended, newest end first. The hosts are in the order of their names.
    """
    records = [record for record in index.list_records() if record.host != host]
    rows = [row for row in (_remote(record, now) for record in records) if row is not None]
    ordered: list[RunRow] = []
    for name in sorted({row.host for row in rows}):
        own = [row for row in rows if row.host == name]
        ordered += sorted((row for row in own if row.ended is None), key=lambda row: row.started, reverse=True)
        ordered += sorted((row for row in own if row.ended is not None), key=lambda row: row.ended or row.started, reverse=True)
    return ordered, len({record.host for record in records})


def fleet_of(
    root: Path, host: str, now: datetime, reader_of: ReaderOf, reads: JournalReads | None = None, index: RunIndex | None = None,
    read_index: bool = True,
) -> Fleet:
    """The runs of the machine under the state root `root`. `host` is the name of the machine, and `now` is the time of the clock, in UTC.

    `reads` holds what an earlier call read (`JournalReads`); with None, each journal is read from the start. `index` is the run index, or
    None when none is set up; the runs of the other hosts of the index follow the runs of this machine (#317). With `read_index` False
    the index is not read (`delegate runs --local`), but it still counts as set up.

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
    others: list[RunRow] = []
    count, problem = 0, None
    if index is not None and read_index:
        try:
            others, count = _others(index, host, now)
        except RunIndexError as error:
            problem = str(error)
    return Fleet(host, now, tuple(working + ended + others), tuple(unreadable), index is not None, 1 + count, problem)


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


def _whole_minutes(seconds: int) -> str:
    """A duration in whole minutes, `55m`, or `1h05m` from an hour."""
    minutes = seconds // 60
    return f"{minutes}m" if minutes < 60 else format_duration(minutes * 60)


def slip_text(seconds: int) -> str:
    """The slip as the copy deck writes it (W8.slip): `+55m`, whole minutes, or `+1h05m` from an hour."""
    return "+" + _whole_minutes(seconds)


def heard_text(heard: datetime, now: datetime) -> str:
    """The alert of an unreachable host (copy deck W8.alert.heard): `heard 47m ago`, in whole minutes, or in seconds under a minute."""
    seconds = max(int((now - heard).total_seconds()), 0)
    return f"heard {format_duration(seconds) if seconds < 60 else _whole_minutes(seconds)} ago"


def sparkline(cells: Sequence[int], largest: int, glyph: output.Glyphs) -> str:
    """The sparkline of the cells (tokens.md): one glyph for each cell, scaled to `largest`, and a space for a cell with no ticket."""
    return "".join(" " if not level else glyph.spark[level - 1] for level in (fleet_rules.spark_level(cell, largest) for cell in cells))


def _cells(row: RunRow, glyph: output.Glyphs, largest: int) -> list[str]:
    """The cells of the row of one run, in the order of `COLUMNS`. A cell with no value is empty.

    The ETA is a clock time, or `-` while the run runs and has no estimate; it is empty for a run that is paused,
    halted, stopped or ended. The ended count is dim for a run that ended. `trend` is dim, on the scale `largest`. `alert` is in `warn`, and `do` is plain.
    """
    if row.unreachable:  # the last-known values, dim, and nothing that this machine cannot know (#317)
        return [
            output.styled("plan", glyph.rail), output.styled("dim", row.repo), output.styled("dim", output.short_run_id(row.run_id)),
            output.styled("dim", row.state), output.styled("dim", f"{row.tickets_ended}/{row.tickets_total}"), *[""] * 7,
        ]
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


def _totals(host: str, rows: Sequence[RunRow]) -> list[str]:
    """The cells of the first row of the host group: its name, and the totals of its runs with no `run-end` (`fleet_rules.totals`).

    The totals are the ended count summed, the latest ETA, and the tokens summed. A run that ended is out of every total.
    """
    total = fleet_rules.totals([row.facts() for row in rows])
    has_runs = any(row.ended is None for row in rows)
    return [
        host, "", "", "", f"{total.tickets_ended}/{total.tickets_total}" if has_runs else "", _clock(total.eta) if total.eta else "", "",
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


def legend_lines(runs: Sequence[RunRow], unreachable: bool = False) -> list[str]:
    """The legend (C27): one line for each word of the column `do` that the rows hold, once, with its command (copy deck W8.do.*).

    `unreachable` is True when the table has the row of an unreachable host: its word `check host` is in the legend (#317).
    """
    present = {row.do() for row in runs} | ({fleet_rules.CHECK_HOST} if unreachable else set())
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


@dataclass(frozen=True)
class _Section:
    """One host group of the table: its name, all its runs (for its row), and the items that show under its row."""

    host: str
    runs: tuple[RunRow, ...]
    items: tuple[RunRow | _Ended, ...]

    @property
    def unreachable(self) -> bool:
        return any(row.unreachable for row in self.runs)

    def cells(self, now: datetime) -> list[str]:
        """The cells of the row of the host: its totals (`_totals`), or for an unreachable host `unreachable`, `heard <duration> ago` and `check host` (C25, #317)."""
        if not self.unreachable:
            return _totals(self.host, self.runs)
        heard = max(row.heard for row in self.runs if row.heard is not None)
        cells = [self.host, "", "", output.styled("warn", UNREACHABLE), *[""] * 6, output.styled("warn", heard_text(heard, now)), fleet_rules.CHECK_HOST]
        return cells


def _candidates(fleet: Fleet) -> list[list[_Section]]:
    """The sections of the table for each step of the fit to the height (C25), from the most to the fewest rows.

    First each run has its row. Then the runs that ended fold into one row for each host. Then each host whose runs that remain have no alert
    and no command folds to its totals row: no run row. An unreachable host has an alert and a command on its row, so it does not fold.
    """
    full: list[_Section] = []
    folded: list[_Section] = []
    quiet: list[_Section] = []
    for host, rows in fleet.hosts():
        working = [row for row in rows if row.ended is None]
        ended = [row for row in rows if row.ended is not None]
        fold: tuple[RunRow | _Ended, ...] = (*working, *([_Ended(tuple(ended))] if ended else []))
        is_quiet = bool(working) and not any(row.unreachable or row.alert() or row.do() for row in working)
        full.append(_Section(host, rows, (*working, *ended)))
        folded.append(_Section(host, rows, fold))
        quiet.append(_Section(host, rows, () if is_quiet else fold))
    return [full, folded, quiet]


def _table(sections: Sequence[_Section], now: datetime, dropped: frozenset[str], glyph: output.Glyphs, largest: int) -> tuple[list[str], int]:
    """The lines of the table without the columns of `dropped`, and the width of the widest line that is not the row of the ended runs.

    The row of the ended runs has its text from the column `state`, over the cells that are blank on it. A blank line is between two host groups.
    """
    keep = [index for index, name in enumerate(COLUMNS) if name not in dropped]
    right = {place for place, index in enumerate(keep) if index in NUMBERS}
    rail = output.styled("plan", glyph.rail)
    rows = [[output.styled("dim", COLUMNS[index]) for index in keep]]
    folds: dict[int, _Ended] = {}
    for number, section in enumerate(sections):
        if number:
            rows.append([""] * len(keep))
        cells = section.cells(now)
        rows.append([cells[index] for index in keep])
        for item in section.items:
            if isinstance(item, RunRow):
                rows.append([_cells(item, glyph, largest)[index] for index in keep])
            else:
                folds[len(rows)] = item
                rows.append([rail, *[""] * (len(keep) - 1)])
    lines = [line.rstrip(" ") for line in output.columns(rows, right=right)]
    widths = [max(output.width(row[place]) for row in rows) for place in range(len(keep))]
    state = keep.index(COLUMNS.index("state"))
    start = sum(widths[:state]) + len(output.GAP) * state
    for place, item in folds.items():
        lines[place] = rail + " " * (start - output.width(rail)) + item.text(glyph)
    return lines, max(output.width(line) for place, line in enumerate(lines) if place not in folds)


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
    running = row.ended is None and row.state in fleet_rules.WORKING_STATES and not row.unreachable
    finish = row.ended or row.last_event
    outcome = None
    if row.unreachable:
        pass  # the last-known life of the run, up to the time that its host was heard from, and no mark of an end (#317)
    elif row.ended is not None:
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


def _all_lane(fleet: Fleet, minutes: int, projection: int, glyph: output.Glyphs) -> str:
    """The lane of `all` (C26, #315): the sparkline of the tickets that ended built in each cell of the window, over the runs with no `run-end`,
    on a scale whose largest cell has the top level; then the column of now, and `░` to the latest ETA (`projection` cells).
    """
    times = [time for row in fleet.runs if row.ended is None and not row.unreachable for time in row.built]
    cells = fleet_rules.ticket_cells(times, fleet.time, minutes)
    return (
        output.styled("dim", sparkline(cells, max(cells), glyph)) + output.styled("now", glyph.now)
        + output.styled("dim", glyph.empty * projection)
    )


def _timeline(fleet: Fleet, width: int, glyph: output.Glyphs) -> list[str]:
    """The lines under the fleet lines of the timeline (C26, #314, #315): the header, a blank line, the lane of `all`, one lane for each run, and the footer.

    A line is the label of the run (`host repo run`, with the host on the first row and the rail under it), the lane, and
    `ended ETA state alert` as in the table. The lane is as long as the window, the column of now, and the projection to the latest ETA of the runs.
    The first lane is `all` (`_all_lane`), with the ended count and the latest ETA of the runs with no `run-end`.

    Under `BAR_COLUMNS` the label is the run id only, the host is a line over its lanes, the column `ETA` and the axis label drop, and so does the footer
    (`W9.footer`), as the legend of the table does.
    """
    compact = width < BAR_COLUMNS
    minutes = fleet_rules.cell_minutes(width)
    latest = fleet_rules.totals([row.facts() for row in fleet.runs]).eta
    projection = fleet_rules.projection_cells(latest, fleet.time, minutes)
    length = fleet_rules.window_cells(minutes) + 1 + projection
    rail = output.styled("plan", glyph.rail)
    summary = _totals(fleet.host, fleet.runs)

    def tail(ended: str, eta: str, state: str, alert: str) -> list[str]:
        return [ended, state, alert] if compact else [ended, eta, state, alert]

    def cells_of(row: RunRow) -> list[str]:
        """The cells of the row, with `unreachable` and `heard <duration> ago` for the run of an unreachable host (C26, #317)."""
        found = _cells(row, glyph, 0)
        if row.unreachable and row.heard is not None:
            found[3], found[10] = output.styled("warn", UNREACHABLE), output.styled("warn", heard_text(row.heard, fleet.time))
        return found

    groups = [[(row, cells_of(row)) for row in rows] for _, rows in fleet.hosts()]
    blank = [""] * (1 if compact else 3)
    label_rows = [[ALL] if compact else [ALL, "", ""]]
    lane_rows = [[_all_lane(fleet, minutes, projection, glyph), *tail(summary[4], summary[5], "", "")]]
    for number, group in enumerate(groups):
        if number and not compact:
            label_rows.append(blank)
            lane_rows.append([""] * 5)
        for place, (row, found) in enumerate(group):
            label_rows.append([found[2]] if compact else [row.host if place == 0 else rail, found[1], found[2]])
            lane_rows.append([_lane_text(row, fleet, minutes, length, glyph), *tail(found[4], found[5], found[3], found[10])])
    labels = output.columns(label_rows)
    label_width = max(output.width(label) for label in labels)
    header = [name for name in W9_HEADER if not compact or name != "ETA"]
    body = output.columns([["", *[output.styled("dim", name) for name in header]], *lane_rows])
    lead = "" if compact else output.styled("dim", f"last {window_label()}, to the latest ETA")
    lane_end = label_width + len(output.GAP) + length
    top = lead + " " * (lane_end - output.width(lead)) + body[0][length:]  # the header row from the gap before `ended`
    lanes = [label + " " * (label_width - output.width(label)) + output.GAP + line for label, line in zip(labels, body[1:])]
    if compact:
        placed = [lanes[0]]
        taken = 1
        for number, group in enumerate(groups):
            placed += [*([""] if number else []), fleet.hosts()[number][0], *lanes[taken:taken + len(group)]]
            taken += len(group)
        return [top, "", *placed]
    return [top, "", *lanes, "", FOOTER]


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
    index_line = [] if fleet.indexed else [NO_INDEX]
    if not fleet.runs:
        return [output.cut(line, max(width, 1)) for line in (
            *index_line, f"runs  0 of {fleet.host_count} hosts have an active run", f"no run ended in the last {window_label()}",
        )]
    glyph = output.glyphs()
    head = [line for line in (*index_line, _fleet_line(fleet, width, glyph), _span_line(fleet, width, glyph, timeline)) if line is not None]
    if timeline:
        return [output.cut(line.rstrip(" "), max(width, 1)) for line in [*head, "", *_timeline(fleet, width, glyph)]]
    largest = max((cell for row in fleet.runs for cell in row.trend), default=0)
    candidates = _candidates(fleet)
    for sections in candidates:
        shown = [item for section in sections for item in section.items if isinstance(item, RunRow)]
        legend = legend_lines(shown, any(section.unreachable for section in sections)) if width >= BAR_COLUMNS else []
        rows = sum(1 + len(section.items) for section in sections) + len(sections) - 1  # a row for each host, and a blank line between two hosts
        if height is None or len(head) + 1 + 1 + rows + (1 + len(legend) if legend else 0) <= height - 1:
            break
    for dropped in FIT_STEPS:
        table, measured = _table(sections, fleet.time, dropped, glyph, largest)
        if measured <= width:
            break
    return [output.cut(line, max(width, 1)) for line in [*head, "", *table, *([""] if legend else []), *legend]]
