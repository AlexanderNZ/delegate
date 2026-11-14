"""The three rules where the folds of the engine and of the views differed before the run state (#319).

Each case names the rule, and the journal that showed the difference.
"""

from __future__ import annotations

from delegate.run import run_state

from .journals import built, ev, failed, journal, plan_change, skipped, started


def test_rule_1_a_plan_change_takes_a_changed_skipped_ticket_out_of_the_skipped_tickets():
    # A crash after the plan change and before the next step-start: the engine read the ticket as skipped again.
    events = journal(
        started("a"), failed("a"), skipped("b"), skipped("c"), ev("run-end", result="failed", built=[], failed=["a"], skipped=["b", "c"]),
        ev("resume"), plan_change(["a", "b", "c"], changed=["b"]),
    )
    state = run_state.fold(events)

    assert [(t.id, t.outcome, t.blocked) for t in state.tickets] == [("a", "failed", False), ("b", "pending", False), ("c", "skipped", True)]


def test_rule_1_a_plan_change_removes_a_removed_skipped_ticket_from_the_tickets():
    events = journal(started("a"), failed("a"), skipped("b"), skipped("c"), ev("resume"), plan_change(["a", "c"], removed=["b"]))

    assert [(t.id, t.outcome) for t in run_state.fold(events).tickets] == [("a", "failed"), ("c", "skipped")]


def test_rule_1_a_plan_change_leaves_a_changed_ticket_that_is_not_skipped_as_it_is():
    events = journal(started("a"), built("a"), started("b"), failed("b"), ev("resume"), plan_change(["a", "b", "c"], changed=["a", "b"]))

    assert [(t.id, t.outcome) for t in run_state.fold(events).tickets] == [("a", "built"), ("b", "failed"), ("c", "pending")]


def test_rule_1_a_skip_after_the_plan_change_skips_the_ticket_again():
    events = journal(skipped("b"), ev("resume"), plan_change(["a", "b", "c"], changed=["b"]), skipped("b", "blocked by a, which failed"))

    assert run_state.fold(events).ticket("b").outcome == "skipped"


def test_rule_2_an_unskip_makes_the_ticket_pending():
    # The resume wrote `unskip` before the step: `status` read the ticket as pending, the engine as skipped until its step-start.
    events = journal(started("a"), failed("a"), skipped("b"), ev("run-end", result="failed", built=[], failed=["a"], skipped=["b"]), ev("resume"), ev("unskip", ticket="b", outlook="runs again"))
    state = run_state.fold(events)

    assert (state.ticket("b").outcome, state.ticket("b").reason, state.ticket("b").blocked) == ("pending", None, False)


def test_rule_2_a_skip_after_the_unskip_skips_the_ticket_again():
    events = journal(skipped("b"), ev("unskip", ticket="b", outlook="runs again"), skipped("b", "blocked by a, which failed"))

    assert (run_state.fold(events).ticket("b").outcome, run_state.fold(events).ticket("b").blocked) == ("skipped", True)


def test_rule_3_a_run_end_ends_the_run_when_no_resume_comes_after_it():
    events = journal(started("a"), built("a"), ev("run-end", result="built", built=["a"], failed=[], skipped=[]))
    state = run_state.fold(events)

    assert (state.status.kind, state.status.event) == ("ended", events[-1])


def test_rule_3_a_resume_after_the_run_end_makes_the_run_active_again():
    events = journal(ev("run-end", result="failed", built=[], failed=[], skipped=[]), ev("resume"))

    assert run_state.fold(events).status.kind == "active"


def test_rule_3_a_run_end_before_a_plan_change_that_a_resume_wrote_does_not_end_the_run():
    # The engine writes `plan-change` after `resume`, so the rule of `status` and the rule of the engine agree.
    events = journal(ev("run-end", result="failed", built=[], failed=[], skipped=[]), ev("resume"), plan_change(["a", "b", "c"]))

    assert run_state.fold(events).status.kind == "active"


def test_rule_3_a_run_end_after_a_resume_ends_the_run_again():
    events = journal(ev("run-end", result="failed", built=[], failed=[], skipped=[]), ev("resume"), ev("run-end", result="built", built=[], failed=[], skipped=[]))

    state = run_state.fold(events)

    assert (state.status.kind, state.status.event["result"]) == ("ended", "built")  # type: ignore[index]
