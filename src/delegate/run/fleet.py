"""The runs of this machine: the use case of `delegate runs` (#307).

The command reads each state directory under the state root of the machine (`runs.machine_states`), so it shows each
run of each repository in one table. The spec is `design/docs/components.md`, the table C25 (frames W8 and E15–E18).
This slice has the table of one host and the columns `host repo run state ended ETA new cache`.

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

from . import output
from .journal import ABANDONED, JournalError, newest_run_end, read_new_events
from .runs import RunsError, all_journals, failed_starts, machine_states
from .status import BUILT, FAILED, HALTED, PAUSED, PAUSING, RUNNING, STOPPED, ReaderOf, format_tokens, snapshot_of

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
            rows.append(RunRow(
                snap.run_id, repo, host, snap.state, _time(events[0]), None if end is None else _time(end), snap.tickets_ended,
                snap.tickets_total, snap.eta if snap.state in (RUNNING, PAUSING) else None, snap.tokens_new, snap.tokens_cache,
            ))
        for start in failed_starts([state]):
            if _in_window(start.changed, now):
                rows.append(RunRow(start.run_id, repo, host, FAILED, start.changed, start.changed, 0, 0, None, None, None))
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
    """The cells of the first row of the host group: its name, and the totals of its runs with no `run-end`.

    The totals are the ended count summed, the latest ETA, and the tokens summed. A run that ended is out of every total.
    """
    active = [row for row in fleet.runs if row.ended is None]
    etas = [row.eta for row in active if row.eta is not None and row.state in (RUNNING, PAUSING)]
    new = [row.tokens_new for row in active if row.tokens_new is not None]
    cache = [row.tokens_cache for row in active if row.tokens_cache is not None]
    return [
        fleet.host, "", "", "",
        f"{sum(row.tickets_ended for row in active)}/{sum(row.tickets_total for row in active)}" if active else "",
        _clock(max(etas)) if etas else "",
        output.styled("dim", format_tokens(sum(new))) if new else "", output.styled("dim", format_tokens(sum(cache))) if cache else "",
    ]


def window_label() -> str:
    """The window in hours, as the copy deck writes it: `6h`."""
    return f"{WINDOW_SECONDS // 3600}h"


def fleet_lines(fleet: Fleet) -> list[str]:
    """The lines of the table of `delegate runs`: the line of the missing index, a blank line, then the table.

    The table has the header, the row of the host with its totals, and one row for each listed run, with the rail
    under the name of the host. With no listed run, the second part is the line `no run ended in the last 6h`.
    """
    head = [NO_INDEX, ""]
    if not fleet.runs:
        return [*head, f"no run ended in the last {window_label()}"]
    glyph = output.glyphs()
    rows = [[output.styled("dim", name) for name in COLUMNS], _totals(fleet), *(_cells(row, glyph.rail) for row in fleet.runs)]
    return [*head, *(line.rstrip(" ") for line in output.columns(rows, right=NUMBERS))]
