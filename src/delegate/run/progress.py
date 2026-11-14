"""What the journal of an earlier run says about its tickets: the progress that a resume, a retry and `delegate clean --abandon` start from.

The run state decides each ticket (`run_state.fold`, ADR 0009). This module reads that decision into lists in plan order. It has its own
module so that a view of the run context, `delegate clean`, can use it with no import of the engine (#331).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import run_state
from .errors import EngineError


@dataclass
class Progress:
    """What the journal of an earlier run says: the ticket outcomes, and the one step that was open."""

    built: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    # The skipped tickets that the skip rule skipped: the newest `skip` of each names a blocker that was not built.
    blocked: set[str] = field(default_factory=set)
    # The pending tickets that an `unskip` took out of the skipped tickets, and whose step did not start (rule 2 of ADR 0009).
    unskipped: list[str] = field(default_factory=list)
    open_ticket: str | None = None
    events: list[dict[str, object]] = field(default_factory=list)

    def was_skipped(self) -> dict[str, str | None]:
        """The tickets that the run skipped, before the plan change and the retry of a resume: each skipped ticket with its
        reason, and each ticket of `unskipped` with None. A resume records what it does with each of them (`_record_outlook`).
        """
        return {**self.skipped, **dict.fromkeys(self.unskipped)}


def restore_progress(events: list[dict[str, object]], run_id: str, retrying: bool = False) -> Progress:
    """Rebuild the progress of a run from the run state of its journal (ADR 0009). Raise EngineError when it ended, unless `retrying`.

    The run state decides each ticket of the newest plan, the open ticket and the state of the run (`run_state.fold`).
    A `run-end` that a `resume` follows does not end the run: a retry and a plan change go on with a run that ended.
    The lists are in plan order.
    """
    state = run_state.fold(events)
    if state.status.kind == run_state.ENDED and not retrying:
        result = state.status.event["result"]  # type: ignore[index]
        raise EngineError(f"run {run_id!r} has ended with the result {result!r}; it cannot be resumed")
    progress = Progress(events=events, open_ticket=state.open_ticket)
    for ticket in state.tickets:
        if ticket.outcome == run_state.BUILT:
            progress.built.append(ticket.id)
        elif ticket.outcome == run_state.FAILED:
            progress.failed.append(ticket.id)
            # A null reason gives a short text, not the text None.
            progress.failures[ticket.id] = ticket.reason or "the step failed"
        elif ticket.outcome == run_state.SKIPPED:
            progress.skipped[ticket.id] = ticket.reason or "the journal gives no reason"
            # A ticket that the run did not reach has no blockers in its `skip`.
            if ticket.blocked:
                progress.blocked.add(ticket.id)
        elif ticket.unskipped:
            progress.unskipped.append(ticket.id)
    return progress
