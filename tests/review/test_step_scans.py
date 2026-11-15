"""Each scan of the events of a step has one function, with a test of its rule (#360).

The engine copied these scans by hand: the newest `agents-changed` that no `agent-start` follows, the commit of the `verify-start`
before a verdict, the place of the newest `run-branch-advance`, and the events of a step. The module `step_scans` holds one function
for each. A case gives a short list of events and asserts the answer that the rule of the engine gives. The expected values come from
the rules of the ticket, not from the code.
"""

from __future__ import annotations

from delegate.run import domain, step_scans

from ..run_state.journals import ev, journal

STOP_SPECIALIST = ev("agents-changed", ticket="a", role="specialist", round=0)
STOP_FIXUP = ev("agents-changed", ticket="a", role="fix-up", round=1)
AGENT = ev("agent-start", ticket="a", role="specialist")


def events_of(*items):
    """The events of a journal with no `run-start`, so that the place of an event is its place in `items`."""
    return journal(*items)[1:]


# --- the newest `agents-changed` that no `agent-start` follows ---


def test_the_newest_stop_with_no_agent_after_it_is_found():
    events = events_of(AGENT, STOP_SPECIALIST)

    assert step_scans.unanswered_stop(events) == 1


def test_a_stop_that_an_agent_start_follows_is_not_found():
    events = events_of(STOP_SPECIALIST, AGENT)

    assert step_scans.unanswered_stop(events) is None


def test_only_the_newest_stop_counts_when_an_older_one_has_no_agent_after_it():
    events = events_of(STOP_SPECIALIST, STOP_FIXUP)

    assert step_scans.unanswered_stop(events) == 1


def test_a_journal_with_no_stop_has_no_unanswered_stop():
    assert step_scans.unanswered_stop(events_of(AGENT)) is None
    assert step_scans.unanswered_stop([]) is None


def test_the_role_makes_an_older_stop_of_that_role_count_when_a_newer_stop_has_another_role():
    events = events_of(STOP_FIXUP, STOP_SPECIALIST)

    assert step_scans.unanswered_stop(events, role="fix-up") == 0
    assert step_scans.unanswered_stop(events) == 1


def test_the_ticket_ignores_the_stops_and_the_agent_starts_of_other_tickets():
    other_agent = ev("agent-start", ticket="b", role="fix-up")
    events = events_of(STOP_FIXUP, other_agent)

    assert step_scans.unanswered_stop(events, ticket="a") == 0
    assert step_scans.unanswered_stop(events) is None


# --- the commit of the `verify-start` before a place ---


def verify(commit, stack=None, ticket="a"):
    fields = {"ticket": ticket, "commit": commit}
    if stack is not None:
        fields["stack"] = stack
    return ev("verify-start", **fields)


def verdict(name, stack=None, ticket="a"):
    fields = {"ticket": ticket, "verdict": name}
    if stack is not None:
        fields["stack"] = stack
    return ev("verdict", **fields)


def test_the_commit_of_the_newest_verify_start_before_the_place_is_found():
    events = events_of(verify("c1"), verdict("REJECT"), verify("c2"), verdict("ACCEPT"), verify("c3"))

    assert step_scans.verify_commit_before(events, 1) == "c1"
    assert step_scans.verify_commit_before(events, 3) == "c2"


def test_no_verify_start_before_the_place_gives_none():
    events = events_of(verdict("REJECT"), verify("c1"))

    assert step_scans.verify_commit_before(events, 0) is None


def test_the_stack_makes_a_verify_start_of_another_stack_not_count():
    events = events_of(verify("c1", "python"), verify("c2", "docs"), verdict("REJECT", "python"))

    assert step_scans.verify_commit_before(events, 2, stack="python") == "c1"
    assert step_scans.verify_commit_before(events, 2) == "c2"


# --- the commit of the newest accepted verdict ---


def test_the_newest_verdict_gives_its_verify_commit_when_it_is_accepted():
    events = events_of(verify("c1"), verdict("REJECT"), verify("c2"), verdict("ACCEPT", ticket="z"))

    found = step_scans.accepted_verdict(events)

    assert found is not None
    assert found.commit == "c2"
    assert found.verdict["ticket"] == "z"


def test_a_newest_verdict_that_is_a_reject_gives_nothing_although_an_older_one_was_accepted():
    events = events_of(verify("c1"), verdict("ACCEPT"), verify("c2"), verdict("REJECT"))

    assert step_scans.accepted_verdict(events) is None


def test_an_accepted_verdict_with_no_verify_start_before_it_gives_nothing():
    assert step_scans.accepted_verdict(events_of(verdict("ACCEPT"), verify("c1"))) is None


def test_a_journal_with_no_verdict_has_no_accepted_verdict():
    assert step_scans.accepted_verdict(events_of(verify("c1"))) is None


def test_the_accepted_word_is_the_word_of_the_domain_module():
    assert step_scans.ACCEPT == domain.ACCEPT


def test_the_commits_of_all_accepted_verdicts_are_listed_with_the_verify_start_that_comes_before_each():
    events = events_of(
        verify("c1"), verdict("ACCEPT"), verify("c2"), verdict("REJECT"), verify("c3"), verdict("ACCEPT"), verdict("ACCEPT"), verdict("ACCEPT"),
    )

    assert step_scans.accepted_commits(events) == ["c1", "c3", "c3", "c3"]


def test_a_verdict_before_any_verify_start_adds_no_commit():
    assert step_scans.accepted_commits(events_of(verdict("ACCEPT"), verify("c1"))) == []


# --- the place of the newest `run-branch-advance` ---


def advance(to_commit, ticket="a"):
    return ev("run-branch-advance", ticket=ticket, to_commit=to_commit, from_commit="base")


def test_the_place_of_the_newest_advance_is_its_place_in_the_events():
    events = events_of(advance("t1"), AGENT, advance("t2"), AGENT)

    assert step_scans.advance_place(events) == 2


def test_a_journal_with_no_advance_has_the_place_minus_one():
    assert step_scans.advance_place(events_of(AGENT)) == -1


def test_the_events_after_the_newest_advance_are_those_that_follow_it():
    events = events_of(advance("t1"), AGENT, advance("t2"), STOP_FIXUP, AGENT)

    assert step_scans.events_after_advance(events) == events[3:]


def test_a_journal_with_no_advance_has_all_its_events_after_the_advance():
    events = events_of(AGENT, STOP_FIXUP)

    assert step_scans.events_after_advance(events) == events


# --- the events of a step ---


def test_the_events_of_a_ticket_are_those_that_name_it():
    other = ev("gate-start", ticket="b")
    run_wide = ev("pause-requested")
    events = events_of(AGENT, other, run_wide, STOP_FIXUP)

    assert step_scans.events_of(events, "a") == [events[0], events[3]]
    assert step_scans.events_of(events, "b") == [events[1]]


def test_the_events_of_the_newest_step_start_at_its_step_start():
    first = ev("step-start", ticket="a")
    second = ev("step-start", ticket="a")
    events = events_of(first, AGENT, ev("step-start", ticket="b"), second, STOP_FIXUP)

    assert step_scans.step_events(events, "a") == [events[3], events[4]]


def test_a_ticket_with_no_step_start_has_all_its_events_as_its_step():
    events = events_of(AGENT, ev("gate-start", ticket="b"), STOP_FIXUP)

    assert step_scans.step_events(events, "a") == [events[0], events[2]]


def test_the_place_of_the_newest_step_start_is_none_when_there_is_none():
    first = ev("step-start", ticket="a")
    events = events_of(first, AGENT, first)

    assert step_scans.step_start_place(events) == 2
    assert step_scans.step_start_place(events_of(AGENT)) is None
