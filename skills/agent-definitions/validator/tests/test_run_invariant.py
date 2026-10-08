"""The engine checks that a verifier leaves the real worktree as it was.

The engine records the HEAD and the status of the worktree of the ticket before
and after each verifier run. A difference halts the run. A verifier that writes
only in its temporary copy changes nothing there, and the engine removes the
copy after every run.

Each case uses a real git repository, registers a scripted adapter by name, and
calls `delegate.main`. The scripted adapter plays a verifier that writes in the
real worktree, in the way a verifier in any harness could.
"""

import pytest

from agent_definitions import adapters, delegate

from .support import ScriptedAdapter, WORKFLOW, event_names, git, make_repo, read_journal
from .test_run_multi import HEAD, ticket


@pytest.fixture(autouse=True)
def identity(git_identity):
    """The rebase and the scripted commits need a committer."""


@pytest.fixture
def registered():
    names = []

    def register(adapter):
        adapters.register("scripted", adapter)
        names.append("scripted")
        return adapter

    yield register
    for name in names:
        adapters.unregister(name)


def run(repo, capsys):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def real_worktree(repo, ticket_id="a"):
    """The worktree where the specialist built the ticket. The run id is the only directory below `worktrees`."""
    (run_dir,) = (repo / ".git" / "delegate" / "worktrees").iterdir()
    return run_dir / ticket_id


def write_in_the_real_worktree(repo, name, text):
    def write(request):
        (real_worktree(repo) / name).write_text(text)

    return write


def commit_in_the_real_worktree(repo):
    def commit(request):
        worktree = real_worktree(repo)
        (worktree / "by-the-verifier.txt").write_text("proof\n")
        git(worktree, "add", "-A")
        git(worktree, "commit", "-q", "-m", "a commit by the verifier")

    return commit


@pytest.mark.parametrize(
    "change, path",
    [
        pytest.param("new-file", "oops.txt", id="new-file"),
        pytest.param("tracked-edit", "feature.txt", id="edit-of-a-tracked-file"),
        pytest.param("tracked-delete", "feature.txt", id="delete-of-a-tracked-file"),
        pytest.param("commit", "by-the-verifier.txt", id="a-commit-that-moves-head"),
    ],
)
def test_a_verifier_that_changes_the_real_worktree_halts_the_run_and_the_journal_names_the_change(
    tmp_path, capsys, registered, change, path
):
    repo = make_repo(tmp_path)
    adapter = registered(ScriptedAdapter())

    def change_it(request):
        worktree = real_worktree(repo)
        if change == "new-file":
            (worktree / "oops.txt").write_text("written by the verifier\n")
        elif change == "tracked-edit":
            (worktree / "feature.txt").write_text("changed by the verifier\n")
        elif change == "tracked-delete":
            (worktree / "feature.txt").unlink()
        else:
            commit_in_the_real_worktree(repo)(request)

    adapter.during_verifier = change_it
    base_tip = git(repo, "rev-parse", "main").strip()

    code, out, err = run(repo, capsys)

    events = read_journal(out)
    assert code == 1
    assert "Traceback" not in err
    assert "delegate run: ticket a: invariant violation" in err and path in err
    (violation,) = [e for e in events if e["event"] == "invariant-violation"]
    assert (violation["ticket"], violation["round"]) == ("a", 0)
    assert violation["worktree"] == str(real_worktree(repo))
    assert any(path in str(entry) for entry in violation["changes"]), violation["changes"]
    if change == "commit":
        assert violation["head_before"] != violation["head_after"]
        assert violation["head_after"] == git(real_worktree(repo), "rev-parse", "HEAD").strip()
    else:
        assert violation["head_before"] == violation["head_after"]
    assert event_names(events)[-1] == "run-end"
    assert events[-1]["result"] == "failed"
    assert next(e for e in events if e["event"] == "step-end")["state"] == "failed"
    assert "run-branch-advance" not in event_names(events)
    assert git(repo, "rev-parse", "run/demo").strip() == base_tip  # an ACCEPT on disk does not advance the run branch


def test_a_violation_halts_the_whole_run_so_a_ticket_that_waits_is_skipped_and_never_built(tmp_path, capsys, registered):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b") + ticket("c", ("a",)))
    adapter = registered(ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abc"}))
    adapter.during_verifier = write_in_the_real_worktree(repo, "oops.txt", "by the verifier\n")

    code, out, err = run(repo, capsys)

    events = read_journal(out)
    assert code == 1
    assert len(adapter.calls) == 1 and len(adapter.verifier_calls) == 1  # only ticket a ran
    skipped = {e["ticket"] for e in events if e["event"] == "skip"}
    assert skipped == {"b", "c"}
    assert [e["ticket"] for e in events if e["event"] == "step-start"] == ["a"]


def test_a_verifier_that_writes_only_in_its_temporary_copy_does_not_halt_the_run(tmp_path, capsys, registered):
    repo = make_repo(tmp_path)
    adapter = registered(ScriptedAdapter(verifier_files={"feature.txt": "broken by the verifier\n", "scratch.txt": "proof\n"}))

    code, out, err = run(repo, capsys)

    events = read_journal(out)
    assert (code, err) == (0, "")
    assert "invariant-violation" not in event_names(events)
    assert events[-1]["result"] == "built"
    assert git(repo, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "run/demo-a").strip()
    assert git(repo, "show", "run/demo:feature.txt") == "feature\n"


def test_a_verifier_that_rewrites_a_file_that_was_untracked_before_it_ran_halts_the_run(tmp_path, capsys, registered):
    # A gate leaves an untracked file in the worktree. The status line of that file is the same before and after the rewrite.
    workflow = WORKFLOW.replace('gates = ["test -f feature.txt"]', 'gates = ["test -f feature.txt && echo gate > leftover.txt"]')
    repo = make_repo(tmp_path, workflow)
    adapter = registered(ScriptedAdapter())
    adapter.during_verifier = write_in_the_real_worktree(repo, "leftover.txt", "rewritten by the verifier\n")

    code, out, err = run(repo, capsys)

    (violation,) = [e for e in read_journal(out) if e["event"] == "invariant-violation"]
    assert code == 1
    assert any("leftover.txt" in str(entry) for entry in violation["changes"])


def test_the_copy_of_each_verifier_run_is_removed_after_an_accept_a_reject_with_fixups_and_a_violation(tmp_path, capsys, registered):
    # Reject twice, then accept: three verifier runs, three copies.
    repo = make_repo(tmp_path)
    adapter = registered(ScriptedAdapter(verdict_sequence=["REJECT", "REJECT", "ACCEPT"], verdict_findings=["feature.txt is not a CSV file."]))

    code, out, err = run(repo, capsys)

    assert code == 0
    assert len(adapter.verifier_calls) == 3
    assert [call.cwd.exists() for call in adapter.verifier_calls] == [False, False, False]


def test_the_copy_is_removed_also_when_the_run_halts_on_a_violation(tmp_path, capsys, registered):
    repo = make_repo(tmp_path)
    adapter = registered(ScriptedAdapter())
    adapter.during_verifier = write_in_the_real_worktree(repo, "oops.txt", "by the verifier\n")

    code, out, err = run(repo, capsys)

    assert code == 1
    (call,) = adapter.verifier_calls
    assert not call.cwd.exists()
    evidence = [e for e in read_journal(out) if e["event"] == "verify-result"]
    assert len(evidence) == 1  # the result of the verifier is journalled before the halt

