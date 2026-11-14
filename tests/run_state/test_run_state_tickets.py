"""The run state of the tickets: the outcome of each ticket of the newest plan, its reason, and the open ticket.

The seam is `run_state.fold`, the one function that gives the run state of a list of journal events.
"""

from __future__ import annotations

import dataclasses

import pytest

from delegate.run import run_state

from .journals import built, ev, failed, journal, plan_change, skipped, started


def outcomes(events) -> dict[str, str]:
    return {t.id: t.outcome for t in run_state.fold(events).tickets}


def test_a_run_that_only_started_has_every_ticket_pending_and_no_open_ticket():
    state = run_state.fold(journal())

    assert [(t.id, t.outcome, t.reason, t.blocked) for t in state.tickets] == [
        ("a", "pending", None, False), ("b", "pending", None, False), ("c", "pending", None, False),
    ]
    assert state.open_ticket is None


def test_a_step_start_opens_the_ticket_and_the_step_end_closes_it():
    open_now = run_state.fold(journal(started("a")))
    closed = run_state.fold(journal(started("a"), built("a")))

    assert (open_now.open_ticket, outcomes(journal(started("a")))["a"]) == ("a", "open")
    assert (closed.open_ticket, outcomes(journal(started("a"), built("a")))["a"]) == (None, "built")
    assert open_now.ticket("a").outcome == run_state.OPEN


def test_a_failed_ticket_holds_the_reason_of_its_step_end():
    state = run_state.fold(journal(started("a"), failed("a", "the gates are red")))

    assert (state.ticket("a").outcome, state.ticket("a").reason) == ("failed", "the gates are red")


def test_a_step_end_with_no_reason_gives_no_reason_and_not_the_text_none():
    state = run_state.fold(journal(started("a"), failed("a", None)))

    assert (state.ticket("a").outcome, state.ticket("a").reason) == ("failed", None)


def test_a_retry_that_builds_a_failed_ticket_makes_it_built_and_drops_the_reason():
    events = journal(started("a"), failed("a"), ev("retry", ticket="a", phase="specialist", round=0, reason="x"), started("a"), built("a"))

    assert (run_state.fold(events).ticket("a").outcome, run_state.fold(events).ticket("a").reason) == ("built", None)


def test_the_verification_of_a_chain_can_fail_a_ticket_that_was_built():
    events = journal(started("a"), built("a"), failed("a", "the verifier rejected the chain"))

    assert (run_state.fold(events).ticket("a").outcome, run_state.fold(events).ticket("a").reason) == ("failed", "the verifier rejected the chain")


def test_a_skipped_ticket_holds_its_reason_and_says_whether_the_skip_rule_skipped_it():
    events = journal(started("a"), failed("a"), skipped("b"), skipped("c", "the run did not reach it", blockers=[]))
    state = run_state.fold(events)

    assert [(t.id, t.outcome, t.reason, t.blocked) for t in state.tickets] == [
        ("a", "failed", "the gates are red", False),
        ("b", "skipped", "blocked by a, which failed", True),
        ("c", "skipped", "the run did not reach it", False),
    ]


def test_the_newest_skip_of_a_ticket_decides_whether_a_blocker_skipped_it():
    events = journal(skipped("b"), skipped("b", "the run did not reach it", blockers=[]))

    assert run_state.fold(events).ticket("b").blocked is False


def test_a_step_start_takes_a_skipped_ticket_out_of_the_skipped_tickets():
    events = journal(skipped("b"), started("b"))

    assert (run_state.fold(events).ticket("b").outcome, run_state.fold(events).ticket("b").blocked) == ("open", False)
    assert run_state.fold(events).open_ticket == "b"


def test_a_ticket_that_a_resume_runs_again_after_a_crash_is_the_open_ticket_once_more():
    events = journal(started("a"), ev("crashed", ticket="a", phase="specialist", error="E", message="m"), ev("resume"), started("a"))

    assert run_state.fold(events).open_ticket == "a"


def test_a_plan_change_sets_the_tickets_of_the_state_to_its_order():
    events = journal(started("a"), built("a"), plan_change(["a", "c", "d"], removed=["b"], added=["d"]))

    assert outcomes(events) == {"a": "built", "c": "pending", "d": "pending"}


def test_without_a_plan_change_the_tickets_of_run_start_are_the_plan():
    assert list(outcomes(journal(tickets=("x", "y")))) == ["x", "y"]


def test_an_earlier_journal_with_no_schema_and_a_crash_in_a_step_end_gives_a_failed_ticket_and_an_ended_run():
    events = journal(
        started("a"), ev("step-end", ticket="a", state="failed", reason="the step crashed: OSError: disk full"),
        skipped("b", "blocked by a, which failed"),
        ev("run-end", result="failed", built=[], failed=["a"], skipped=["b"]),
        tickets=("a", "b"), versioned=False,
    )
    state = run_state.fold(events)

    assert "schema" not in events[0] and "plan" not in events[0]
    assert [(t.id, t.outcome, t.reason) for t in state.tickets] == [
        ("a", "failed", "the step crashed: OSError: disk full"), ("b", "skipped", "blocked by a, which failed"),
    ]
    assert (state.status.kind, state.status.event["result"]) == ("ended", "failed")  # type: ignore[index]


def test_a_retry_of_a_ticket_that_failed_is_marked_until_its_next_step_event():
    retried = journal(started("a"), failed("a"), ev("retry", ticket="a", phase="specialist", round=0, reason="x"))
    ended_again = journal(started("a"), failed("a"), ev("retry", ticket="a", phase="specialist", round=0, reason="x"), failed("a"))

    assert run_state.fold(retried[:3]).ticket("a").retrying is False
    assert run_state.fold(retried).ticket("a").retrying is True
    assert run_state.fold(retried).ticket("a").outcome == "failed"  # the engine keeps the old step-end until the retry ends
    assert run_state.fold(ended_again).ticket("a").retrying is False


def test_the_run_state_is_a_frozen_value():
    state = run_state.fold(journal())

    with pytest.raises(dataclasses.FrozenInstanceError):
        state.open_ticket = "a"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.tickets[0].outcome = "built"  # type: ignore[misc]


def test_the_run_state_at_an_earlier_time_is_the_run_state_of_the_events_up_to_that_time():
    events = journal(started("a"), built("a"), started("b"), failed("b"))

    assert outcomes(events[:3]) == {"a": "built", "b": "pending", "c": "pending"}
    assert outcomes(events[:4]) == {"a": "built", "b": "open", "c": "pending"}
    assert outcomes(events) == {"a": "built", "b": "failed", "c": "pending"}


def test_events_that_hold_no_ticket_outcome_change_no_ticket():
    events = journal(started("a"), ev("gate-start", ticket="a", phase="build", command="t", gate_log="/l"), ev("agent-start", ticket="a", role="specialist"))

    assert outcomes(events) == {"a": "open", "b": "pending", "c": "pending"}
