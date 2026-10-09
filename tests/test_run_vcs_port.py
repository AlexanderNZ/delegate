"""The engine sets up a run through the version-control port.

The engine takes the port from its caller, as it takes the harness adapter. Each
case hands `run_workflow` a spy around the real git backend, on a real temporary
repository, and checks what the engine asked of the port and what came of it.
"""

import pytest

from delegate import adapters
from delegate.adapters.git import GitVersionControl
from delegate.engine import EngineError, run_workflow
from delegate.ports.vcs import VcsError
from delegate.tiers import load_tiers
from delegate.workflow import load_workflow

from .support import ScriptedAdapter, git, make_repo


class SpyVcs:
    """The real git backend, and a list of the calls that the engine made on it."""

    def __init__(self, refuse: str | None = None) -> None:
        self._inner = GitVersionControl()
        self._refuse = refuse
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __getattr__(self, name):
        method = getattr(self._inner, name)

        def call(*args):
            self.calls.append((name, args))
            if name == self._refuse:
                raise VcsError(f"{name} refused by the test")
            return method(*args)

        return call


@pytest.fixture
def scripted_adapter(git_identity):
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def start_run(repo, vcs, adapter):
    tiers = load_tiers()
    workflow_path = repo / "workflow.toml"
    workflow = load_workflow(workflow_path, tiers, adapters.registered_names())
    return run_workflow(workflow, workflow_path, repo, tiers, adapter, vcs)


def test_the_run_branch_and_the_ticket_worktree_come_from_the_port(tmp_path, scripted_adapter):
    repo = make_repo(tmp_path)
    vcs = SpyVcs()

    result = start_run(repo, vcs, scripted_adapter)

    assert result.ok
    made = [(name, args[1:]) for name, args in vcs.calls if name == "create_branch"]
    assert made == [("create_branch", ("run/demo", "main"))]
    worktrees = [args for name, args in vcs.calls if name == "ensure_worktree"]
    assert len(worktrees) == 1
    _, worktree, branch, start = worktrees[0]
    assert (branch, start) == ("run/demo-a", "main")
    assert worktree.name == "a" and worktree.is_dir()
    assert git(repo, "rev-parse", "--verify", "refs/heads/run/demo").strip()


def test_the_worktree_of_the_ticket_keeps_the_file_watcher_off(tmp_path, scripted_adapter):
    repo = make_repo(tmp_path)
    vcs = SpyVcs()

    start_run(repo, vcs, scripted_adapter)

    worktree = next(args[1] for name, args in vcs.calls if name == "ensure_worktree")
    assert git(worktree, "config", "--worktree", "core.fsmonitor").strip() == "false"


def test_a_port_that_cannot_make_the_run_branch_stops_the_run_with_an_engine_error(tmp_path, scripted_adapter):
    repo = make_repo(tmp_path)
    vcs = SpyVcs(refuse="create_branch")

    with pytest.raises(EngineError, match="create_branch refused by the test"):
        start_run(repo, vcs, scripted_adapter)

    assert not any(name == "ensure_worktree" for name, _ in vcs.calls)
    assert git(repo, "worktree", "list").strip().count("\n") == 0
