"""The state of the run, and the current pass of the chain verification, in the run state (#319)."""

from __future__ import annotations

import pytest

from delegate.run import domain, run_state

from .journals import built, ev, journal, plan_change, started

RUN_END = ev("run-end", result="built", built=["a"], failed=[], skipped=[])
PAUSED = ev("paused", ticket="b", before="b")
HALTED = ev("halted", ticket="a", role="verifier", message="the harness failed")
CRASHED = ev("crashed", ticket="a", phase="specialist", error="OSError", message="disk full", traceback="/t")
SETUP = ev("setup-problem", problems=["the gate command is not found"])


def kind(*items) -> str:
    return run_state.fold(journal(*items)).status.kind


def test_a_run_with_no_stop_event_is_active_and_has_no_event_for_its_state():
    state = run_state.fold(journal(started("a")))

    assert (state.status.kind, state.status.event) == ("active", None)


@pytest.mark.parametrize(
    ("item", "expected"),
    [(RUN_END, "ended"), (PAUSED, "paused"), (HALTED, "halted"), (CRASHED, "crashed"), (SETUP, "setup-problem")],
)
def test_each_event_that_stops_a_run_gives_its_state_and_is_the_event_of_the_state(item, expected):
    events = journal(started("a"), item)
    state = run_state.fold(events)

    assert (state.status.kind, state.status.event) == (expected, events[-1])


@pytest.mark.parametrize("item", [PAUSED, HALTED, CRASHED, SETUP, RUN_END])
def test_a_resume_after_the_event_makes_the_run_active_again(item):
    assert kind(started("a"), item, ev("resume")) == "active"


def test_a_stop_event_after_a_resume_stops_the_run_again():
    assert kind(CRASHED, ev("resume"), HALTED) == "halted"


def test_the_newest_stop_event_of_a_kind_is_the_event_of_the_state():
    first = ev("halted", ticket="a", role="verifier", message="first")
    second = ev("halted", ticket="b", role="specialist", message="second")

    assert run_state.fold(journal(first, second)).status.event["message"] == "second"  # type: ignore[index]


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ((PAUSED, RUN_END), "ended"),
        ((HALTED, PAUSED), "paused"),
        ((CRASHED, HALTED), "halted"),
        ((SETUP, CRASHED), "crashed"),
        ((CRASHED, SETUP), "crashed"),
        ((RUN_END, HALTED), "ended"),
    ],
)
def test_when_more_than_one_stop_event_follows_the_newest_resume_the_run_has_the_state_of_the_strongest(items, expected):
    # The order of strength is the order of `delegate status`: ended, paused, halted, crashed, setup problem.
    assert kind(*items) == expected


@pytest.mark.parametrize("trailer", ["cleanup", "lock-broken", "index-warning"])
def test_an_event_that_a_command_writes_after_the_run_end_leaves_the_run_ended(trailer):
    events = journal(started("a"), built("a"), RUN_END, ev(trailer))
    state = run_state.fold(events)

    assert (state.status.kind, state.status.event) == ("ended", events[-2])


def test_a_crash_that_an_earlier_version_recorded_in_a_step_end_gives_an_ended_run_and_no_crashed_state():
    events = journal(
        started("a"), ev("step-end", ticket="a", state="failed", reason="the step crashed: OSError: disk full"),
        ev("run-end", result="failed", built=[], failed=["a"], skipped=[]), versioned=False,
    )

    assert run_state.fold(events).status.kind == "ended"


# The chain verification of an `economy` run.
def verdict(stack: str, text: str):
    return ev("verdict", stack=stack, ticket="c", verdict=text)


def fixup(stack: str, number: int):
    return ev("fixup-start", stack=stack, ticket="c", round=number, rejected_commit="r" * 40)


def chain(*items):
    return run_state.fold(journal(*items, mode="economy")).chain


def stacks_of(found) -> dict[str, tuple[tuple[str, ...], list[object]]]:
    return {s.stack: (s.verdicts, [f["round"] for f in s.fixup_starts]) for s in found.stacks}


def test_a_chain_with_no_event_that_starts_a_pass_has_one_pass_from_the_first_event():
    found = chain(started("a"), verdict("python", "REJECT"), fixup("python", 1), verdict("python", "ACCEPT"))

    assert found.start == 0
    assert stacks_of(found) == {"python": (("REJECT", "ACCEPT"), [1])}
    assert found.advanced is False


def test_a_retry_starts_a_new_pass_and_the_pass_starts_at_the_event_after_the_retry():
    events = journal(started("a"), verdict("python", "REJECT"), ev("retry", ticket="c", phase="chain", round=0, reason="x"), verdict("python", "ACCEPT"), mode="economy")
    found = run_state.fold(events).chain

    assert found.start == 4  # the index of the first event after the retry
    assert events[found.start]["event"] == "verdict"
    assert stacks_of(found) == {"python": (("ACCEPT",), [])}


def test_a_retry_at_the_chain_fixup_goes_on_with_the_pass():
    found = chain(
        fixup("python", 1), verdict("python", "REJECT"), ev("retry", ticket="c", phase="chain-fixup", round=1, reason="x"), fixup("python", 2),
    )

    assert found.start == 0
    assert stacks_of(found) == {"python": (("REJECT",), [1, 2])}


def test_a_plan_change_starts_a_new_pass():
    found = chain(verdict("python", "REJECT"), plan_change(["a", "b", "c"]), fixup("python", 1))

    assert found.start == 3
    assert stacks_of(found) == {"python": ((), [1])}


def test_the_verdicts_and_the_fixup_starts_of_each_stack_are_kept_apart_in_the_order_of_their_first_event():
    found = chain(verdict("web", "ACCEPT"), verdict("python", "REJECT"), fixup("python", 1), verdict("python", "ACCEPT"))

    assert [s.stack for s in found.stacks] == ["web", "python"]
    assert stacks_of(found) == {"web": (("ACCEPT",), []), "python": (("REJECT", "ACCEPT"), [1])}


def test_a_fixup_start_is_the_event_of_the_journal_with_its_rejected_commit():
    found = chain(fixup("python", 1))

    assert found.stacks[0].fixup_starts[0]["rejected_commit"] == "r" * 40


def test_a_run_branch_advance_in_the_pass_is_marked_and_one_before_the_pass_is_not():
    advanced = chain(verdict("python", "ACCEPT"), ev("run-branch-advance", ticket="c"))
    before_retry = chain(ev("run-branch-advance", ticket="c"), ev("retry", ticket="c", phase="chain", round=0, reason="x"))

    assert advanced.advanced is True
    assert before_retry.advanced is False


def test_the_retry_of_a_ticket_that_is_not_the_tip_of_the_chain_starts_the_pass_after_the_retry():
    # #304: `retry --retry a` names a ticket of the first stack, not the tip. The pass still starts after the retry.
    events = journal(
        started("a"), built("a"), started("b"), built("b"), verdict("python", "REJECT"), verdict("web", "ACCEPT"),
        ev("retry", ticket="a", phase="specialist", round=0, reason="x"), started("a"), built("a"), verdict("python", "ACCEPT"),
        mode="economy",
    )
    found = run_state.fold(events).chain

    assert events[found.start]["event"] == "step-start" and events[found.start]["ticket"] == "a"
    assert found.start == 8
    assert stacks_of(found) == {"python": (("ACCEPT",), [])}


def test_starts_chain_verification_is_true_for_a_plan_change_and_a_retry_except_at_the_chain_fixup():
    assert run_state.starts_chain_verification({"event": "plan-change"}) is True
    assert run_state.starts_chain_verification({"event": "retry", "phase": "chain"}) is True
    assert run_state.starts_chain_verification({"event": "retry"}) is True
    assert run_state.starts_chain_verification({"event": "retry", "phase": "chain-fixup"}) is False
    assert run_state.starts_chain_verification({"event": "fixup-start"}) is False


def test_the_phase_of_a_retry_at_the_chain_fixup_is_the_phase_that_the_domain_module_names():
    assert run_state.RETRY_CHAIN_FIXUP == domain.RETRY_CHAIN_FIXUP
