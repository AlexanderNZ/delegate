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
