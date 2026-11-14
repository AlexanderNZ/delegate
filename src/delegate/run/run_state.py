"""The run state: the one fold of the journal of a run that the engine and the views decide from (#319, ADR 0009).

`fold` gives the run state of a list of journal events. It is a pure function of the events. It reads no file, no lock,
no pause request, no event stream and no clock, and it imports no other module of `delegate` (a pure module: see
`PURE_MODULES` in `tests/test_architecture.py`). The run state at an earlier time is the run state of the events up to
that time, so a caller that wants it gives `events[:n]`.

The run state holds:

* for each ticket of the newest plan, its outcome, the reason of a failed or a skipped ticket, and whether the skip rule
  skipped it (`TicketState`);
* the open ticket, or none;
* the state of the run, and the event that gives it (`RunStatus`);
* the current pass of the chain verification (`ChainPass`).

The three rules where the folds of the engine and of the views differed before this module are in `_tickets` and `_status`:

1. A `plan-change` takes each removed ticket, and each changed ticket, out of the skipped tickets.
2. An `unskip` makes the ticket pending.
3. A `run-end` ends the run when no `resume` comes after it. Before #320 the engine ended a run with a `run-end` after the
   newest `plan-change`, so it read a retry of a run that ended, which then stopped, as a run that ended.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

# The outcome of a ticket.
PENDING: str = "pending"
OPEN: str = "open"
BUILT: str = "built"
FAILED: str = "failed"
SKIPPED: str = "skipped"

# The state of a run.
ACTIVE: str = "active"
ENDED: str = "ended"
PAUSED: str = "paused"
HALTED: str = "halted"
CRASHED: str = "crashed"
SETUP_PROBLEM: str = "setup-problem"

# The event that gives each state of a run that is not active. A state that comes first in this tuple is stronger: when
# more than one such event follows the newest `resume`, the run has the first state in the tuple that has an event.
_STATE_OF_EVENT: tuple[tuple[str, str], ...] = (
    ("run-end", ENDED), ("paused", PAUSED), ("halted", HALTED), ("crashed", CRASHED), ("setup-problem", SETUP_PROBLEM),
)

# The `phase` of a `retry` that goes on with the verification of a chain. The domain module holds the same value
# (`domain.RETRY_CHAIN_FIXUP`): a pure module imports no other module, and a test checks that the two are equal.
RETRY_CHAIN_FIXUP: str = "chain-fixup"


@dataclass(frozen=True)
class TicketState:
    """The state of one ticket of the newest plan.

    `reason` is the reason of the `step-end` of a failed ticket and of the `skip` of a skipped ticket, as the journal
    gives it, or None when the journal gives none or the ticket is not failed or skipped. `blocked` is True for a skipped
    ticket that the skip rule skipped: its newest `skip` names a blocker that was not built.

    `retrying` is True when a `retry` of the ticket comes after its newest `step-start` or `step-end`. The outcome then
    stays `failed` or `built` until the retry ends, as the engine reads it, and a view shows the ticket as running.
    """

    id: str
    outcome: str = PENDING
    reason: str | None = None
    blocked: bool = False
    retrying: bool = False


@dataclass(frozen=True)
class RunStatus:
    """The state of the run: one of the states of this module, and the event that gives it (None when the run is active)."""

    kind: str
    event: Mapping[str, object] | None = None


@dataclass(frozen=True)
class StackPass:
    """The `verdict` values and the `fixup-start` events of one stack, in the current pass of the chain verification."""

    stack: str
    verdicts: tuple[str, ...]
    fixup_starts: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class ChainPass:
    """The current pass of the chain verification of an `economy` run.

    `start` is the position, in the events, of the first event of the pass: the event after the newest event that starts a
    pass (`starts_chain_verification`), or 0 when none does. `stacks` holds each stack that has a `verdict` or a
    `fixup-start` in the pass, in the order of its first such event. `advanced` is True when the pass has a
    `run-branch-advance`.
    """

    start: int
    stacks: tuple[StackPass, ...]
    advanced: bool


@dataclass(frozen=True)
class RunState:
    """What the journal of a run says: its tickets, its open ticket, its state, and the current pass of its chain."""

    tickets: tuple[TicketState, ...]
    open_ticket: str | None
    status: RunStatus
    chain: ChainPass

    def ticket(self, ticket_id: str) -> TicketState:
        """The state of the ticket `ticket_id`. Raise KeyError when the newest plan does not hold it."""
        for found in self.tickets:
            if found.id == ticket_id:
                return found
        raise KeyError(ticket_id)


def starts_chain_verification(event: Mapping[str, object]) -> bool:
    """Tell whether a journal event starts a new pass of the verification of an `economy` chain (#297).

    A `plan-change` and a `retry` start one, except a `retry` with the phase `chain-fixup`: that retry goes on with the
    verification that the stop cut off, so the fix-up rounds and the time before it still count. The engine reads the
    events after the newest such event, and `delegate status` and `delegate watch` use this function for the same
    rule, so the round and the time of a chain are the same in the three.
    """
    kind = event["event"]
    return kind == "plan-change" or (kind == "retry" and event.get("phase") != RETRY_CHAIN_FIXUP)


def fold(events: Sequence[Mapping[str, object]]) -> RunState:
    """The run state of the events of a journal, in order. A list with no event gives a run state with no ticket."""
    return RunState(*_tickets(events), status=_status(events), chain=_chain(events))


def _plan(events: Sequence[Mapping[str, object]]) -> list[str]:
    """The ticket ids of the newest plan: the `order` of the newest `plan-change`, or else the `tickets` of `run-start`."""
    for event in reversed(events):
        if event["event"] == "plan-change":
            return [str(t) for t in event["order"]]  # type: ignore[attr-defined]
    if events and events[0]["event"] == "run-start":
        return [str(t) for t in events[0]["tickets"]]  # type: ignore[attr-defined]
    return []


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _tickets(events: Sequence[Mapping[str, object]]) -> tuple[tuple[TicketState, ...], str | None]:
    """The state of each ticket of the newest plan, and the open ticket.

    The newest event of a ticket that sets its outcome decides it: `step-start`, `step-end`, `skip` or `unskip`. The
    open ticket is the ticket of the newest `step-start` that no `step-end` of the same ticket follows.
    """
    found: dict[str, TicketState] = {}
    open_ticket: str | None = None
    for event in events:
        kind = event["event"]
        ticket = str(event.get("ticket"))
        if kind == "plan-change":
            # Rule 1: a removed ticket and a changed ticket are no longer skipped.
            for name in [*event.get("removed", []), *event.get("changed", [])]:  # type: ignore[attr-defined, misc]
                if found.get(str(name), TicketState(str(name))).outcome == SKIPPED:
                    found[str(name)] = TicketState(str(name))
            continue
        if kind not in ("step-start", "step-end", "skip", "unskip", "retry"):
            continue
        before = found.get(ticket, TicketState(ticket))
        if kind == "step-start":
            found[ticket], open_ticket = TicketState(ticket, OPEN), ticket
        elif kind == "step-end":
            built = event["state"] == BUILT
            found[ticket] = TicketState(ticket, BUILT if built else FAILED, None if built else _text(event.get("reason")))
            open_ticket = None if open_ticket == ticket else open_ticket
        elif kind == "skip":
            found[ticket] = TicketState(ticket, SKIPPED, _text(event.get("reason")), bool(event.get("blockers")))
        elif kind == "unskip":
            found[ticket] = TicketState(ticket)  # rule 2
        else:
            found[ticket] = replace(before, retrying=True)
    return tuple(found.get(ticket, TicketState(ticket)) for ticket in _plan(events)), open_ticket


def _status(events: Sequence[Mapping[str, object]]) -> RunStatus:
    """The state of the run from the events after the newest `resume`, and the event that gives it.

    Rule 3: a `run-end` ends the run when no `resume` comes after it. The events that a command writes after the
    `run-end` of a run that ended (`cleanup`, `lock-broken`, `index-warning`) are no stop event, so they change nothing.
    """
    newest: dict[str, Mapping[str, object]] = {}
    for event in reversed(events):
        if event["event"] == "resume":
            break
        newest.setdefault(str(event["event"]), event)
    for name, kind in _STATE_OF_EVENT:
        if name in newest:
            return RunStatus(kind, newest[name])
    return RunStatus(ACTIVE)


def _chain(events: Sequence[Mapping[str, object]]) -> ChainPass:
    """The current pass of the chain verification: the events after the newest event that starts a pass."""
    starts = [index for index, event in enumerate(events) if starts_chain_verification(event)]
    start = starts[-1] + 1 if starts else 0
    verdicts: dict[str, list[str]] = {}
    fixups: dict[str, list[Mapping[str, object]]] = {}
    advanced = False
    for event in events[start:]:
        kind, stack = event["event"], event.get("stack")
        if kind == "run-branch-advance":
            advanced = True
        elif kind in ("verdict", "fixup-start") and isinstance(stack, str):
            verdicts.setdefault(stack, [])
            fixups.setdefault(stack, [])
            if kind == "verdict":
                verdicts[stack].append(str(event["verdict"]))
            else:
                fixups[stack].append(event)
    return ChainPass(start, tuple(StackPass(s, tuple(verdicts[s]), tuple(fixups[s])) for s in verdicts), advanced)
