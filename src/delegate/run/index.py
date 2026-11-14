"""The run index in the engine: a run registers itself at its milestones (#316, ADR 0008).

The engine writes the record of its run to the run index port (`delegate.ports.index`) after `run-start`, `resume`,
each `step-end`, `paused`, `halted`, `crashed`, `interrupted`, `setup-problem` and `run-end` (#327). The values come from the snapshot of the run at the write, which
the journal events give. The journal stays the record of the run, and the index is derived from it.

A write of the index never changes the run. A write that fails adds one `index-warning` event with the reason, and the
run goes on. A run with no index writes nothing and adds no event. The command-line driver always gives an index (#323): with
no setting, the index is `index/` in the state root of the machine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..ports.index import RunIndex, RunRecord
from .events import CLEANUP, CRASHED, HALTED, INDEX_WARNING, INTERRUPTED, PAUSED, RESUME, RUN_END, RUN_START, SETUP_PROBLEM, STEP_END
from .host import host_name
from .journal import Journal
from .status import Snapshot, snapshot_at

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


def follow(journal: Journal, index: RunIndex | None, repo: Path) -> None:
    """Write the record of the run to `index` after each event of `INDEXED` that `journal` appends. Do nothing with no index.

    Each failure of the write, whatever its cause, adds `index-warning` with the reason: the rule is that the index never changes the run.
    """
    if index is None:
        return

    def write(event: str) -> None:
        if event not in INDEXED:
            return
        try:
            index.record(record_of(journal.events, journal.path, repo, datetime.now(timezone.utc), live_process=event not in PROCESS_ENDS))
        except Exception as error:  # noqa: BLE001 - nothing that the index does may change the run
            journal.append(INDEX_WARNING, reason=str(error) or type(error).__name__)

    journal.after_append = write
