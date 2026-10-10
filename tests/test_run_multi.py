"""`delegate run` with several tickets: dependency order, the rebase onto the run branch, and the skip after a failed blocker.

Each case writes a workflow into a real temporary git repository, registers a
scripted adapter by name, and calls `delegate.main`. The scripted adapter writes
a different file for each ticket, so the run branch shows which tickets landed.
"""

import json
from pathlib import Path

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, WORKFLOW, event_names, git, make_repo, read_journal, valid_report

# Every stack gate lists the text files of the worktree, so the gate output shows what the tree held.
GATE_LISTING = 'gates = ["ls *.txt"]'

HEAD = WORKFLOW.split("[[tickets]]")[0].replace('gates = ["test -f feature.txt"]', GATE_LISTING)


def ticket(ticket_id: str, blocked_by: tuple[str, ...] = ()) -> str:
    blockers = ", ".join(f'"{b}"' for b in blocked_by)
    return f'\n[[tickets]]\nid = "{ticket_id}"\ntext = "Ticket {ticket_id} adds its file."\nstack = "python"\nblocked-by = [{blockers}]\n'


@pytest.fixture(autouse=True)
def committer_identity(monkeypatch):
    """The rebase writes commits, so git needs a committer. This sets the environment of the process, which is the real boundary."""
    for key, value in (("NAME", "Scratch"), ("EMAIL", "scratch@example.invalid")):
        monkeypatch.setenv(f"GIT_COMMITTER_{key}", value)
        monkeypatch.setenv(f"GIT_AUTHOR_{key}", value)


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abcd"})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys, *extra):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def tree(repo: Path, branch: str) -> list[str]:
    return git(repo, "ls-tree", "-r", "--name-only", branch).split()


def of_ticket(events, ticket_id):
    return [e for e in events if e.get("ticket") == ticket_id]


def test_tickets_run_in_dependency_order_and_the_run_branch_holds_all_three_when_all_are_accepted(tmp_path, capsys, scripted):
    # The file lists b first, and b waits on a. Ticket c is free.
    repo = make_repo(tmp_path, HEAD + ticket("b", ("a",)) + ticket("c") + ticket("a"))
    base_tip = git(repo, "rev-parse", "main").strip()

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    started = [e["ticket"] for e in events if e["event"] == "step-start"]
    assert sorted(started) == ["a", "b", "c"] and started.index("a") < started.index("b")
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["ACCEPT"] * 3
    run_end = next(e for e in events if e["event"] == "run-end")
    assert (run_end["result"], run_end["built"], run_end["failed"], run_end["skipped"]) == ("built", started, [], [])
    assert {"a.txt", "b.txt", "c.txt", "seed.txt"} <= set(tree(repo, "run/demo"))
    assert git(repo, "rev-list", "--count", f"{base_tip}..run/demo").strip() == "3"  # one linear history
    assert git(repo, "rev-parse", "main").strip() == base_tip


def test_the_journal_shows_a_gate_result_before_and_after_the_rebase_for_each_ticket(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    for ticket_id in ("a", "b"):
        mine = of_ticket(events, ticket_id)
        names = event_names(mine)
        gates = [i for i, name in enumerate(names) if name == "gate-result"]
        assert gates[0] < names.index("rebase") < gates[1] < names.index("verify-start")
        assert [e["phase"] for e in mine if e["event"] == "gate-result"] == ["build", "rebase"]
    b_gates = [e for e in of_ticket(events, "b") if e["event"] == "gate-result"]
    # Ticket b is built from main, so it lacks a.txt until the rebase puts the accepted work of ticket a under it.
    assert b_gates[0]["output_tail"] == "b.txt\nseed.txt\n"
    assert b_gates[1]["output_tail"] == "a.txt\nb.txt\nseed.txt\n"
    rebase = next(e for e in of_ticket(events, "b") if e["event"] == "rebase")
    assert (rebase["result"], rebase["onto_commit"]) == ("rebased", git(repo, "rev-parse", "run/demo-a").strip())
    assert rebase["from_commit"] != rebase["to_commit"] == git(repo, "rev-parse", "run/demo-b").strip()


def test_a_gate_that_is_red_only_after_the_rebase_fails_the_step_before_the_verifier_starts(tmp_path, capsys, scripted):
    # Three text files make the gate red. Ticket b holds two before the rebase (seed.txt, b.txt) and three after it.
    workflow = HEAD.replace(GATE_LISTING, 'gates = ["test $(ls *.txt | wc -l) -lt 3"]') + ticket("a") + ticket("b", ("a",))
    repo = make_repo(tmp_path, workflow)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    events = read_journal(out)
    assert [(e["ticket"], e["phase"], e["green"]) for e in events if e["event"] == "gate-result"] == [
        ("a", "build", True), ("a", "rebase", True), ("b", "build", True), ("b", "rebase", False),
    ]
    assert "ticket b: gates red" in err
    assert [e["ticket"] for e in events if e["event"] == "verify-start"] == ["a"]
    assert git(repo, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "run/demo-a").strip()


def test_when_a_ticket_fails_its_dependants_are_skipped_with_the_reason_and_independent_tickets_still_run(tmp_path, capsys, scripted):
    scripted.failing_tickets = {"a"}
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c") + ticket("d", ("b",)))

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    events = read_journal(out)
    assert [e["ticket"] for e in events if e["event"] == "step-start"] == ["a", "c"]
    skips = {e["ticket"]: e for e in events if e["event"] == "skip"}
    assert sorted(skips) == ["b", "d"]
    assert (skips["b"]["blockers"], skips["b"]["reason"]) == (["a"], "blocked by a, which failed")
    assert (skips["d"]["blockers"], skips["d"]["reason"]) == (["b"], "blocked by b, which was skipped")
    run_end = next(e for e in events if e["event"] == "run-end")
    assert (run_end["result"], run_end["built"], run_end["failed"], run_end["skipped"]) == ("failed", ["c"], ["a"], ["b", "d"])
    assert "c.txt" in tree(repo, "run/demo") and "a.txt" not in tree(repo, "run/demo")
    branches = git(repo, "branch", "--list", "--format=%(refname:short)").split()
    assert "run/demo-b" not in branches and "run/demo-d" not in branches
    assert "ticket b: skipped" in err and "a" in err.split("ticket b: skipped")[1].splitlines()[0]
    assert "ticket a:" in err


def test_a_rebase_conflict_fails_that_step_leaves_the_branch_and_worktree_clean_and_independent_tickets_continue(tmp_path, capsys, scripted):
    scripted.files_by_ticket = {"a": {"shared.txt": "from a\n"}, "c": {"shared.txt": "from c\n"}, "d": {"d.txt": "d\n"}}
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("c") + ticket("d"))
    base_tip = git(repo, "rev-parse", "main").strip()

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    events = read_journal(out)
    conflict = next(e for e in events if e["event"] == "rebase" and e["ticket"] == "c")
    assert (conflict["result"], conflict["files"], conflict["to_commit"]) == ("conflict", ["shared.txt"], None)
    step_end = next(e for e in events if e["event"] == "step-end" and e["ticket"] == "c")
    assert step_end["state"] == "failed" and "rebase" in step_end["reason"] and "shared.txt" in step_end["reason"]
    assert "ticket c:" in err and "shared.txt" in err
    assert [e["ticket"] for e in events if e["event"] == "verify-start"] == ["a", "d"]
    run_end = next(e for e in events if e["event"] == "run-end")
    assert (run_end["built"], run_end["failed"], run_end["skipped"]) == (["a", "d"], ["c"], [])
    assert git(repo, "show", "run/demo:shared.txt") == "from a\n"
    assert "d.txt" in tree(repo, "run/demo")
    # The failed branch keeps its own commit on the base, and its worktree holds no half-done rebase.
    worktree = next(e for e in events if e["event"] == "step-start" and e["ticket"] == "c")["worktree"]
    assert git(repo, "show", "run/demo-c:shared.txt") == "from c\n"
    assert git(repo, "rev-parse", "run/demo-c~1").strip() == base_tip
    assert git(worktree, "status", "--short") == ""
    assert "rebase" not in git(worktree, "status")


def test_a_rebase_that_cannot_start_because_a_tracked_file_is_dirty_fails_only_that_step_and_independent_tickets_continue(tmp_path, capsys, scripted):
    # The specialist of ticket b leaves a tracked file changed and uncommitted, so git refuses to start the rebase.
    def report(request, head):
        own = "b" if "Ticket b" in request.prompt else ("c" if "Ticket c" in request.prompt else "a")
        if own == "b":
            (request.cwd / "seed.txt").write_text("left dirty by the specialist\n")
        return json.dumps(valid_report(own, f"run/demo-{own}", head))

    scripted.report_text = report
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b") + ticket("c"))

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err and "crashed" not in err
    events = read_journal(out)
    refused = next(e for e in events if e["event"] == "rebase" and e["ticket"] == "b")
    assert (refused["result"], refused["files"], refused["to_commit"]) == ("failed", [], None)
    step_end = next(e for e in events if e["event"] == "step-end" and e["ticket"] == "b")
    assert step_end["state"] == "failed" and step_end["reason"].startswith("rebase onto run/demo failed:")
    assert "seed.txt" in step_end["reason"] or "unstaged" in step_end["reason"]
    assert "ticket b:" in err
    assert [e["ticket"] for e in events if e["event"] == "verify-start"] == ["a", "c"]
    run_end = events[-1]
    assert (run_end["event"], run_end["built"], run_end["failed"], run_end["skipped"]) == ("run-end", ["a", "c"], ["b"], [])
    assert {"a.txt", "c.txt"} <= set(tree(repo, "run/demo")) and "b.txt" not in tree(repo, "run/demo")


def test_a_ticket_that_holds_no_commit_beyond_the_run_branch_after_the_rebase_fails_the_step(tmp_path, capsys, scripted):
    # Ticket b writes the same file as ticket a. After the rebase onto the work of a, nothing of b is left.
    scripted.files_by_ticket = {"a": {"feature.txt": "feature\n"}, "b": {"feature.txt": "feature\n"}}
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    assert "ticket b:" in err and "run/demo" in err and "no commit" in err
    assert git(repo, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "run/demo-a").strip()
    events = read_journal(out)
    assert "b" not in [e["ticket"] for e in events if e["event"] == "verify-start"]
    run_end = next(e for e in events if e["event"] == "run-end")
    assert (run_end["built"], run_end["failed"]) == (["a"], ["b"])
