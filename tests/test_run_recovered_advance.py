"""A resume closes an accepted step when the run branch holds its commit (#180).

The engine moves the run branch, and then it writes `run-branch-advance`. A stop between the two leaves a step that
the verifier accepted and whose commit landed. Each case hands `run_workflow` a wrapper around the real git backend,
on a real temporary repository. The wrapper raises `KeyboardInterrupt` just after `fast_forward_branch` returns.
"""

import pytest

from delegate import adapters
from delegate.adapters.git import GitVersionControl
from delegate.adapters.shell import ShellGateRunner
from delegate.run.engine import RunInterrupted, run_workflow
from delegate.run.workflow import load_workflow
from delegate.shared.tiers import load_tiers

from .support import ScriptedAdapter, git, make_repo, read_journal_file
from .test_run_multi import HEAD, ticket

PLAN = HEAD + ticket("a") + ticket("b", ("a",))


class StopAtAdvance:
    """The real git backend. The first `fast_forward_branch` raises Ctrl-C, after it moved the branch or instead of it."""

    def __init__(self, *, after: bool) -> None:
        self._inner = GitVersionControl()
        self._after = after
        self.armed = True

    def __getattr__(self, name):
        method = getattr(self._inner, name)
        if name != "fast_forward_branch":
            return method

        def call(*args, **kwargs):
            if not self.armed:
                return method(*args, **kwargs)
            self.armed = False
            if self._after:
                method(*args, **kwargs)
            raise KeyboardInterrupt

        return call


@pytest.fixture
def adapter(git_identity):
    scripted = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "ab"})
    adapters.register("scripted", scripted)
    yield scripted
    adapters.unregister("scripted")


def run(repo, adapter, vcs, **options):
    tiers = load_tiers()
    path = repo / "workflow.toml"
    workflow = load_workflow(path, tiers, adapters.registered_names())
    return run_workflow(workflow, path, repo, tiers, adapter, vcs, ShellGateRunner(), **options)


def only_run_id(repo) -> str:
    (run_dir,) = (repo / ".git" / "delegate" / "runs").iterdir()
    return run_dir.name


def test_a_resume_after_a_stop_between_the_advance_and_its_event_builds_the_step_as_recovered(tmp_path, adapter):
    repo = make_repo(tmp_path, PLAN)
    vcs = StopAtAdvance(after=True)
    with pytest.raises(RunInterrupted):
        run(repo, adapter, vcs)
    run_id = only_run_id(repo)
    accepted = git(repo, "rev-parse", "run/demo-a").strip()
    assert git(repo, "rev-parse", "run/demo").strip() == accepted
    assert not [e for e in read_journal_file(repo, run_id) if e["event"] == "run-branch-advance"]

    result = run(repo, adapter, vcs, resume=run_id)

    assert result.ok
    events = read_journal_file(repo, run_id)
    advances = [e for e in events if e["event"] == "run-branch-advance"]
    assert [(e["ticket"], e["to_commit"], e.get("recovered")) for e in advances][0] == ("a", accepted, True)
    assert [e.get("recovered") for e in advances if e["ticket"] == "b"] == [None]
    ends = {e["ticket"]: e["state"] for e in events if e["event"] == "step-end"}
    assert ends == {"a": "built", "b": "built"}
    assert events[-1]["event"] == "run-end" and events[-1]["result"] == "built"
    assert sorted(git(repo, "ls-tree", "-r", "--name-only", "run/demo").split()) >= ["a.txt", "b.txt"]


def test_a_resume_after_an_accept_whose_commit_the_run_branch_does_not_hold_advances_the_run_branch(tmp_path, adapter):
    repo = make_repo(tmp_path, PLAN)
    vcs = StopAtAdvance(after=False)
    with pytest.raises(RunInterrupted):
        run(repo, adapter, vcs)
    run_id = only_run_id(repo)
    assert git(repo, "rev-parse", "run/demo").strip() != git(repo, "rev-parse", "run/demo-a").strip()

    result = run(repo, adapter, vcs, resume=run_id)

    assert result.ok
    events = read_journal_file(repo, run_id)
    advances = [e for e in events if e["event"] == "run-branch-advance"]
    assert [(e["ticket"], "recovered" in e) for e in advances] == [("a", False), ("b", False)]
    assert git(repo, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "run/demo-b").strip()
