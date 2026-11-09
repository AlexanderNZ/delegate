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

from .support import state_dir
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
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
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


# `economy` mode (#279): the engine moves the run branch once, to the tip of the chain, after every stack is accepted.
ECONOMY_PLAN = PLAN.replace('mode = "assure"', 'mode = "economy"')


def sessions(adapter) -> int:
    return len(adapter.calls) + len(adapter.verifier_calls) + len(adapter.fixup_calls)


def test_an_economy_resume_after_a_stop_between_the_advance_and_its_event_records_the_advance_as_recovered(tmp_path, adapter):
    repo = make_repo(tmp_path, ECONOMY_PLAN)
    vcs = StopAtAdvance(after=True)
    with pytest.raises(RunInterrupted):
        run(repo, adapter, vcs)
    run_id = only_run_id(repo)
    accepted = git(repo, "rev-parse", "run/demo-b").strip()
    assert git(repo, "rev-parse", "run/demo").strip() == accepted
    assert not [e for e in read_journal_file(repo, run_id) if e["event"] == "run-branch-advance"]
    before = sessions(adapter)

    result = run(repo, adapter, vcs, resume=run_id)

    assert result.ok
    assert sessions(adapter) == before
    events = read_journal_file(repo, run_id)
    advances = [e for e in events if e["event"] == "run-branch-advance"]
    assert [(e["ticket"], e["to_commit"], e.get("recovered")) for e in advances] == [("b", accepted, True)]
    assert advances[0]["from_commit"] == events[0]["base_commit"]
    ends = {e["ticket"]: e["state"] for e in events if e["event"] == "step-end"}
    assert ends == {"a": "built", "b": "built"}
    assert events[-1]["event"] == "run-end" and events[-1]["result"] == "built"
    assert git(repo, "rev-parse", "run/demo").strip() == accepted


def test_an_economy_resume_after_an_accept_whose_commit_the_run_branch_does_not_hold_advances_the_run_branch(tmp_path, adapter):
    repo = make_repo(tmp_path, ECONOMY_PLAN)
    vcs = StopAtAdvance(after=False)
    with pytest.raises(RunInterrupted):
        run(repo, adapter, vcs)
    run_id = only_run_id(repo)
    accepted = git(repo, "rev-parse", "run/demo-b").strip()
    assert git(repo, "rev-parse", "run/demo").strip() != accepted

    result = run(repo, adapter, vcs, resume=run_id)

    assert result.ok
    advances = [e for e in read_journal_file(repo, run_id) if e["event"] == "run-branch-advance"]
    assert [(e["ticket"], e["to_commit"], "recovered" in e) for e in advances] == [("b", accepted, False)]
    assert git(repo, "rev-parse", "run/demo").strip() == accepted


def test_an_economy_resume_of_a_chain_whose_newest_verdict_is_reject_recovers_no_advance(tmp_path, adapter):
    adapter.verdict_sequence = ["REJECT", "ACCEPT"]

    def stop_at_the_fixup(request):
        if "## Findings to fix" in request.prompt:
            adapter.on_specialist = None
            raise KeyboardInterrupt

    adapter.on_specialist = stop_at_the_fixup
    repo = make_repo(tmp_path, ECONOMY_PLAN)
    vcs = GitVersionControl()
    with pytest.raises(RunInterrupted):
        run(repo, adapter, vcs)
    run_id = only_run_id(repo)
    verdicts = [e["verdict"] for e in read_journal_file(repo, run_id) if e["event"] == "verdict"]
    assert verdicts == ["REJECT"]

    result = run(repo, adapter, vcs, resume=run_id)

    assert result.ok
    events = read_journal_file(repo, run_id)
    advances = [e for e in events if e["event"] == "run-branch-advance"]
    assert [(e["ticket"], "recovered" in e) for e in advances] == [("b", False)]
    resumed = next(index for index, e in enumerate(events) if e["event"] == "resume")
    assert [e["verdict"] for e in events[resumed:] if e["event"] == "verdict"] == ["ACCEPT"]
    assert git(repo, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "run/demo-b").strip()


def test_an_economy_resume_with_a_plan_change_records_the_recovered_advance_before_the_new_ticket_is_verified(tmp_path, adapter):
    adapter.files_by_ticket["c"] = {"c.txt": "c\n"}
    repo = make_repo(tmp_path, ECONOMY_PLAN)
    vcs = StopAtAdvance(after=True)
    with pytest.raises(RunInterrupted):
        run(repo, adapter, vcs)
    run_id = only_run_id(repo)
    chain_tip = git(repo, "rev-parse", "run/demo-b").strip()
    (repo / "workflow.toml").write_text(ECONOMY_PLAN + ticket("c", ("b",)))

    result = run(repo, adapter, vcs, resume=run_id, reviewed=("all",))

    assert result.ok
    events = read_journal_file(repo, run_id)
    names = [e["event"] for e in events]
    assert "run-branch-moved" not in names
    advances = [e for e in events if e["event"] == "run-branch-advance"]
    assert [(e["ticket"], e["to_commit"], e.get("recovered")) for e in advances][0] == ("b", chain_tip, True)
    assert names.index("run-branch-advance") < names.index("plan-change")
    assert [(e["ticket"], e["from_commit"], e.get("recovered")) for e in advances[1:]] == [("c", chain_tip, None)]
    assert git(repo, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "run/demo-c").strip()
