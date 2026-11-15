"""The run index in the engine: a run registers itself at its milestones (#316, ADR 0008).

The engine writes the record of its run to the run index port (`delegate.ports.index`) after `run-start`, `resume`,
each `step-end`, `paused`, `halted`, `crashed`, `interrupted`, `setup-problem` and `run-end` (#327). The values come from the snapshot of the run at the write, which
the journal events give. The journal stays the record of the run, and the index is derived from it.

A write of the index never changes the run. A write that fails adds one `index-warning` event with the reason, and the
run goes on. A run with no index writes nothing and adds no event. The command-line driver always gives an index (#323): with
no setting, the index is `index/` in the state root of the machine.

The backfill (`record_runs`, #323, #334) writes the record of each run of the state root of the machine that has none, or an older one, so a run of an earlier
version, or a run that started with no index, gets its record. It reads the fold of each journal and one snapshot, and no more: it builds no view (no ETA of
two hours ago, no trend). `delegate runs` and `delegate run` call it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from ..ports.index import RunIndex, RunIndexError, RunRecord
from .events import CLEANUP, CRASHED, HALTED, INDEX_WARNING, INTERRUPTED, PAUSED, RESUME, RUN_END, RUN_START, SETUP_PROBLEM, STEP_END
from .fleet_rules import in_window
from .host import host_name
from .journal import EventObserver, Journal, JournalError, JournalReads, event_time
from .runs import RunsError, StateScan, machine_scans
from .state_location import repository_name
from .status import snapshot_at, snapshot_of
from .status_model import Snapshot

# The events after which the engine writes the record of the run.
INDEXED: frozenset[str] = frozenset({RUN_START, RESUME, STEP_END, PAUSED, HALTED, CRASHED, INTERRUPTED, SETUP_PROBLEM, RUN_END})

# The events after which the run process ends: the record is of a run with no live process, so its state is `stopped`.
PROCESS_ENDS: frozenset[str] = frozenset({CRASHED, INTERRUPTED, SETUP_PROBLEM})

# The events that start a stretch of the run: `cleaned` looks for a `cleanup` event after the newest of them.
_STRETCH_STARTS: frozenset[str] = frozenset({RUN_START, RESUME, RUN_END, PAUSED, HALTED, CRASHED, SETUP_PROBLEM, INTERRUPTED})


def cleaned(events: list[dict[str, object]]) -> bool:
    """True when `delegate clean` or `delegate clean --abandon` cleaned the run whose journal events are `events` (#327).

    The run is cleaned when it ended `abandoned`, or when a `cleanup` event comes after its newest `run-start`, `resume`, `run-end` or
    stop (the engine writes its own `cleanup` before `run-end`, so that one does not count). The command forgot the record of such a
    run (#317), so the backfill of `delegate runs` (#323) writes none. A `resume` after the clean makes the run not cleaned.
    """
    for event in reversed(events):
        name = event["event"]
        if name == CLEANUP:
            return True
        if name in _STRETCH_STARTS:
            return name == RUN_END and event.get("result") == "abandoned"
    return False


def record_of(
    events: list[dict[str, object]], journal: Path, repo: Path, now: datetime, *, host: str | None = None, snapshot: Snapshot | None = None,
    live_process: bool = True,
) -> RunRecord:
    """The record of the run whose journal events are `events`, as of the time `now` (UTC). It is the one builder of a record (#326).

    The engine and the backfill of `delegate runs` (#323) both call it, so a run has one record whatever the process that writes it. The host
    and the name of the repository come from `run-start`; a journal of an earlier version without them gets `host`, the host name of this
    process when it is None, and the name of `repo`.

    The engine holds the lock of its run, so with no `snapshot` the record is of a snapshot with a live process (`live_process`; the engine gives False
    after an event of `PROCESS_ENDS`) and no pause request, and it reads no stream. The backfill reads the lock and the pause request of the run, and gives the `snapshot` that it made from them.
    """
    snap = snapshot or snapshot_at(events, journal, lambda adapter: None, now, live_process=live_process, pause_request=False)
    start = events[0]
    return RunRecord(
        run_id=snap.run_id, host=str(start.get("host") or host or host_name()), repo=Path(str(start.get("repo") or repo)).name,
        started=str(start["time"]), state=snap.state, tickets_ended=snap.tickets_ended, tickets_total=snap.tickets_total,
        eta=snap.eta.isoformat() if snap.eta else None, heard=now.isoformat(),
    )


def index_observers(index: RunIndex | None, repo: Path) -> tuple[EventObserver, ...]:
    """The observers that write the record of the run to `index` after each event of `INDEXED` that a journal appends. None with no index.

    The command-line driver gives them to `run_workflow` (#332). Each failure of the write, whatever its cause, adds `index-warning`
    with the reason: the rule is that the index never changes the run.
    """
    if index is None:
        return ()

    def write(journal: Journal, event: str) -> None:
        if event not in INDEXED:
            return
        try:
            index.record(record_of(journal.events, journal.path, repo, datetime.now(timezone.utc), live_process=event not in PROCESS_ENDS))
        except Exception as error:  # noqa: BLE001 - nothing that the index does may change the run
            journal.append(INDEX_WARNING, reason=str(error) or type(error).__name__)

    return (write,)


def _is_older(record: RunRecord, made: RunRecord, last_event: datetime) -> bool:
    """True when `record` is older than the journal of its run: it was written before the newest event, or it differs from the record `made` now.

    The ETA and the time of the write are not compared: they change with the clock (#326).
    """
    heard = record.heard_time()
    if heard is None or heard < last_event:
        return True
    return replace(record, eta=None, heard="") != replace(made, eta=None, heard="")


def _unrecorded(
    scan: StateScan, repo: str, host: str, now: datetime, reads: JournalReads, recorded: dict[str, RunRecord], snapshots: dict[Path, Snapshot],
) -> list[RunRecord]:
    """The records to write for the journals of one repository: of each run that is listed, not cleaned, and has no record or an older one.

    A run that ended before the window is not listed (`fleet_rules.in_window`). A journal that cannot be read is skipped: `delegate runs` names it.
    `snapshots` holds the snapshot that the caller made of a journal at `now`: the record takes the snapshot from it and makes none (#350).
    """
    found = []
    for journal in scan.journals:
        try:
            events = reads.events(journal)
            folded = reads.folded(journal)
            end = folded.run_end
            if end is not None and not in_window(event_time(end), now):
                continue
            snap = snapshots.get(journal) or snapshot_of(events, journal, lambda adapter: None, now, folded=folded)
            made = record_of(events, journal, Path(repo), now, host=host, snapshot=snap)
        except (JournalError, RunsError):
            continue
        old = recorded.get(made.run_id)
        if not cleaned(events) and (old is None or _is_older(old, made, event_time(events[-1]))):
            found.append(made)
    return found


def backfill(
    root: Path, host: str, now: datetime, index: RunIndex, reads: JournalReads | None = None, scans: Sequence[StateScan] | None = None,
    snapshots: dict[Path, Snapshot] | None = None,
) -> tuple[list[RunRecord], str | None]:
    """`record_runs`, and the records that the index holds after the writes: the one listing of the index of a view (#350). The second value is the reason of the first failure, or None.

    `scans` is the scan of each state directory under `root` (`runs.machine_scans`) when the caller has them, and `snapshots` is a snapshot that the caller made at `now` for
    each journal (`fleet.fleet_of`); with None, the call scans and makes its own. When the index cannot be listed, the records are empty.
    """
    reads = JournalReads() if reads is None else reads
    scanned = machine_scans(root) if scans is None else scans
    snaps = {} if snapshots is None else snapshots
    try:
        recorded = {record.run_id: record for record in index.list_records()}
    except RunIndexError as error:
        return [], str(error)
    try:
        for scan in scanned:
            for record in _unrecorded(scan, repository_name(scan.state), host, now, reads, recorded, snaps):
                index.record(record)
                recorded[record.run_id] = record
    except RunIndexError as error:
        return list(recorded.values()), str(error)
    return list(recorded.values()), None


def record_runs(root: Path, host: str, now: datetime, index: RunIndex, reads: JournalReads | None = None) -> str | None:
    """Write the record of each run of the state root `root` that `delegate runs` lists and that has no record, or an older one (#323). Return the reason of the first failure of the index, or None.

    `delegate run` and `delegate run --resume` call it before the run starts, and `delegate runs` calls it for each frame (`reads` is then the reads of the frame, so the journals are read once). It reads no
    stream and builds no view: each record is the one that `record_of` makes from the fold of the journal, so the write is safe at any time (ADR 0008). A run that failed at start has no journal, so it gets no
    record. A run that `delegate clean` cleaned gets none: the command forgot its record (#327). A lock file that cannot be read stops no record (#330).
    """
    return backfill(root, host, now, index, reads)[1]
