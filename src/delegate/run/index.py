"""The run index in the engine: a run registers itself at its milestones (#316, ADR 0008).

The engine writes the record of its run to the run index port (`delegate.ports.index`) after `run-start`, `resume`,
each `step-end`, `paused`, `halted` and `run-end`. The values come from the snapshot of the run at the write, which
the journal events give. The journal stays the record of the run, and the index is derived from it.

A write of the index never changes the run. A write that fails adds one `index-warning` event with the reason, and the
run goes on. A run with no index writes nothing and adds no event. The command-line driver always gives an index (#323): with
no setting, the index is `index/` in the state root of the machine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..ports.index import RunIndex, RunRecord
from .events import HALTED, INDEX_WARNING, PAUSED, RESUME, RUN_END, RUN_START, STEP_END
from .host import host_name
from .journal import Journal
from .status import snapshot_at

# The events after which the engine writes the record of the run.
INDEXED: frozenset[str] = frozenset({RUN_START, RESUME, STEP_END, PAUSED, HALTED, RUN_END})


def record_of(events: list[dict[str, object]], journal: Path, repo: Path, now: datetime) -> RunRecord:
    """The record of the run whose journal events are `events`, as of the time `now` (UTC).

    The run that writes the record holds its lock, so the snapshot has a live process and no pause request, and it reads
    no stream. The host and the name of the repository come from `run-start`; a journal of an earlier version without
    them gets the host of this process and the repository of the run.
    """
    snap = snapshot_at(events, journal, lambda adapter: None, now, live_process=True, pause_request=False)
    start = events[0]
    return RunRecord(
        run_id=snap.run_id, host=str(start.get("host") or host_name()), repo=Path(str(start.get("repo") or repo)).name,
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
            index.record(record_of(journal.events, journal.path, repo, datetime.now(timezone.utc)))
        except Exception as error:  # noqa: BLE001 - nothing that the index does may change the run
            journal.append(INDEX_WARNING, reason=str(error) or type(error).__name__)

    journal.after_append = write
