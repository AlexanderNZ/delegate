"""The values of `delegate runs` that the reads and the view share (#334): one run, a host that could not be read, the settings of the header line, and the fleet.

`fleet_hosts.py` reads the other hosts into `RunRow`, `fleet.py` (`fleet_of`) joins them with the journals of this machine into a `Fleet`, and `fleet_view.py` writes the
`Fleet` as lines. `Fleet.to_json` is the contract of `delegate runs --json`. This module reads no file and writes no line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from itertools import groupby
from pathlib import Path

from . import fleet_rules, output
from .live import IDLE_WARNING_MINUTES
from .status import format_duration

# The stall limit of `delegate watch`, in seconds: an agent idle for this time or more is an alert.
IDLE_LIMIT_SECONDS: int = int(IDLE_WARNING_MINUTES * 60)

# The version of the contract of `--json`. A new field does not change it; a change to a field needs a new version.
FORMAT: int = 1


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

    def shown_alert(self) -> fleet_rules.Alert | None:
        """The alert that the cell `alert` shows (`fleet_rules.shown_alert`): the one alert, or for a run in trouble the reason (#322). `to_json` has `alert`, not this."""
        return fleet_rules.shown_alert(self.facts(), IDLE_LIMIT_SECONDS)

    def do(self) -> str | None:
        """The word of the column `do` (`fleet_rules.next_command`)."""
        return fleet_rules.next_command(self.facts(), self.alert() is not None)

    def to_json(self) -> dict[str, object]:
        """The document of one run. `idle_seconds`, `fixup_round` and `fixup_limit` are the values of the one alert in `alert` (#329), or None, so a reader works out its own alert from them and parses no text."""
        found = self.alert()
        idle = found.seconds if found is not None and found.kind == fleet_rules.IDLE else None
        round_of, limit_of = (found.round, found.limit) if found is not None and found.kind == fleet_rules.FIXUP else (None, None)
        return {
            "run_id": self.run_id, "repo": self.repo, "host": self.host, "state": self.state,
            "started": self.started.isoformat(), "ended": self.ended.isoformat() if self.ended else None,
            "tickets_ended": self.tickets_ended, "tickets_total": self.tickets_total,
            "eta": self.eta.isoformat() if self.eta else None, "tokens_new": self.tokens_new, "tokens_cache": self.tokens_cache,
            "alert": None if found is None else alert_text(found, output.UNICODE.idle), "do": self.do(), "trend": list(self.trend),
            "slip_seconds": self.slip_seconds, "idle_seconds": idle, "fixup_round": round_of, "fixup_limit": limit_of, "failed_start": self.failed_start,
        }


@dataclass(frozen=True)
class ViewSettings:
    """The settings that the header line of `delegate runs` names (#323): the configuration file that the command read, or None when there is none;
    the directory of a shared run index, or None for the index of this machine only; and the hosts of the key `hosts`, which the command reads too.
    """

    config: Path | None = None
    shared_index: Path | None = None
    hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class LostHost:
    """A host that the command could not read (#318, #323): its name, the reason, and the time it was last heard from, or None when it never was.

    The time is the time of its last answer in the store of last answers, or of its newest record in the run index.
    """

    name: str
    reason: str
    heard: datetime | None


@dataclass(frozen=True)
class Fleet:
    """The runs of this machine and of the other hosts of the run index: the name of this machine, the time of the command, the listed runs
    in the order of the table, and each journal that the listing skipped with the reason.

    The runs of this machine come first, then the runs of each other host, in the order of the names of the hosts (`RunRow.unreachable`).
    `view` is what the header line names (#323). `host_count` is this machine and each other host that the command read: a host that the index
    has a record of, whether the record is listed or not, and a host of the key `hosts`. `index_problem` is the reason that the index
    could not be read or written, or None. `read` is the name of each other host that the command read, in order, and `lost` is each of them
    that it could not read. `others_read` is False for `--local` and `--json`, which read no other host.
    """

    host: str
    time: datetime
    runs: tuple[RunRow, ...]
    unreadable: tuple[tuple[Path, str], ...] = ()
    view: ViewSettings = field(default_factory=ViewSettings)
    host_count: int = 1
    index_problem: str | None = None
    read: tuple[str, ...] = ()
    lost: tuple[LostHost, ...] = ()
    others_read: bool = True

    def unreadable_lines(self, command: str) -> list[str]:
        """One line for each journal that the listing skipped, and one for an index that could not be read, for stderr."""
        lines = [f"{command}: skipped the journal {path}: {reason}" for path, reason in self.unreadable]
        return lines + ([f"{command}: {self.index_problem}"] if self.index_problem else [])

    def hosts(self) -> list[tuple[str, tuple[RunRow, ...]]]:
        """The host groups of the table: this machine first, then each other host with a listed run or that could not be read, with the rows of each.

        A host that could not be read and that has no last-known run has no rows (#323).
        """
        groups = [(host, tuple(rows)) for host, rows in groupby(self.runs, key=lambda row: row.host)]
        mine = [group for group in groups if group[0] == self.host]
        present = {name for name, _ in groups}
        others = [group for group in groups if group[0] != self.host] + [(lost.name, ()) for lost in self.lost if lost.name not in present]
        return mine + sorted(others, key=lambda group: group[0])

    def lost_host(self, name: str) -> LostHost | None:
        """The host `name` when the command could not read it, else None."""
        return next((lost for lost in self.lost if lost.name == name), None)

    def to_json(self) -> dict[str, object]:
        """The object of `delegate runs --json`. The reference page of the command documents it: a new field comes last."""
        return {
            "format": FORMAT, "host": self.host, "time": self.time.isoformat(), "window_seconds": fleet_rules.WINDOW_SECONDS,
            "runs": [row.to_json() for row in self.runs if not row.unreachable],
        }


def alert_text(found: fleet_rules.Alert, idle_glyph: str) -> str:
    """The words of an alert (copy deck W8.alert.idle and W8.alert.fixup): `⚠ idle 6m40s`, or `fix-up 1/2`.

    An alert that says why a run is in trouble (#322) is its words: `stopped`, `halted` or `failed at start`.
    """
    if found.kind == fleet_rules.IDLE:
        return f"{idle_glyph} idle {format_duration(found.seconds)}"
    if found.kind in fleet_rules.TROUBLE_KINDS:
        return found.kind
    return f"{found.kind} {found.round}/{found.limit}"
