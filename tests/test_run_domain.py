"""The rules of a run, driven with plain values.

These tests need no repository, no harness and no file system. They call the
public functions of the domain module of the run context and assert the value
that each rule gives.
"""

import pytest

from delegate.run import domain


# The modes


def test_a_run_has_two_modes_and_the_assure_mode_is_the_first():
    assert domain.MODES == ("assure", "economy")


@pytest.mark.parametrize(
    ("mode", "role", "tier"),
    [
        ("assure", "specialist", "strong"),
        ("economy", "specialist", "standard"),
        ("assure", "verifier", "verifier"),
        ("economy", "verifier", "verifier"),
    ],
)
def test_the_tier_of_a_role_depends_on_the_mode_for_the_specialist_only(mode, role, tier):
    assert domain.role_tier(mode, role, {}) == tier


def test_a_workflow_override_replaces_the_tier_of_its_role_only():
    overrides = {"specialist": "cheap"}

    assert domain.role_tier("assure", "specialist", overrides) == "cheap"
    assert domain.role_tier("assure", "verifier", overrides) == "verifier"


def test_the_verifier_tier_is_the_same_in_every_mode():
    assert {domain.role_tier(mode, "verifier", {}) for mode in domain.MODES} == {domain.VERIFIER_TIER}


# The limits


@pytest.mark.parametrize(("mode", "rounds"), [("assure", 2), ("economy", 1)])
def test_the_fixup_round_limit_depends_on_the_mode(mode, rounds):
    assert domain.fixup_round_limit(mode) == rounds


@pytest.mark.parametrize(("mode", "count"), [("assure", 2), ("economy", 1)])
def test_the_continuation_limit_depends_on_the_mode(mode, count):
    assert domain.continuation_limit(mode) == count


@pytest.mark.parametrize(
    ("mode", "continuations", "may"),
    [("assure", 0, True), ("assure", 1, True), ("assure", 2, False), ("economy", 0, True), ("economy", 1, False)],
)
def test_a_specialist_continues_until_the_limit_of_the_mode(mode, continuations, may):
    assert domain.may_continue(mode, continuations) is may


def test_the_count_of_a_stopped_run_that_is_beyond_the_limit_does_not_continue_either():
    assert domain.may_continue("economy", 5) is False


# The dependency order


def test_a_ticket_follows_the_tickets_that_block_it():
    tickets = [("c", ["a", "b"]), ("b", ["a"]), ("a", [])]

    assert domain.dependency_order(tickets) == ["a", "b", "c"]


def test_tickets_that_are_ready_together_keep_the_order_of_the_file():
    tickets = [("x", []), ("y", []), ("z", ["x"])]

    assert domain.dependency_order(tickets) == ["x", "y", "z"]


def test_a_ticket_that_waits_is_taken_after_the_tickets_that_were_ready_before_it():
    tickets = [("late", ["first"]), ("first", []), ("other", [])]

    assert domain.dependency_order(tickets) == ["first", "late", "other"]


def test_tickets_that_wait_on_each_other_are_refused_with_their_ids():
    tickets = [("a", ["b"]), ("b", ["a"]), ("c", [])]

    with pytest.raises(ValueError, match=r"\['a', 'b'\] wait on each other"):
        domain.dependency_order(tickets)


def test_no_tickets_have_an_empty_order():
    assert domain.dependency_order([]) == []


# The skip rule


def test_a_ticket_with_every_blocker_built_is_not_skipped():
    assert domain.skip_reason(["a", "b"], built=["a", "b", "c"], failed=[]) is None


def test_a_ticket_with_no_blocker_is_not_skipped():
    assert domain.skip_reason([], built=[], failed=[]) is None


def test_a_ticket_whose_blocker_failed_is_skipped_and_the_reason_names_the_blocker():
    assert domain.skip_reason(["a"], built=[], failed=["a"]) == "blocked by a, which failed"


def test_a_ticket_whose_blocker_was_skipped_is_skipped_and_the_reason_says_so():
    assert domain.skip_reason(["a"], built=[], failed=[]) == "blocked by a, which was skipped"


def test_the_reason_names_each_blocker_that_is_not_built_and_none_that_is():
    reason = domain.skip_reason(["ok", "bad", "gone"], built=["ok"], failed=["bad"])

    assert reason == "blocked by bad, which failed; gone, which was skipped"


def test_the_blockers_that_are_not_built_keep_their_order():
    assert domain.unbuilt_blockers(["c", "a", "b"], built=["a"]) == ["c", "b"]


def test_a_ticket_after_a_halted_run_is_skipped_for_that_reason():
    assert domain.unreached_reason("t1", halted=True) == "the run ended after ticket t1 halted the run"


def test_a_ticket_after_a_crashed_step_is_skipped_for_that_reason():
    assert domain.unreached_reason("t1", halted=False) == "the run ended after ticket t1 crashed"


# The start of a ticket in each mode


def test_in_assure_mode_every_ticket_starts_from_the_base_branch():
    assert domain.chain_predecessor("assure", ["a", "b", "c"], built=["a", "b"]) is None


def test_in_economy_mode_a_ticket_starts_from_the_last_built_ticket_in_plan_order():
    assert domain.chain_predecessor("economy", ["a", "b", "c"], built=["b", "a"]) == "b"


def test_in_economy_mode_a_failed_ticket_is_not_part_of_the_chain():
    assert domain.chain_predecessor("economy", ["a", "b", "c"], built=["a"]) == "a"


def test_in_economy_mode_the_first_ticket_has_no_predecessor():
    assert domain.chain_predecessor("economy", ["a", "b"], built=[]) is None


def test_in_assure_mode_the_work_of_a_ticket_is_counted_from_the_base_branch():
    assert domain.work_starts_at("assure", "main", "abc123") == "main"


def test_in_economy_mode_the_work_of_a_ticket_is_counted_from_where_its_branch_started():
    assert domain.work_starts_at("economy", "main", "abc123") == "abc123"


# The verdict


def test_an_accept_verdict_is_accepted_and_a_reject_is_not():
    assert domain.is_accepted(domain.ACCEPT) is True
    assert domain.is_accepted(domain.REJECT) is False


def test_the_two_verdicts_are_accept_and_reject():
    assert domain.VERDICTS == ("ACCEPT", "REJECT")


def test_a_run_that_starts_at_round_zero_has_every_round_up_to_the_limit():
    assert list(domain.verification_rounds("assure", 0)) == [0, 1, 2]
    assert list(domain.verification_rounds("economy", 0)) == [0, 1]


def test_a_resume_goes_on_at_the_round_the_journal_holds():
    assert list(domain.verification_rounds("assure", 2)) == [2]


def test_a_resume_with_more_verifier_runs_than_the_limit_allows_has_no_rounds_and_a_reason():
    assert list(domain.verification_rounds("economy", 3)) == []
    assert domain.rounds_used_up_reason("economy", 3) == "the 3 verifier runs of the stopped run use up the 1 fix-up rounds"


def test_a_resume_within_the_limit_has_no_reason_to_fail():
    assert domain.rounds_used_up_reason("assure", 2) is None
    assert domain.rounds_used_up_reason("assure", 0) is None


def test_a_reject_after_the_last_round_gives_the_findings_in_the_reason():
    reason = domain.rejection_reason("assure", ["one", "two"])

    assert reason == "the verifier rejected the branch after 2 fix-up rounds: one; two"


def test_the_reason_says_round_in_the_singular_for_one_round():
    assert domain.rejection_reason("economy", ["bad"]) == "the verifier rejected the branch after 1 fix-up round: bad"


def test_a_reject_of_a_chain_gives_its_own_reason_for_each_ticket():
    reason = domain.chain_rejection_reason("economy", ["bad"])

    assert reason == "the verifier rejected the chain after 1 fix-up round: bad"


def test_a_ticket_of_a_stack_after_the_failed_one_is_not_verified():
    reason = domain.unverified_chain_reason("backend")

    assert reason == "not verified: the chain ended when the verification of the stack 'backend' failed"


# The specialist report


def test_a_committed_report_has_no_reason_to_fail():
    assert domain.unfinished_reason("committed", None, "the specialist") is None


def test_a_blocked_report_fails_with_the_status_and_the_reason_it_gives():
    reason = domain.unfinished_reason("blocked", "no access", "the specialist")

    assert reason == "the specialist reported status 'blocked': no access"


def test_a_partial_report_without_a_reason_fails_with_the_status_only():
    assert domain.unfinished_reason("partial", None, "the fix-up specialist") == "the fix-up specialist reported status 'partial'"
