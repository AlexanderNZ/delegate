"""The verifier copy is made from the branch whatever the HEAD of the repository is.

Each case uses a real git repository, registers a scripted adapter by name, and
calls `delegate.main`.
"""

import pytest

from delegate import adapters, delegate

from .support import ScriptedAdapter, git, make_repo


@pytest.fixture(autouse=True)
def identity(git_identity):
    """The rebase and the scripted commits need a committer."""


@pytest.fixture
def registered():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_verifier_copy_is_made_and_the_verifier_runs_when_the_repository_head_is_on_the_run_branch(tmp_path, capsys, registered):
    repo = make_repo(tmp_path)
    adapter = registered
    # The coordinator checks out the run branch in the main checkout while the specialist works.
    adapter.on_specialist = lambda request: git(repo, "checkout", "-q", "run/demo")

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (call,) = adapter.verifier_calls
    assert adapter.verifier_heads == [git(repo, "rev-parse", "run/demo-a").strip()]
    assert not call.cwd.exists()
    assert git(repo, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "run/demo-a").strip()

