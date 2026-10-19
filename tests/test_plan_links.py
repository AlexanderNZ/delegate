"""`plan_links(events)`: each ticket of the newest plan of a run, in plan order, with its blockers and its rail (#143).

The rail and the `after` column of the ticket table come from `blocked_by` in the `plan` field of `run-start`, or of
the newest `plan-change` (#107). The rail joins a row to the row directly above when that row is its one blocker. In
every other case the `after` column names each blocker: a blocker that is not the row above, and each blocker of a
join. A journal of an earlier version of the engine has no plan, so no ticket has a blocker.

One case reads the journal of a real run with blockers, which the engine writes in a real temporary git repository
with the scripted adapter. The other cases write the events of a journal with the journal class of the engine.
"""

from __future__ import annotations

from delegate.run.journal import read_events
from delegate.run.status import plan_links

from .support import bare_repo, finished_run, make_repo, run_start, step_end, step_start, write_journal
from .test_run_multi import HEAD, ticket


def plan(*tickets: tuple[str, tuple[str, ...]]) -> list[dict[str, object]]:
    """The `plan` field of a journal event, as the engine records it: each ticket with its stack, blockers and text hash."""
    return [{"id": t, "stack": "python", "blocked_by": list(blockers), "text_sha256": "0" * 64} for t, blockers in tickets]


def links(events: list[dict[str, object]]) -> list[tuple[str, bool, tuple[str, ...]]]:
    """Each ticket with its rail and the blockers that the `after` column names."""
    return [(link.id, link.rail, link.after) for link in plan_links(events)]


def test_the_links_of_a_real_run_give_a_rail_to_each_ticket_after_the_row_above(tmp_path, capsys, scripted):
    workflow = HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",)) + ticket("d")
    repo = make_repo(tmp_path, workflow)
    run_id = finished_run(repo, capsys)

    events = read_events(repo / ".git" / "delegate" / "runs" / run_id / "journal.jsonl")

    assert links(events) == [("a", False, ()), ("b", True, ()), ("c", True, ()), ("d", False, ())]


def test_a_blocker_that_is_not_the_row_above_and_each_blocker_of_a_join_are_in_after(tmp_path):
    start = run_start("r-1", "a", "b", "c", "e")
    start[1]["plan"] = plan(("a", ()), ("b", ("a",)), ("c", ("a",)), ("e", ("c", "b")))
    path = write_journal(bare_repo(tmp_path), "r-1", start)

    # c is after a, which is not the row above. e is a join: c is the row above, but e has two blockers.
    assert links(read_events(path)) == [("a", False, ()), ("b", True, ()), ("c", False, ("a",)), ("e", False, ("c", "b"))]
    assert [link.blockers for link in plan_links(read_events(path))] == [(), ("a",), ("a",), ("c", "b")]


def test_a_chain_of_40_tickets_has_a_rail_on_each_row_after_the_first(tmp_path):
    ids = [f"t{n}" for n in range(1, 41)]
    start = run_start("r-1", *ids)
    start[1]["plan"] = plan(("t1", ()), *((ticket_id, (above,)) for above, ticket_id in zip(ids, ids[1:])))
    path = write_journal(bare_repo(tmp_path), "r-1", start)

    assert [link.rail for link in plan_links(read_events(path))] == [False] + [True] * 39


def test_the_newest_plan_change_decides_the_order_and_the_links(tmp_path):
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

    assert links(read_events(path)) == [("a", False, ()), ("c", True, ()), ("b", True, ())]


def test_a_blocker_that_is_not_in_the_plan_does_not_count(tmp_path):
    start = run_start("r-1", "a", "b")
    start[1]["plan"] = plan(("a", ()), ("b", ("a", "gone")))
    path = write_journal(bare_repo(tmp_path), "r-1", start)

    assert links(read_events(path)) == [("a", False, ()), ("b", True, ())]


def test_an_old_journal_with_no_plan_gives_no_blocker_and_no_rail_in_the_order_of_its_tickets(tmp_path):
    path = write_journal(bare_repo(tmp_path), "r-old", run_start("r-old", "b", "a", "c"), step_start("b"))

    assert links(read_events(path)) == [("b", False, ()), ("a", False, ()), ("c", False, ())]
