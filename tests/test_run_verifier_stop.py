"""A stop during the verifier session does not hide a change that the verifier made to the real worktree (#192).

The engine records the HEAD and the content of the changed paths of the real
worktree in `verify-start`. A resume of a step whose newest `verify-start` has no
`verify-result` compares the worktree with that record before it does anything
else. A difference halts the run, as it does after a session that ended.

Each case runs `delegate.main` against a real temporary git repository with the
scripted adapter. The scripted verifier raises the interrupt of Ctrl-C after it
changed the worktree, which leaves what a stop leaves: a journal with no
`run-end`, and an open step.
"""

import json

import pytest

from delegate import adapters

from .support import ScriptedAdapter, event_names, git, journal_path, make_repo, read_journal_file, resume_main, run_main
from .test_run_multi import HEAD, ticket


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter(files_by_ticket={"a": {"a.txt": "a\n"}})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def start(repo, capsys):
    return run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))


def run_id_of(repo) -> str:
    (run_dir,) = (repo / ".git" / "delegate" / "runs").iterdir()
    return run_dir.name


def real_worktree(repo):
    (run_dir,) = (repo / ".git" / "delegate" / "worktrees").iterdir()
    return run_dir / "a"


def commit_in_the_real_worktree(repo):
    def commit(request):
        worktree = real_worktree(repo)
        (worktree / "by-the-verifier.txt").write_text("proof\n")
        git(worktree, "add", "-A")
        git(worktree, "commit", "-q", "-m", "a commit by the verifier")

    return commit


def clear_calls(scripted) -> None:
    for calls in (scripted.calls, scripted.verifier_calls, scripted.fixup_calls, scripted.continuation_calls):
        calls.clear()


def gate_runs(repo, run_id) -> int:
    return sum(1 for e in read_journal_file(repo, run_id) if e["event"] == "gate-start")


def stopped_in_the_verifier(repo, capsys, scripted, change) -> str:
    """Start the run, let the verifier call `change` (or nothing), stop it with Ctrl-C, and return the run id."""

    def interrupt(request):
        if change is not None:
            change(request)
        raise KeyboardInterrupt

    scripted.during_verifier = interrupt
    code, _, _ = start(repo, capsys)
    assert code == 130
    scripted.during_verifier = None
    clear_calls(scripted)
    return run_id_of(repo)


def test_a_resume_after_a_stop_in_a_verifier_that_committed_in_the_real_worktree_halts_with_an_invariant_violation_and_runs_no_gate(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id = stopped_in_the_verifier(repo, capsys, scripted, commit_in_the_real_worktree(repo))
    gates_before = gate_runs(repo, run_id)
    head = git(real_worktree(repo), "rev-parse", "HEAD").strip()

    code, out, err = resume_main(repo, capsys, run_id)

    events = read_journal_file(repo, run_id)
    assert code == 1
    assert "invariant violation" in err and "by-the-verifier.txt" in err
    (violation,) = [e for e in events if e["event"] == "invariant-violation"]
    assert violation["ticket"] == "a" and violation["head_after"] == head and violation["head_before"] != head
    assert any("by-the-verifier.txt" in str(change) for change in violation["changes"])
    assert gate_runs(repo, run_id) == gates_before
    assert scripted.verifier_calls == [] and scripted.calls == []
    assert events[-1]["event"] == "run-end" and events[-1]["result"] == "failed"
    assert git(repo, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "main").strip()


def test_a_resume_after_a_stop_in_a_verifier_that_wrote_an_untracked_file_halts_with_an_invariant_violation(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))

    def write(request):
        (real_worktree(repo) / "oops.txt").write_text("by the verifier\n")

    run_id = stopped_in_the_verifier(repo, capsys, scripted, write)

    code, out, err = resume_main(repo, capsys, run_id)

    (violation,) = [e for e in read_journal_file(repo, run_id) if e["event"] == "invariant-violation"]
    assert code == 1 and any("oops.txt" in str(change) for change in violation["changes"])
    assert scripted.verifier_calls == []


def test_a_resume_after_a_stop_in_a_verifier_that_changed_nothing_goes_on_and_builds_the_ticket(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id = stopped_in_the_verifier(repo, capsys, scripted, None)

    code, out, err = resume_main(repo, capsys, run_id)

    events = read_journal_file(repo, run_id)
    assert (code, err) == (0, "")
    assert "invariant-violation" not in event_names(events)
    assert events[-1]["event"] == "run-end" and events[-1]["result"] == "built"
    assert len(scripted.verifier_calls) == 1


def test_a_journal_with_an_invariant_violation_and_no_run_end_is_refused_at_a_resume_with_an_unchanged_plan(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))

    def write(request):
        (real_worktree(repo) / "oops.txt").write_text("by the verifier\n")

    scripted.during_verifier = write
    code, _, _ = start(repo, capsys)
    assert code == 1
    run_id = run_id_of(repo)
    # A kill after `invariant-violation` and before `step-end` leaves a journal with no `run-end`.
    lines = journal_path(repo, run_id).read_text().splitlines()
    cut = next(i for i, line in enumerate(lines) if json.loads(line)["event"] == "invariant-violation")
    journal_path(repo, run_id).write_text("\n".join(lines[: cut + 1]) + "\n")
    scripted.during_verifier = None
    clear_calls(scripted)
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1
    assert "invariant violation" in err and "new run" in err
    assert journal_path(repo, run_id).read_bytes() == before
    assert scripted.calls == [] and scripted.verifier_calls == []
