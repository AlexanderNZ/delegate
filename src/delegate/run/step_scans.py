"""The scans of the journal events of a step: one function for each rule that the engine read by hand in more than one place (#360).

Each function is a pure function of a list of journal events. It reads no file, no lock and no clock, and it imports no other
module of `delegate` (a pure module: see `PURE_MODULES` in `tests/test_architecture.py`). The engine decides from the answers. The
functions are:

* `events_of`, `step_events` and `step_start_place`: the events of a ticket, and those of its newest step;
* `unanswered_stop`: the newest `agents-changed` that no `agent-start` follows (the stop of the agent check, #205);
* `verify_commit_before`, `accepted_verdict` and `accepted_commits`: the commit of the `verify-start` before a verdict;
* `advance_place` and `events_after_advance`: the place of the newest `run-branch-advance`, and the events after it;
* `chain_failed`: a step that the verification of an `economy` chain failed (#270, #338);
* `specialist_done` and `specialist_reports`: a valid report of the specialist after its last spawn (#339).

A place is the index of an event in the list that the function got. A caller that slices the list gets a place in the slice.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

Event = dict[str, object]

# The verdict that lets a branch through. The domain module holds the same value (`domain.ACCEPT`): a pure module imports no other
# module, and a test checks that the two are equal.
ACCEPT: str = "ACCEPT"

# The state of a `step-end` of a built step. The run state holds the same value (`run_state.BUILT`).
BUILT: str = "built"


def events_of(events: Sequence[Event], ticket: str) -> list[Event]:
    """The events that name `ticket`, in the order of the journal."""
    return [event for event in events if event.get("ticket") == ticket]


def step_start_place(events: Sequence[Event]) -> int | None:
    """The place of the newest `step-start` in `events`, or None when there is none."""
    places = [place for place, event in enumerate(events) if event["event"] == "step-start"]
    return places[-1] if places else None


def step_events(events: Sequence[Event], ticket: str) -> list[Event]:
    """The events of the newest step of `ticket`: its events from its last `step-start`.

    A retry with a new text starts the ticket again, so the journal of such a ticket holds more than one step. The rounds, the
    verdicts and the reports of the steps before the last one are history. A ticket with no `step-start` gives all its events.
    """
    own = events_of(events, ticket)
    place = step_start_place(own)
    return own if place is None else own[place:]


def unanswered_stop(events: Sequence[Event], role: str | None = None, ticket: str | None = None) -> int | None:
    """The place of the newest `agents-changed` that no `agent-start` follows, or None.

    The agent check stops a step before an agent session. The step then holds no work of that session, so a retry can run the
    session. With `role`, only an `agents-changed` of that role counts, and a newer one of another role does not hide an older
    one of `role`. With `ticket`, only the events of that ticket count, for the stops and for the `agent-start` events.
    """
    places = [
        place for place, event in enumerate(events)
        if (ticket is None or event.get("ticket") == ticket) and event["event"] == "agents-changed"
        and (role is None or event["role"] == role)
    ]
    if not places:
        return None
    after = events[places[-1] + 1 :]
    if any(event["event"] == "agent-start" and (ticket is None or event.get("ticket") == ticket) for event in after):
        return None
    return places[-1]


def verify_commit_before(events: Sequence[Event], place: int, stack: str | None = None) -> str | None:
    """The `commit` of the newest `verify-start` before `place`, or None when none comes before it.

    With `stack`, only a `verify-start` of that stack counts (the verification of an `economy` chain).
    """
    for event in reversed(events[:place]):
        if event["event"] == "verify-start" and (stack is None or event.get("stack") == stack):
            return str(event["commit"])
    return None


class AcceptedVerdict(NamedTuple):
    """The newest `verdict` of a list of events when it accepts, with the commit that its verifier checked."""

    verdict: Event
    commit: str


def accepted_verdict(events: Sequence[Event]) -> AcceptedVerdict | None:
    """The newest `verdict` of `events` with the commit of the `verify-start` before it, or None.

    None when there is no verdict, when the newest verdict is not an ACCEPT (an older ACCEPT does not count), or when no
    `verify-start` comes before it.
    """
    places = [place for place, event in enumerate(events) if event["event"] == "verdict"]
    if not places or events[places[-1]]["verdict"] != ACCEPT:
        return None
    commit = verify_commit_before(events, places[-1])
    return None if commit is None else AcceptedVerdict(events[places[-1]], commit)


def accepted_commits(events: Sequence[Event]) -> list[str]:
    """The commit of each accepted `verdict` of `events`: the `commit` of the newest `verify-start` before it, in the order of the verdicts.

    A verdict with no `verify-start` before it adds no commit.
    """
    accepted: list[str] = []
    commit: str | None = None
    for event in events:
        if event["event"] == "verify-start":
            commit = str(event["commit"])
        elif event["event"] == "verdict" and commit is not None and event["verdict"] == ACCEPT:
            accepted.append(commit)
    return accepted


def advance_place(events: Sequence[Event]) -> int:
    """The place of the newest `run-branch-advance` in `events`, or -1 when there is none."""
    places = [place for place, event in enumerate(events) if event["event"] == "run-branch-advance"]
    return places[-1] if places else -1


def events_after_advance(events: Sequence[Event]) -> list[Event]:
    """The events after the newest `run-branch-advance`, or all the events when there is none."""
    return list(events[advance_place(events) + 1 :])


def chain_failed(prior: Sequence[Event]) -> bool:
    """True when the verification of the chain failed the step: the step has a `step-end` built before its newest `step-end`."""
    return any(e["event"] == "step-end" and e["state"] == BUILT for e in prior)


def specialist_reports(prior: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    """The `report-validation` events of the step when a valid report of the specialist came after its last spawn, else none."""
    return [e for e in prior if e["event"] == "report-validation"] if specialist_done(list(prior)) else []


def specialist_done(prior: list[dict[str, object]]) -> bool:
    """True when the journal shows a valid specialist report after the last time the specialist was spawned."""
    spawned = valid = -1
    for index, event in enumerate(prior):
        if event["event"] in ("step-start", "continuation"):
            spawned = index
        elif event["event"] == "report-validation" and event["valid"]:
            valid = index
    return valid > spawned
