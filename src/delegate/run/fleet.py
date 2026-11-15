"""The runs of this machine: the use case of `delegate runs` (#307).

`fleet_of` reads the journals of each state directory under the state root of the machine (`runs.machine_states`) and joins them with the other hosts into the `Fleet`
(`fleet_model`). The reads of the other hosts are in `fleet_hosts` (#318, #328), the table and the timeline are in `fleet_view` (`fleet_lines`, #308–#315), and the backfill of the
run index is `index.record_runs` (#323, #334).

A run is listed when it has no `run-end` (running, pausing, paused, halted or stopped), or when its `run-end` is
in the window, the last `fleet_rules.WINDOW_SECONDS`. A run that failed at start (`runs.FailedStart`) and whose log last changed
in the window is listed with the state `failed`. An older run that ended is not listed.

With the same `JournalReads` at each call, as `delegate runs --watch` does (#313), a call reads each journal only from the position where the call before stopped, so a redraw does
not read each journal from the start. A journal that cannot be read is skipped, and `Fleet.unreadable` names it with the reason, as `delegate status` does.

With a run index (#317, ADR 0008), `fleet_of` adds a group for each other host of the index, from the records of the index, and for each host of the key
`hosts` of the configuration file (#323, `ViewSettings`). The runs of this machine come from its journals, and its records in the index are not used. A host that
is not read, or whose read fails, is `unreachable`, with its last-known runs (`fleet_hosts.other_hosts`). `--json` describes this machine only.

The run index is always on (#323). With an index, `fleet_of` writes the record of each listed run of this machine that has no record, or an older one (`index.record_runs`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from ..ports.index import RunIndex, RunIndexError, RunRecord
from . import domain, fleet_rules, run_state
from .fleet_hosts import HostReads, other_hosts
from .fleet_model import Fleet, LostHost, RunRow, ViewSettings
from .fleet_rules import in_window
from .index import record_runs
from .journal import JournalError, JournalReads, event_time
from .runs import RunsError, all_journals, failed_starts, machine_states
from .state_location import repository_name
from .status import FAILED, PAUSING, RUNNING, ReaderOf, snapshot_at, snapshot_of


def _eta_then(events: list[dict[str, object]], journal: Path, now: datetime) -> datetime | None:
    """The ETA that the run had `fleet_rules.SLIP_WINDOW_SECONDS` before `now` (#311), worked out from its journal.

    The engine records no ETA. The snapshot of that time is the events up to it, that time, a live process and no pause
    request, with no reader of the streams: the ETA comes from the events alone. None when the run had not started then.
    """
    then = now - timedelta(seconds=fleet_rules.SLIP_WINDOW_SECONDS)
    until = [event for event in events if event_time(event) <= then]
    if not until:
        return None
    return snapshot_at(until, journal, lambda adapter: None, then, live_process=True, pause_request=False).eta


def fleet_of(
    root: Path, host: str, now: datetime, reader_of: ReaderOf, reads: JournalReads | None = None, index: RunIndex | None = None,
    read_index: bool = True, hosts: HostReads | None = None, view: ViewSettings | None = None,
) -> Fleet:
    """The runs of the machine under the state root `root`. `host` is the name of the machine, and `now` is the time of the clock, in UTC.

    `reads` holds what an earlier call read (`JournalReads`); with None, each journal is read from the start. `index` is the run index, or
    None when none is set up; the runs of the other hosts of the index follow the runs of this machine (#317). With `read_index` False
    the index is not read (`delegate runs --local`), and no other host is read. `hosts` reads the other hosts (#318); with None, each
    of them is unreachable, as in #317. `view` is what the header line names (#323); its `hosts` are other hosts to read, also with no record in the index.

    With an index, the call writes the record of each listed run of this machine that has no record, or an older one (#323, `index.record_runs`),
    also with `read_index` False. A write that fails is in `Fleet.index_problem`, and the call goes on.

    The order is the order of the table: the runs with no `run-end`, newest start first, then the runs that ended,
    newest end first. A lock file that cannot be read, in the search for the runs that failed at start, is one entry of `Fleet.unreadable` for its
    state directory (#330), as a journal that cannot be read is; the other state directories are listed.
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
                folded = run_state.fold(events)
                end = folded.run_end
                if end is not None and not in_window(event_time(end), now):
                    continue  # an older run that ended is not listed, and its streams are not read
                snap = snapshot_of(events, journal, reader_of, now, folded=folded)
                eta = snap.eta if snap.state in (RUNNING, PAUSING) else None
                slip = fleet_rules.slip_seconds(eta, None if eta is None else _eta_then(events, journal, now), event_time(events[0]), now)
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
                snap.run_id, repo, host, snap.state, event_time(events[0]), None if end is None else event_time(end), snap.tickets_ended,
                snap.tickets_total, eta, snap.tokens_new, snap.tokens_cache,
                idle_seconds=max(idle) if idle else None, fixup=fixup,
                trend=fleet_rules.trend_cells(built_times, now), slip_seconds=slip, built=tuple(built_times), last_event=event_time(events[-1]),
            ))
        try:
            starts = failed_starts([state])
        except RunsError as error:  # a lock file that cannot be read: one line for the state directory, and the other repositories show (#330)
            unreadable.append((state / "runs", str(error)))
            continue
        for start in starts:
            if in_window(start.changed, now):
                rows.append(RunRow(start.run_id, repo, host, FAILED, start.changed, start.changed, 0, 0, None, None, None, failed_start=True))
    reads.keep_only(listed)
    working = sorted((row for row in rows if row.ended is None), key=lambda row: row.started, reverse=True)
    ended = sorted((row for row in rows if row.ended is not None), key=lambda row: row.ended or row.started, reverse=True)
    view = ViewSettings() if view is None else view
    foreign: list[RunRow] = []
    names: list[str] = []
    lost: list[LostHost] = []
    problem = None
    if index is not None:
        problem = record_runs(root, host, now, index, reads)
    if read_index:
        records: list[RunRecord] = []
        if index is not None:
            try:
                records = index.list_records()
            except RunIndexError as error:
                problem = problem or str(error)
        foreign, names, lost = other_hosts(records, host, now, hosts, view.hosts)
    return Fleet(
        host, now, tuple(working + ended + foreign), tuple(unreadable), view, 1 + len(names), problem, tuple(names), tuple(lost), read_index,
    )
