"""The runs of this machine: the use case of `delegate runs` (#307).

The command reads each state directory under the state root of the machine (`runs.machine_states`), so it shows each
run of each repository in one table. The spec is `design/docs/components.md`, the table C25 (frames W8 and E15–E18).
This slice has the table of one host and the columns `host repo run state ended ETA new cache`. Above the table are the
two fleet lines (C24, #308): the sums of the runs with no `run-end`, from the rules of `fleet_rules`.

A run is listed when it has no `run-end` (running, pausing, paused, halted or stopped), or when its `run-end` is
in the window, the last `WINDOW_SECONDS`. A run that failed at start (`runs.FailedStart`) and whose log last changed
in the window is listed with the state `failed`. An older run that ended is not listed.

`fleet_of` reads the journals and gives the `Fleet`. `Fleet.to_json` is the contract of `delegate runs --json`, and
`fleet_lines` is the table. A journal that cannot be read is skipped, and `Fleet.unreadable` names it with the reason,
as `delegate status` does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import fleet_rules, output
from .journal import ABANDONED, JournalError, newest_run_end, read_new_events
from .runs import RunsError, all_journals, failed_starts, machine_states
from .live import IDLE_WARNING_MINUTES
from .status import (
    BAR_CELLS, BAR_COLUMNS, BUILT, FAILED, HALTED, PAUSED, PAUSING, RUNNING, STOPPED, ReaderOf, format_tokens, snapshot_of,
)

# The window of the runs that ended: the last 6 hours.
WINDOW_SECONDS: int = 6 * 3600

# The version of the contract of `--json`. A new field does not change it; a change to a field needs a new version.
FORMAT: int = 1

# The columns of the table, in order. `new` and `cache` are numbers and align right. Later tickets add `slip`,
# `trend`, `alert` and `do`.
COLUMNS: tuple[str, ...] = ("host", "repo", "run", "state", "ended", "ETA", "new", "cache")
NUMBERS: frozenset[int] = frozenset({6, 7})

# The state directory of a repository is `<name>-<key>`, with a key of 12 hex digits (`engine.state_directory`).
KEY = re.compile(r"-[0-9a-f]{12}$")

# The line over the table while no run index exists (copy deck W8.noIndex), and the line of a table with no row (W8.empty.ended).
NO_INDEX: str = "no run index is set up: these are the runs of this machine"


@dataclass(frozen=True)
class RunRow:
    """One listed run: its id, its repository, its host, its state, and when it started and ended.

    `ended` is None for a run with no `run-end`. A run that failed at start has no `run-start`: its `started` and its
    `ended` are the time of the last change of its log, and it has 0 of 0 tickets. `eta` is set only while the run
    runs. The two token counts are None when the command has no reader for the streams of the run.

    The last two fields are for the fleet lines, and `to_json` leaves them out. `failed_start` is True for a run that
    failed at start. `idle_seconds` is the longest time that an agent of the run is idle, or None when no agent runs.
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

    def facts(self) -> fleet_rules.RunFacts:
        """The run as the rules of the fleet read it."""
        return fleet_rules.RunFacts(
            self.state, self.started, self.ended is not None, self.failed_start, self.tickets_ended, self.tickets_total, self.eta,
            self.tokens_new, self.tokens_cache, self.idle_seconds,
        )

    def to_json(self) -> dict[str, object]:
        return {
            "run_id": self.run_id, "repo": self.repo, "host": self.host, "state": self.state,
            "started": self.started.isoformat(), "ended": self.ended.isoformat() if self.ended else None,
            "tickets_ended": self.tickets_ended, "tickets_total": self.tickets_total,
            "eta": self.eta.isoformat() if self.eta else None, "tokens_new": self.tokens_new, "tokens_cache": self.tokens_cache,
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


def _in_window(ended: datetime, now: datetime) -> bool:
    return (now - ended).total_seconds() <= WINDOW_SECONDS


def fleet_of(root: Path, host: str, now: datetime, reader_of: ReaderOf) -> Fleet:
    """The runs of the machine under the state root `root`. `host` is the name of the machine, and `now` is the time of the clock, in UTC.

    The order is the order of the table: the runs with no `run-end`, newest start first, then the runs that ended,
    newest end first. Raise RunsError when a lock file cannot be read.
    """
    rows: list[RunRow] = []
    unreadable: list[tuple[Path, str]] = []
    for state in machine_states(root):
        repo = repository_name(state)
        for journal in all_journals([state]):
            try:
                events, _ = read_new_events(journal, 0, 1)
                end = newest_run_end(events)
                if end is not None and not _in_window(_time(end), now):
                    continue  # an older run that ended is not listed, and its streams are not read
                snap = snapshot_of(events, journal, reader_of, now)
            except (JournalError, RunsError) as error:
                unreadable.append((journal, str(error)))
                continue
            idle = [ticket.idle_seconds for ticket in snap.tickets if ticket.idle_seconds is not None]
            rows.append(RunRow(
                snap.run_id, repo, host, snap.state, _time(events[0]), None if end is None else _time(end), snap.tickets_ended,
                snap.tickets_total, snap.eta if snap.state in (RUNNING, PAUSING) else None, snap.tokens_new, snap.tokens_cache,
                idle_seconds=max(idle) if idle else None,
            ))
        for start in failed_starts([state]):
            if _in_window(start.changed, now):
                rows.append(RunRow(start.run_id, repo, host, FAILED, start.changed, start.changed, 0, 0, None, None, None, failed_start=True))
    working = sorted((row for row in rows if row.ended is None), key=lambda row: row.started, reverse=True)
    ended = sorted((row for row in rows if row.ended is not None), key=lambda row: row.ended or row.started, reverse=True)
    return Fleet(host, now, tuple(working + ended), tuple(unreadable))


# The role of the state word in the table (components.md, C25). A state that has no entry has no role.
STATE_ROLES: dict[str, str] = {
    RUNNING: "now bold", PAUSING: "warn", PAUSED: "warn", STOPPED: "bad", HALTED: "bad bold", BUILT: "ok bold", FAILED: "bad bold",
    ABANDONED: "dim",
}


def _clock(eta: datetime) -> str:
    """The local clock time of an ETA, `HH:MM`."""
    return eta.astimezone().strftime("%H:%M")


def _cells(row: RunRow, rail: str) -> list[str]:
    """The cells of the row of one run, in the order of `COLUMNS`. A cell with no value is empty.

    The ETA is a clock time, or `-` while the run runs and has no estimate; it is empty for a run that is paused,
    halted, stopped or ended. The ended count is dim for a run that ended.
    """
    eta = "" if row.state not in (RUNNING, PAUSING) else _clock(row.eta) if row.eta else "-"
    counted = f"{row.tickets_ended}/{row.tickets_total}"
    return [
        output.styled("plan", rail), row.repo, output.styled("bold", output.short_run_id(row.run_id)),
        output.styled(STATE_ROLES.get(row.state, ""), row.state), output.styled("dim", counted) if row.ended else counted, eta,
        "" if row.tokens_new is None else output.styled("dim", format_tokens(row.tokens_new)),
        "" if row.tokens_cache is None else output.styled("dim", format_tokens(row.tokens_cache)),
    ]


def _totals(fleet: Fleet) -> list[str]:
    """The cells of the first row of the host group: its name, and the totals of its runs with no `run-end` (`fleet_rules.totals`).

    The totals are the ended count summed, the latest ETA, and the tokens summed. A run that ended is out of every total.
    """
    total = fleet_rules.totals([row.facts() for row in fleet.runs])
    has_runs = any(row.ended is None for row in fleet.runs)
    return [
        fleet.host, "", "", "", f"{total.tickets_ended}/{total.tickets_total}" if has_runs else "", _clock(total.eta) if total.eta else "",
        "" if total.tokens_new is None else output.styled("dim", format_tokens(total.tokens_new)),
        "" if total.tokens_cache is None else output.styled("dim", format_tokens(total.tokens_cache)),
    ]


def window_label() -> str:
    """The window in hours, as the copy deck writes it: `6h`."""
    return f"{WINDOW_SECONDS // 3600}h"


def _fleet_line(fleet: Fleet, width: int, glyph: output.Glyphs) -> str:
    """The first fleet line (C24): the runs that are reachable, the bar, the percentage, the tickets that ended, the latest ETA, and the tokens.

    The bar is the bar of the run line of `delegate watch`: 16 cells, and only on a terminal of `BAR_COLUMNS` or more. The
    ETA is `-` when no run has one, and the runs with no ETA are counted after it, when there is one. The tokens are
    two sums, `new` and `cache`, and no cost.
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
    if total.tokens_new is not None and total.tokens_cache is not None:
        parts.append(output.styled("dim", f"{format_tokens(total.tokens_new)} new{dot}{format_tokens(total.tokens_cache)} cache"))
    return output.GAP.join(parts)


def _span_line(fleet: Fleet, glyph: output.Glyphs) -> str | None:
    """The second fleet line (C24): the earliest start of the runs of the sums to now, and the runs in trouble. None with no run in the sums.

    The trend of the line comes in a later ticket. The limit of idle time is the stall limit of `delegate watch`.
    """
    runs = [row.facts() for row in fleet.runs]
    start = fleet_rules.earliest_start(runs)
    if start is None:
        return None
    parts = [output.styled("dim", f"{_clock(start)}{glyph.dash}{_clock(fleet.time)}")]
    trouble = fleet_rules.in_trouble(runs, int(IDLE_WARNING_MINUTES * 60))
    if trouble:
        parts.append(output.styled("warn", f"{glyph.idle} {trouble} in trouble"))
    return output.GAP.join(parts)


def fleet_lines(fleet: Fleet, width: int = BAR_COLUMNS) -> list[str]:
    """The lines of `delegate runs`: the line of the missing index, the two fleet lines, a blank line, then the table.

    `width` is the width of the terminal: the bar of the first fleet line needs `BAR_COLUMNS`. The table has the header,
    the row of the host with its totals, and one row for each listed run, with the rail under the name of the host. With
    no listed run, there are no fleet lines, and the rest is the line `no run ended in the last 6h`.
    """
    if not fleet.runs:
        return [NO_INDEX, "", f"no run ended in the last {window_label()}"]
    glyph = output.glyphs()
    head = [NO_INDEX, _fleet_line(fleet, width, glyph), _span_line(fleet, glyph)]
    rows = [[output.styled("dim", name) for name in COLUMNS], _totals(fleet), *(_cells(row, glyph.rail) for row in fleet.runs)]
    return [*(line for line in head if line is not None), "", *(line.rstrip(" ") for line in output.columns(rows, right=NUMBERS))]
