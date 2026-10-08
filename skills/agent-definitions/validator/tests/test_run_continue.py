"""A specialist that ends capped or failed, or whose gates are red, continues in the same worktree.

Each case writes a workflow into a real temporary git repository, registers a
scripted adapter by name, and calls `delegate.main`. The scripted adapter ends
each call in the state that the test gives, so the engine runs end to end with
no model.
"""

import pytest

from agent_definitions import adapters, delegate

from .support import ScriptedAdapter, WORKFLOW, event_names, git, make_repo, read_journal


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys, *extra):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize(("end_state", "exit_status"), [("capped", 0), ("failed", 1)])
def test_a_capped_or_failed_specialist_gets_a_second_invocation_in_the_same_worktree_and_keeps_its_first_commit(
    tmp_path, capsys, scripted, end_state, exit_status
):
    scripted.outcomes = [(end_state, exit_status), ("finished", 0)]
    scripted.continuation_files = [{"second.txt": "second\n"}]
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (first,) = scripted.calls
    (second,) = scripted.continuation_calls
    assert second.cwd == first.cwd
    branch = next(b for b in git(repo, "branch", "--format=%(refname:short)").split() if b.startswith("run/demo-"))
    # Oldest first: seed, the commit of the capped run, the commit of the continuation.
    shas = git(repo, "log", "--reverse", "--format=%H", branch).split()
    assert len(shas) == 3
    assert git(repo, "show", f"{shas[1]}:feature.txt") == "feature\n"
    assert git(repo, "show", f"{branch}:second.txt") == "second\n"
    assert git(repo, "show", f"{branch}:feature.txt") == "feature\n"


# The gate prints its reason in upper case, so the output is not a substring of the command in the brief.
GATE_THAT_SAYS_WHY = 'gates = ["test -f feature.txt || { echo feature.txt is absent | tr a-z A-Z; exit 1; }"]'
RED_GATE_WORKFLOW = WORKFLOW.replace('gates = ["test -f feature.txt"]', GATE_THAT_SAYS_WHY)


def branch_of(repo):
    return next(b for b in git(repo, "branch", "--format=%(refname:short)").split() if b.startswith("run/demo-"))


def test_a_specialist_with_red_gates_continues_in_the_same_worktree_and_the_journal_shows_red_then_green(tmp_path, capsys, scripted):
    scripted.files = {"other.txt": "no feature file\n"}  # the gate looks for feature.txt
    scripted.continuation_files = [{"feature.txt": "feature\n"}]
    repo = make_repo(tmp_path, RED_GATE_WORKFLOW)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (first,) = scripted.calls
    (second,) = scripted.continuation_calls
    assert second.cwd == first.cwd
    gates = [e for e in read_journal(out) if e["event"] == "gate-result" and e["phase"] == "build"]
    assert [g["green"] for g in gates] == [False, True]
    branch = branch_of(repo)
    assert git(repo, "show", f"{branch}:other.txt") == "no feature file\n"
    assert git(repo, "show", f"{branch}:feature.txt") == "feature\n"


def test_the_continuation_brief_of_an_adapter_without_resume_lists_the_commits_so_far_and_the_output_of_the_red_gates(tmp_path, capsys, scripted):
    scripted.files = {"other.txt": "no feature file\n"}
    scripted.continuation_files = [{"feature.txt": "feature\n"}]
    repo = make_repo(tmp_path, RED_GATE_WORKFLOW)

    code, out, err = run(repo, capsys)

    (second,) = scripted.continuation_calls
    first_commit = git(repo, "log", "--reverse", "--format=%H", f"main..{branch_of(repo)}").split()[0]
    assert second.resume_session is None
    assert first_commit in second.prompt
    assert "FEATURE.TXT IS ABSENT" in second.prompt
    # A new agent has no context: the brief holds the task and the report path again.
    assert "The export command writes a CSV file." in second.prompt
    assert str(second.report_path) in second.prompt
