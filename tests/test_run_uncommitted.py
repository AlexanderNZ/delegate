"""A gate result is a fact about the commit that it names (#176).

The gate runs in the worktree as it is. A file that the specialist did not
commit can make a gate green that is red on the commit. So before a gate runs,
the engine reads the changes of the worktree that are not committed. When there
is one, it runs no gate and continues the specialist with the trigger
`uncommitted`. Each case runs `delegate run` end to end, with a scripted
specialist, on a real temporary git repository.
"""

import json
from dataclasses import dataclass

import pytest

from delegate import adapters
from delegate.ports.harness import AdapterResult
from delegate.adapters.git import GitVersionControl
from delegate.adapters.shell import ShellGateRunner
from delegate.run.engine import run_workflow
from delegate.run.workflow import load_workflow
from delegate.shared.tiers import load_tiers

from .support import ScriptedAdapter, WORKFLOW, commit_head, git, journal_path, kill_a_run, make_repo, read_journal_file, tree, valid_report

GATE = "test -f feature.txt"


@dataclass
class ForgetfulAdapter(ScriptedAdapter):
    """A specialist that writes `feature.txt` and `other.txt`, and commits only `other.txt`.

    Its report says `committed`. On each continuation it commits `feature.txt`
    when `repair` is set, and else it does nothing.
    """

    repair: bool = True

    def run(self, request):
        if request.agent.endswith("verifier"):
            return super().run(request)
        if self.on_specialist is not None:
            self.on_specialist(request)
        is_continuation = "## Continuation" in request.prompt
        (self.continuation_calls if is_continuation else self.calls).append(request)
        if not is_continuation:
            (request.cwd / "feature.txt").write_text("feature\n")
            (request.cwd / "other.txt").write_text("other\n")
            git(request.cwd, "add", "other.txt")
            head, branch = commit_head(request.cwd, "-m", "work without feature.txt")
        elif self.repair and len(self.continuation_calls) == 1:
            git(request.cwd, "add", "feature.txt")
            head, branch = commit_head(request.cwd, "-m", "add feature.txt")
        elif self.repair:
            (request.cwd / "done.txt").write_text("done\n")
            git(request.cwd, "add", "done.txt")
            head, branch = commit_head(request.cwd, "-m", "add done.txt")
        else:
            head, branch = git(request.cwd, "rev-parse", "HEAD", "--abbrev-ref", "HEAD").split()
        request.report_path.parent.mkdir(parents=True, exist_ok=True)
        request.report_path.write_text(json.dumps(valid_report("a", branch, head)))
        request.event_stream.write_text('{"type":"result"}\n')
        return AdapterResult(0, "finished", self.session_id, request.event_stream)


@pytest.fixture
def forgetful(git_identity):
    adapter = ForgetfulAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


class SpyGates:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, command, worktree, log=None, timeout=None, environment=None):
        self.calls.append(command)
        return ShellGateRunner().run(command, worktree, log=log, timeout=timeout, environment=environment)


def start_run(repo, adapter, gates, **options):
    tiers = load_tiers()
    workflow_path = repo / "workflow.toml"
    workflow = load_workflow(workflow_path, tiers, adapters.registered_names())
    return run_workflow(workflow, workflow_path, repo, tiers, adapter, GitVersionControl(), gates, **options)


def test_a_file_that_the_specialist_did_not_commit_does_not_make_a_gate_green_on_the_commit(tmp_path, forgetful):
    repo = make_repo(tmp_path, WORKFLOW)
    gates = SpyGates()

    result = start_run(repo, forgetful, gates)

    events = read_journal_file(repo, result.run_id)
    # The engine ran no gate in the worktree with the change, and continued the specialist with the new trigger.
    (continuation,) = [e for e in events if e["event"] == "continuation"]
    assert continuation["trigger"] == "uncommitted"
    assert continuation["count"] == 1
    assert "feature.txt" in continuation["reason"]
    assert "other.txt" not in continuation["reason"]
    (call,) = forgetful.continuation_calls
    assert "feature.txt" in call.prompt
    # The gate ran once, on the commit that holds `feature.txt`.
    results = [e for e in events if e["event"] == "gate-result"]
    assert [(e["phase"], e["green"], e["clean"]) for e in results] == [("build", True, True)]
    assert gates.calls == [GATE]
    assert results[0]["commit"] == git(repo, "rev-parse", "run/demo-a").strip()
    assert "feature.txt" in tree(repo, "run/demo")
    assert result.ok


def test_a_specialist_that_never_commits_the_file_fails_the_step_with_the_path_in_the_reason(tmp_path, forgetful):
    forgetful.repair = False
    repo = make_repo(tmp_path, WORKFLOW)
    gates = SpyGates()

    result = start_run(repo, forgetful, gates)

    assert not result.ok
    assert gates.calls == []
    events = read_journal_file(repo, result.run_id)
    assert [e["trigger"] for e in events if e["event"] == "continuation"] == ["uncommitted", "uncommitted"]
    (limit,) = [e for e in events if e["event"] == "continuation-limit"]
    assert limit["trigger"] == "uncommitted"
    (end,) = [e for e in events if e["event"] == "step-end" and e["state"] == "failed"]
    assert "feature.txt" in end["reason"] and "continuation limit" in end["reason"]
    assert "feature.txt" not in tree(repo, "run/demo")


def test_a_worktree_that_holds_only_ignored_files_runs_the_gates_as_before(tmp_path, scripted):
    repo = make_repo(tmp_path, WORKFLOW)
    (repo / ".gitignore").write_text("*.log\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ignore logs")
    scripted.files_by_ticket = {"a": {"feature.txt": "feature\n"}}
    scripted.on_specialist = lambda request: (request.cwd / "debug.log").write_text("log\n")
    gates = SpyGates()

    result = start_run(repo, scripted, gates)

    assert result.ok and scripted.continuation_calls == []
    events = read_journal_file(repo, result.run_id)
    assert "continuation" not in [e["event"] for e in events]
    assert [(e["phase"], e["green"], e["clean"]) for e in events if e["event"] == "gate-result"] == [("build", True, True)]
    assert gates.calls == [GATE]


def test_a_green_gate_result_with_no_clean_field_does_not_make_a_later_gate_a_skip(tmp_path, scripted):
    # A journal of an older version holds a green result with no `clean` field. A resume must run the gate again.
    listing = WORKFLOW.replace(f'gates = ["{GATE}"]', 'gates = ["ls *.txt"]')
    repo = make_repo(tmp_path, listing)
    run_id, _ = kill_a_run(repo, 2)  # killed as the verifier starts: the build gate is green, and no verdict
    path = journal_path(repo, run_id)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    for event in lines:
        if event["event"] == "gate-result":
            event.pop("clean", None)
    path.write_text("".join(json.dumps(event) + "\n" for event in lines))
    gates = SpyGates()

    result = start_run(repo, scripted, gates, resume=run_id, break_lock=True)

    assert result.ok
    assert gates.calls == ["ls *.txt"]
    events = read_journal_file(repo, run_id)
    assert not [e for e in events if e["event"] == "gate-skip" and e["phase"] == "build"]


def test_in_economy_mode_a_file_that_the_specialist_did_not_commit_gets_a_continuation_before_the_chain_gates(tmp_path, forgetful):
    repo = make_repo(tmp_path, WORKFLOW.replace('mode = "assure"', 'mode = "economy"'))
    gates = SpyGates()

    result = start_run(repo, forgetful, gates)

    events = read_journal_file(repo, result.run_id)
    (continuation,) = [e for e in events if e["event"] == "continuation"]
    assert (continuation["trigger"], continuation["limit"]) == ("uncommitted", 1)
    assert "feature.txt" in continuation["reason"]
    assert result.ok
    assert [(e["phase"], e["clean"]) for e in events if e["event"] == "gate-result"] == [("chain", True)]
    assert "feature.txt" in tree(repo, "run/demo")


def test_a_file_that_a_red_gate_left_is_no_change_of_the_specialist(tmp_path, forgetful):
    # The gate writes `junk.tmp` on each run, which git does not ignore. It is red until `done.txt` is committed.
    # The first continuation commits `feature.txt`, the second commits `done.txt`, and `junk.tmp` stays untracked.
    gate = "echo x > junk.tmp; test -f feature.txt && test -f done.txt"
    repo = make_repo(tmp_path, WORKFLOW.replace(f'gates = ["{GATE}"]', f'gates = ["{gate}"]'))
    gates = SpyGates()

    result = start_run(repo, forgetful, gates)

    events = read_journal_file(repo, result.run_id)
    assert [e["trigger"] for e in events if e["event"] == "continuation"] == ["uncommitted", "gates-red"]
    assert "that is not committed" not in forgetful.continuation_calls[1].prompt
    assert result.ok
    assert "done.txt" in tree(repo, "run/demo") and "junk.tmp" not in tree(repo, "run/demo")


def test_a_fixup_that_leaves_a_file_uncommitted_runs_no_gate_and_fails_the_step_with_the_path(tmp_path, scripted):
    # The verifier rejects. The fix-up specialist commits its fix, and leaves `forgotten.txt` in the worktree.
    scripted.files_by_ticket = {"a": {"feature.txt": "feature\n"}}
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = ["feature.txt is wrong."]

    def leave_a_file(request, head):
        (request.cwd / "forgotten.txt").write_text("forgotten\n")
        return json.dumps(valid_report("a", "run/demo-a", head))

    scripted.fixup_report_text = leave_a_file
    repo = make_repo(tmp_path, WORKFLOW)
    gates = SpyGates()

    result = start_run(repo, scripted, gates)

    assert not result.ok
    assert gates.calls == [GATE]  # the build gate only; the fix-up gates did not run
    assert "forgotten.txt" in result.failures["a"]
    events = read_journal_file(repo, result.run_id)
    assert [e["phase"] for e in events if e["event"] == "gate-result"] == ["build"]
