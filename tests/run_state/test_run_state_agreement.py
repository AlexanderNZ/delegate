"""The run state agrees with the two folds that it replaces, except for the three rules where they disagreed (#319).

For each journal of a table, the run state gives the same built, failed, skipped and open tickets as `restore_progress`
(the fold of the engine), and the same state of each ticket and of the run as `snapshot_at` (the fold of the views). The
journals are the events that the engine writes, in the order that it writes them. A journal of the table also agrees at
each point in time: the run state of the first `n` events is the same as the state that the two folds give for them.

The named differences are in the last cases. Each one names the rule of the run state that decides it, and the fold that
read the journal in another way. Since #320 the engine reads the run state (`restore_progress`), so the cases of the
engine fold show that it now agrees with the run state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from delegate.run import run_state, status
from delegate.run.engine import EngineError, restore_progress

from .journals import built, ev, failed, journal, plan_change, skipped, started

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
JOURNAL_FILE = Path("/state/runs/20261114T090000Z-test/journal.jsonl")

RUN_ENDED = ev("run-end", result="failed", built=[], failed=[], skipped=[])
BUILT_END = ev("run-end", result="built", built=[], failed=[], skipped=[])


def chain_gates(ticket: str, number: int):
    """The gates of the chain at `ticket`: a start and a result."""
    return [
        ev("gate-start", ticket=ticket, phase="chain", command="make test", gate_log=f"/l/{number}", name="tests"),
        ev("gate-result", ticket=ticket, phase="chain", command="make test", exit_code=0, passed=True),
    ]


def verifier(ticket: str, text: str):
    return [
        ev("verify-start", ticket=ticket, stack="python", round=0),
        ev("verdict", ticket=ticket, stack="python", verdict=text),
    ]


def fixup(ticket: str, number: int):
    return ev("fixup-start", ticket=ticket, stack="python", round=number, rejected_commit="r" * 40)


# Each journal of the table: its name, its events, its mode, and whether it also agrees at each point in time.
PLAIN = [started("a"), built("a"), started("b"), built("b"), started("c"), built("c"), BUILT_END]
FAILED_AND_SKIP = [
    started("a"), failed("a", "the gates are red"), skipped("b", "blocked by a, which failed"),
    skipped("c", "blocked by b, which was skipped", blockers=["b"]), RUN_ENDED,
]
OPEN_STEP = [started("a"), built("a"), started("b")]
RETRY = [
    *FAILED_AND_SKIP, ev("resume"), ev("unskip", ticket="b", outlook="runs again"), ev("unskip", ticket="c", outlook="runs again"),
    ev("retry", ticket="a", phase="specialist", round=0, reason="the gates are red"), built("a"), started("b"), built("b"),
    started("c"), built("c"), BUILT_END,
]
PLAN_CHANGE = [
    started("a"), failed("a"), skipped("b"), skipped("c"), RUN_ENDED, ev("resume"),
    plan_change(["a", "b", "c", "d"], changed=["b"], added=["d"]), ev("unskip", ticket="b", outlook="runs again"),
    ev("unskip", ticket="c", outlook="runs again"), ev("retry", ticket="a", phase="specialist", round=0, reason="x"), built("a"),
    started("b"), failed("b", "the gates are red"), skipped("c", "blocked by b, which failed", blockers=["b"]),
    skipped("d", "the run did not reach it", blockers=[]), RUN_ENDED,
]
PAUSE = [started("a"), built("a"), ev("paused", ticket="b", before="b")]
PAUSE_AND_RESUME = [*PAUSE, ev("resume"), started("b")]
HALT = [started("a"), ev("halted", ticket="a", role="verifier", message="the harness failed")]
CRASH = [started("a"), ev("crashed", ticket="a", phase="specialist", error="OSError", message="disk full", traceback="/t")]
SETUP = [ev("setup-problem", problems=["the gate command is not found"])]
EARLIER_VERSION_CRASH = [
    started("a"), ev("step-end", ticket="a", state="failed", reason="the step crashed: OSError: disk full"),
    skipped("b", "blocked by a, which failed"), skipped("c", "blocked by a, which failed"), RUN_ENDED,
]
ECONOMY = [
    started("a"), built("a"), started("b"), built("b"), started("c"), built("c"),
    *chain_gates("c", 1), *verifier("c", "REJECT"), fixup("c", 1), *chain_gates("c", 2), *verifier("c", "REJECT"),
    failed("c", "the verifier rejected the chain"),
    RUN_ENDED, ev("resume"), ev("retry", ticket="c", phase="chain", round=0, reason="the verifier rejected the chain", revived=["c"]),
    *chain_gates("c", 3), *verifier("c", "ACCEPT"), ev("run-branch-advance", ticket="c", commit="c" * 40), built("c"), BUILT_END,
]

# The journals of the table: the name, the events, the mode, and whether the journal agrees at each point in time. A journal with
# an `unskip` or a `plan-change` agrees at its end only. Between a `plan-change` and the `unskip`, the view fold reads a changed
# ticket as skipped (rule 1 below). Before #320 the engine fold also read a ticket as skipped between its `unskip` and its
# `step-start` (rule 2 below).
TABLE = [
    ("a plain run", PLAIN, "assure", True),
    ("a failed ticket and a skip", FAILED_AND_SKIP, "assure", True),
    ("a step that is open", OPEN_STEP, "assure", True),
    ("a retry", RETRY, "assure", False),
    ("a plan change", PLAN_CHANGE, "assure", False),
    ("a pause", PAUSE, "assure", True),
    ("a pause and a resume", PAUSE_AND_RESUME, "assure", True),
    ("a halt", HALT, "assure", True),
    ("a crash", CRASH, "assure", True),
    ("a stop by the check of the setup", SETUP, "assure", True),
    ("a crash that an earlier version recorded in a step-end", EARLIER_VERSION_CRASH, "assure", True),
    ("an economy chain with a fix-up and a chain retry", ECONOMY, "economy", True),
]


def of(items, mode: str, count: int | None = None):
    events = journal(*items, mode=mode)
    return events if count is None else events[:count]


def snapshot(events):
    return status.snapshot_at(events, JOURNAL_FILE, lambda adapter: None, NOW, live_process=True, pause_request=False)


def points(items, mode: str, every: bool):
    """The events of the journal up to each point in time, or the whole journal when it agrees at its end only."""
    total = len(items) + 1
    return [of(items, mode, count) for count in (range(1, total + 1) if every else [total])]


def cases():
    return [(name, events) for name, items, mode, every in TABLE for events in points(items, mode, every)]


def test_the_run_state_gives_the_same_tickets_as_restore_progress():
    for name, events in cases():
        state = run_state.fold(events)
        plan = [t.id for t in state.tickets]
        progress = restore_progress(events, "run", retrying=True)

        assert [t.id for t in state.tickets if t.outcome == "built"] == [t for t in progress.built if t in plan], name
        assert [t.id for t in state.tickets if t.outcome == "failed"] == [t for t in progress.failed if t in plan], name
        assert {t.id: t.reason or "the step failed" for t in state.tickets if t.outcome == "failed"} == {
            t: r for t, r in progress.failures.items() if t in plan
        }, name
        assert {t.id: t.reason for t in state.tickets if t.outcome == "skipped"} == {
            t: r for t, r in progress.skipped.items() if t in plan
        }, name
        assert {t.id for t in state.tickets if t.outcome == "skipped" and t.blocked} == {
            t for t in progress.blocked if t in plan and t in progress.skipped
        }, name
        assert state.open_ticket == progress.open_ticket, name


def test_the_run_state_gives_the_same_state_of_each_ticket_as_snapshot_at():
    for name, events in cases():
        state = run_state.fold(events)
        snap = snapshot(events)

        # A view shows a ticket with a `retry` as running, and the engine keeps its outcome until the retry ends.
        mine = {t.id: "running" if t.retrying or t.outcome == "open" else t.outcome for t in state.tickets}
        assert mine == {t.id: t.state for t in snap.tickets}, name
        assert {t.id: t.reason for t in state.tickets if t.outcome in ("failed", "skipped") and not t.retrying} == {
            t.id: t.reason for t in snap.tickets if t.state in ("failed", "skipped")
        }, name


def test_the_run_state_gives_the_same_state_of_the_run_as_snapshot_at():
    wanted = {"active": "running", "paused": "paused", "halted": "halted", "crashed": "stopped", "setup-problem": "stopped"}
    for name, events in cases():
        state = run_state.fold(events)

        expected = str(state.status.event["result"]) if state.status.kind == "ended" else wanted[state.status.kind]  # type: ignore[index]
        assert snapshot(events).state == expected, name


def test_the_current_pass_of_the_chain_agrees_with_the_chain_rows_of_snapshot_at():
    events = of(ECONOMY, "economy")
    state = run_state.fold(events)
    rows = {row.stack: row for row in snapshot(events).chains}

    assert [s.stack for s in state.chain.stacks] == ["python"]
    assert state.chain.stacks[0].verdicts == ("ACCEPT",)
    assert rows["python"].state == "accepted" and rows["python"].verdict == "ACCEPT"
    assert state.chain.advanced is True
    # The pass before the chain retry had one fix-up round. The chain row of the new pass has none.
    assert rows["python"].fixup_round == len(state.chain.stacks[0].fixup_starts) == 0
    before_retry = run_state.fold(events[: events.index(next(e for e in events if e["event"] == "retry"))])
    assert before_retry.chain.stacks[0].verdicts == ("REJECT", "REJECT")
    assert [f["round"] for f in before_retry.chain.stacks[0].fixup_starts] == [1]


def test_the_fixup_round_of_a_chain_row_is_the_newest_fixup_start_of_the_pass():
    events = of(ECONOMY, "economy", 12)
    state = run_state.fold(events)
    rows = {row.stack: row for row in snapshot(events).chains}

    assert events[-1]["event"] == "fixup-start"
    assert rows["python"].fixup_round == max(f["round"] for f in state.chain.stacks[0].fixup_starts) == 1  # type: ignore[type-var]


# The three rules. In each case the run state is the right reading, and the case names the fold that read the journal in another way.
def test_rule_1_a_plan_change_that_a_crash_cut_off_before_the_next_step_start_the_engine_reads_a_changed_ticket_as_not_skipped():
    events = journal(started("a"), failed("a"), skipped("b"), RUN_ENDED, ev("resume"), plan_change(["a", "b"], changed=["b"]))

    progress = restore_progress(events, "run", retrying=True)
    assert (progress.skipped, progress.failed) == ({}, ["a"])  # the engine fold: b is pending (it read b as skipped before #320)
    assert run_state.fold(events).ticket("b").outcome == "pending"  # the run state: the plan change took b out of the skipped tickets


def test_rule_1_the_views_read_a_changed_ticket_as_skipped_until_the_unskip_that_the_engine_writes_after_the_plan_change():
    events = journal(started("a"), failed("a"), skipped("b"), RUN_ENDED, ev("resume"), plan_change(["a", "b"], changed=["b"]))

    assert {t.id: t.state for t in snapshot(events).tickets} == {"a": "failed", "b": "skipped"}  # the view fold
    assert run_state.fold(events).ticket("b").outcome == "pending"


def test_rule_2_the_engine_reads_a_ticket_with_an_unskip_as_pending_before_its_step_start():
    events = journal(started("a"), failed("a"), skipped("b"), RUN_ENDED, ev("resume"), ev("unskip", ticket="b", outlook="runs again"))

    progress = restore_progress(events, "run", retrying=True)
    assert (progress.skipped, progress.blocked) == ({}, set())  # the engine fold: pending (it read b as skipped before #320)
    assert {t.id: t.state for t in snapshot(events).tickets}["b"] == "pending"  # the view fold: pending
    assert run_state.fold(events).ticket("b").outcome == "pending"  # the run state: pending, as the view fold reads it


def test_rule_3_the_engine_reads_a_run_end_before_a_resume_as_no_end_of_the_run_also_when_no_plan_change_followed_it():
    events = journal(started("a"), failed("a"), RUN_ENDED, ev("resume"), started("a"))

    # The engine fold: the run goes on with the open step (before #320 it read the run as ended, and refused the resume).
    assert restore_progress(events, "run").open_ticket == "a"
    with pytest.raises(EngineError, match="has ended with the result 'failed'"):
        restore_progress(events[:-2], "run")  # a run-end that no resume follows ends the run
    assert snapshot(events).state == "running"  # the view fold: a resume came after the run-end
    assert run_state.fold(events).status.kind == "active"  # the run state: a resume came after the run-end


def test_the_plan_change_of_the_table_agrees_at_its_end_after_the_unskip_that_the_engine_writes():
    events = of(PLAN_CHANGE, "assure")
    state = run_state.fold(events)

    assert [(t.id, t.outcome) for t in state.tickets] == [("a", "built"), ("b", "failed"), ("c", "skipped"), ("d", "skipped")]
    assert [(t.id, t.state) for t in snapshot(events).tickets] == [("a", "built"), ("b", "failed"), ("c", "skipped"), ("d", "skipped")]
