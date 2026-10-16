"""The engine runs a gate at most once green on one commit.

In `assure` mode the engine runs the gates after the build and again after the
rebase onto the run branch. When the rebase keeps the commit, or a resume goes
on with a commit whose gates the journal holds green, the engine does not run
those gates again. It writes `gate-skip` with the commit and the green result
that it relies on. Each case hands `run_workflow` a gate runner that records
each call, on a real temporary git repository. The gate runner stands at a
system boundary: it starts a process.
"""

import pytest

from delegate import adapters
from delegate.adapters.git import GitVersionControl
from delegate.adapters.shell import ShellGateRunner
from delegate.run.engine import run_workflow
from delegate.run.workflow import load_workflow
from delegate.shared.tiers import load_tiers

from .support import event_names, git, kill_a_run, make_repo, read_journal_file, tree
from .test_run_multi import HEAD, ticket

GATE = "ls *.txt"


class SpyGates:
    """A gate runner that records the ticket worktree of each call and runs the command in the real shell."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def run(self, command, worktree, log=None):
        self.calls.append((command, worktree.name))
        return ShellGateRunner().run(command, worktree, log=log)


def start_run(repo, adapter, gates, **options):
    tiers = load_tiers()
    workflow_path = repo / "workflow.toml"
    workflow = load_workflow(workflow_path, tiers, adapters.registered_names())
    return run_workflow(workflow, workflow_path, repo, tiers, adapter, GitVersionControl(), gates, **options)


def of_ticket(events, ticket_id):
    return [e for e in events if e.get("ticket") == ticket_id]


@pytest.mark.parametrize("ticket_start", [None, "run-branch"])
def test_a_rebase_that_keeps_the_commit_runs_each_gate_once_and_the_journal_records_the_skip(tmp_path, scripted, ticket_start):
    # The run branch starts at main, and ticket a starts from main or from the run branch, so the rebase keeps its commit.
    workflow = HEAD + ticket("a")
    if ticket_start:
        workflow = f'ticket-start = "{ticket_start}"\n' + workflow
    repo = make_repo(tmp_path, workflow)
    gates = SpyGates()

    result = start_run(repo, scripted, gates)

    assert result.ok
    assert gates.calls == [(GATE, "a")]
    events = read_journal_file(repo, result.run_id)
    commit = git(repo, "rev-parse", "run/demo-a").strip()
    rebase = next(e for e in events if e["event"] == "rebase")
    assert rebase["from_commit"] == rebase["to_commit"] == commit
    (green,) = [e for e in events if e["event"] == "gate-result"]
    assert (green["phase"], green["command"], green["commit"], green["green"]) == ("build", GATE, commit, True)
    (skip,) = [e for e in events if e["event"] == "gate-skip"]
    assert (skip["ticket"], skip["phase"], skip["round"], skip["command"], skip["commit"]) == ("a", "rebase", 0, GATE, commit)
    assert skip["green_seq"] == green["seq"]
    assert commit in skip["reason"] and "green" in skip["reason"]
    names = event_names(events)
    assert names.index("rebase") < names.index("gate-skip") < names.index("verify-start")
    assert git(repo, "rev-parse", "run/demo").strip() == commit


def test_a_rebase_that_changes_the_commit_runs_the_gates_again(tmp_path, scripted):
    # Ticket b starts from main. The rebase puts the accepted work of ticket a under it, so its commit changes.
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))
    gates = SpyGates()

    result = start_run(repo, scripted, gates)

    assert result.ok
    assert gates.calls == [(GATE, "a"), (GATE, "b"), (GATE, "b")]
    events = read_journal_file(repo, result.run_id)
    mine = of_ticket(events, "b")
    rebase = next(e for e in mine if e["event"] == "rebase")
    assert rebase["from_commit"] != rebase["to_commit"]
    results = [(e["phase"], e["commit"], e["green"]) for e in mine if e["event"] == "gate-result"]
    assert results == [("build", rebase["from_commit"], True), ("rebase", rebase["to_commit"], True)]
    assert "gate-skip" not in event_names(mine)


def test_a_gate_that_was_red_on_the_commit_runs_again(tmp_path, scripted):
    # The gate is red on the first commit of ticket a. The continuation adds a commit, and the gate is green on it.
    flag = "fixed.txt"
    workflow = HEAD.replace(f'gates = ["{GATE}"]', f'gates = ["test -f {flag}"]') + ticket("a")
    scripted.continuation_files = [{flag: "fixed\n"}]
    repo = make_repo(tmp_path, workflow)
    gates = SpyGates()

    result = start_run(repo, scripted, gates)

    assert result.ok
    assert len(gates.calls) == 2  # the red build, and the build after the continuation; the rebase keeps the commit
    events = read_journal_file(repo, result.run_id)
    results = [(e["phase"], e["green"]) for e in events if e["event"] == "gate-result"]
    assert results == [("build", False), ("build", True)]
    first, second = [e["commit"] for e in events if e["event"] == "gate-result"]
    assert first != second
    (skip,) = [e for e in events if e["event"] == "gate-skip"]
    assert (skip["phase"], skip["commit"]) == ("rebase", second)


def test_a_resume_after_a_stop_at_the_verifier_runs_no_gate_on_the_commit_whose_gates_are_green(tmp_path, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id, _ = kill_a_run(repo, 2)  # killed as the verifier of ticket a starts: the gates are green, and no verdict
    commit = git(repo, "rev-parse", "run/demo-a").strip()
    before = read_journal_file(repo, run_id)
    assert [(e["phase"], e["commit"], e["green"]) for e in before if e["event"] == "gate-result"] == [("build", commit, True)]
    assert event_names(before)[-1] == "agent-start"
    gates = SpyGates()

    result = start_run(repo, scripted, gates, resume=run_id, break_lock=True)

    assert result.ok and result.built == ["a"]
    assert gates.calls == []
    events = read_journal_file(repo, run_id)
    resumed = events[event_names(events).index("resume"):]
    assert "gate-result" not in event_names(resumed)
    green = next(e for e in before if e["event"] == "gate-result")
    skips = [(e["phase"], e["command"], e["commit"], e["green_seq"]) for e in resumed if e["event"] == "gate-skip"]
    assert skips == [("build", GATE, commit, green["seq"]), ("rebase", GATE, commit, green["seq"])]
    assert len(scripted.verifier_calls) == 1 and scripted.calls == []
    assert "a.txt" in tree(repo, "run/demo")
    assert git(repo, "rev-parse", "run/demo").strip() == commit
