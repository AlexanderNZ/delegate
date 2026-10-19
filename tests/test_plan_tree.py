"""`plan_tree(events)`: the ticket ids of the newest plan of a run, in plan order, each with its depth in the tree (#115).

The tree of the ticket table comes from `blocked_by` in the `plan` field of `run-start`, or of the newest
`plan-change` (#107). A ticket with no blocker has depth 0. A ticket with blockers sits one level under its deepest
blocker. A journal of an earlier version of the engine has no plan, so each ticket has depth 0.

One case reads the journal of a real run with blockers, which the engine writes in a real temporary git repository
with the scripted adapter. The other cases write the events of a journal with the journal class of the engine.
"""

from __future__ import annotations

from delegate.run.journal import read_events
from delegate.run.status import plan_tree

from .support import bare_repo, finished_run, make_repo, run_start, step_end, step_start, write_journal
from .test_run_multi import HEAD, ticket


def plan(*tickets: tuple[str, tuple[str, ...]]) -> list[dict[str, object]]:
    """The `plan` field of a journal event, as the engine records it: each ticket with its stack, blockers and text hash."""
    return [{"id": t, "stack": "python", "blocked_by": list(blockers), "text_sha256": "0" * 64} for t, blockers in tickets]


def test_the_tree_of_a_real_run_gives_each_ticket_the_depth_of_its_blockers(tmp_path, capsys, scripted):
    workflow = HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",)) + ticket("d")
    repo = make_repo(tmp_path, workflow)
    run_id = finished_run(repo, capsys)

    events = read_events(repo / ".git" / "delegate" / "runs" / run_id / "journal.jsonl")

    assert plan_tree(events) == [("a", 0), ("b", 1), ("c", 2), ("d", 0)]


def test_a_ticket_with_two_blockers_sits_under_the_deeper_one(tmp_path):
    start = run_start("r-1", "a", "b", "c", "e")
    start[1]["plan"] = plan(("a", ()), ("b", ("a",)), ("c", ()), ("e", ("c", "b")))
    path = write_journal(bare_repo(tmp_path), "r-1", start)

    assert plan_tree(read_events(path)) == [("a", 0), ("b", 1), ("c", 0), ("e", 2)]


def test_the_newest_plan_change_decides_the_order_and_the_depths(tmp_path):
    start = run_start("r-1", "a", "b")
    start[1]["plan"] = plan(("a", ()), ("b", ("a",)))
    first_change = ("plan-change", {
        "added": ["c"], "changed": [], "fields": [], "removed": [], "order": ["a", "c", "b"], "reviewed": [],
        "plan": plan(("a", ()), ("c", ("a",)), ("b", ("a",))),
    })
    newest_change = ("plan-change", {
        "added": [], "changed": ["b"], "fields": ["blocked-by"], "removed": [], "order": ["a", "c", "b"], "reviewed": ["b"],
        "plan": plan(("a", ()), ("c", ("a",)), ("b", ("c",))),
    })
    path = write_journal(bare_repo(tmp_path), "r-1", start, step_start("a"), step_end("a"), first_change, newest_change)

    assert plan_tree(read_events(path)) == [("a", 0), ("c", 1), ("b", 2)]


def test_an_old_journal_with_no_plan_gives_each_ticket_depth_0_in_the_order_of_its_tickets(tmp_path):
    path = write_journal(bare_repo(tmp_path), "r-old", run_start("r-old", "b", "a", "c"), step_start("b"))

    assert plan_tree(read_events(path)) == [("b", 0), ("a", 0), ("c", 0)]
