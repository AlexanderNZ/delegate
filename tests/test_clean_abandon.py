"""`delegate clean --abandon <run-id> --reason <text>` ends a run that is not built for good (#201).

The command acts on a run that is paused, halted, failed, or stopped with no `run-end`, and that has no live process.
It appends `run-end` with the result `abandoned` and the reason to the journal, then removes the ticket worktrees, the
ticket branches and the hooks directory of the run. It keeps the run branch, and `--delete-run-branch` deletes it too.
`delegate status` and `delegate watch` show the state `abandoned` and the reason, and `delegate run --resume` refuses the run.

Each case makes a real run with `delegate.main` and the scripted adapter in a real temporary git repository, calls
`delegate clean`, and reads the git state, the files and the journal.
"""

import json
from pathlib import Path

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, bare_repo, git, journal_path, live_lock, make_repo, read_journal_file, run_end, run_start, stale_lock, state_dir, write_journal
from .support import event_names, resume_main, run_main, ticket_of
from .test_run_multi import HEAD, ticket
from .test_run_pause import ECONOMY_HEAD, pause_during_the_specialist_of, run_id_of

REASON = "the owner cancelled the work"


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abcx"})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def main(capsys, *argv: str) -> tuple[int, str, str]:
    code = delegate.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def abandon(repo: Path, capsys, run_id: str, *extra: str, reason: str | None = REASON) -> tuple[int, str, str]:
    args = ["clean", "--abandon", run_id, *(["--reason", reason] if reason is not None else []), *extra, "--repo", str(repo)]
    return main(capsys, *args)


def branches(repo: Path) -> set[str]:
    return set(git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").split())


def worktrees(repo: Path) -> set[Path]:
    lines = git(repo, "worktree", "list", "--porcelain").splitlines()
    return {Path(line.removeprefix("worktree ")).resolve() for line in lines if line.startswith("worktree ")}


def a_paused_run(tmp_path, capsys, scripted) -> tuple[Path, str]:
    """A run of tickets a and b that paused before b. Ticket a is built, so its worktree, branch and hooks are on the disk."""
    repo = make_repo(tmp_path, ECONOMY_HEAD + ticket("a") + ticket("b"))
    pause_during_the_specialist_of(repo, capsys, scripted, "a")
    code, _, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 3, err
    run_id = run_id_of(repo)
    hooks = state_dir(repo) / "hooks" / run_id
    assert hooks.is_dir() and branches(repo) >= {"run/demo", "run/demo-a"} and len(worktrees(repo)) == 2
    return repo, run_id


def test_abandon_of_a_paused_run_records_the_reason_removes_what_the_run_left_and_keeps_the_run_branch(tmp_path, capsys, scripted):
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)
    tip = git(repo, "rev-parse", "run/demo").strip()

    code, out, err = abandon(repo, capsys, run_id)

    assert (code, err) == (0, "")
    events = read_journal_file(repo, run_id)
    (end,) = [e for e in events if e["event"] == "run-end"]
    assert (end["result"], end["reason"]) == ("abandoned", REASON)
    assert (end["built"], end["failed"], end["skipped"]) == (["a"], [], [])
    # The decision is on the disk before the removal: the `cleanup` event follows `run-end`.
    assert event_names(events)[-2:] == ["run-end", "cleanup"]
    assert worktrees(repo) == {repo.resolve()}
    assert branches(repo) == {"main", "run/demo"}
    assert git(repo, "rev-parse", "run/demo").strip() == tip
    assert not (state_dir(repo) / "hooks" / run_id).exists()
    assert not (state_dir(repo) / "worktrees" / run_id).exists()
    assert "run/demo" in out and "kept" in out


def test_abandon_with_delete_run_branch_also_deletes_the_run_branch(tmp_path, capsys, scripted):
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)

    code, out, err = abandon(repo, capsys, run_id, "--delete-run-branch")

    assert (code, err) == (0, "")
    assert branches(repo) == {"main"}
    assert "run/demo" in out and "deleted" in out
    assert read_journal_file(repo, run_id)[-2]["result"] == "abandoned"


def test_abandon_removes_a_ticket_branch_that_the_run_branch_does_not_hold_and_a_worktree_with_a_change(tmp_path, capsys, scripted):
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)
    worktree = Path(next(e["worktree"] for e in read_journal_file(repo, run_id) if e["event"] == "step-start"))
    (worktree / "scratch.txt").write_text("not committed\n")
    git(worktree, "commit", "--allow-empty", "-m", "work that no branch holds")

    code, _, err = abandon(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert not worktree.exists() and branches(repo) == {"main", "run/demo"}


def test_a_second_abandon_of_the_same_run_writes_no_second_run_end(tmp_path, capsys, scripted):
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)
    assert abandon(repo, capsys, run_id)[0] == 0
    before = journal_path(repo, run_id).read_text()

    code, _, err = abandon(repo, capsys, run_id, "--delete-run-branch")

    assert (code, err) == (0, "")
    assert branches(repo) == {"main"}
    assert [e["result"] for e in read_journal_file(repo, run_id) if e["event"] == "run-end"] == ["abandoned"]
    assert journal_path(repo, run_id).read_text().startswith(before)


def test_abandon_of_a_run_with_a_live_lock_exits_1_names_delegate_stop_and_changes_nothing(tmp_path, capsys, scripted):
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)
    before = (journal_path(repo, run_id).read_text(), branches(repo), worktrees(repo))

    with live_lock(journal_path(repo, run_id)):
        code, out, err = abandon(repo, capsys, run_id)

    assert code == 1 and out == ""
    assert f"delegate stop {run_id}" in err
    assert (journal_path(repo, run_id).read_text(), branches(repo), worktrees(repo)) == before


def test_abandon_of_a_run_that_ended_built_exits_1_names_delegate_clean_and_changes_nothing(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, _, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 0, err
    run_id = run_id_of(repo)
    before = (journal_path(repo, run_id).read_text(), branches(repo), worktrees(repo))

    code, out, err = abandon(repo, capsys, run_id)

    assert code == 1 and out == ""
    assert "delegate clean" in err and "built" in err
    assert (journal_path(repo, run_id).read_text(), branches(repo), worktrees(repo)) == before


def test_abandon_of_a_run_that_failed_ends_it_as_abandoned(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    scripted.failing_tickets = {"a"}
    code, _, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 1, err
    run_id = run_id_of(repo)

    code, _, err = abandon(repo, capsys, run_id)

    assert (code, err) == (0, "")
    ends = [e for e in read_journal_file(repo, run_id) if e["event"] == "run-end"]
    assert [e["result"] for e in ends] == ["failed", "abandoned"]
    assert worktrees(repo) == {repo.resolve()} and branches(repo) == {"main", "run/demo"}


def test_abandon_without_a_reason_is_refused_by_the_parser_and_changes_nothing(tmp_path, capsys, scripted):
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)
    before = journal_path(repo, run_id).read_text()

    with pytest.raises(SystemExit) as refused:
        abandon(repo, capsys, run_id, reason=None)

    assert refused.value.code == 2
    assert "--reason" in capsys.readouterr().err
    assert journal_path(repo, run_id).read_text() == before


@pytest.mark.parametrize("extra", [["--merged"], ["--ended"], ["--dry-run"], ["a-run"]])
def test_abandon_with_an_option_or_a_run_of_the_other_clean_is_refused_by_the_parser(tmp_path, capsys, extra):
    repo = bare_repo(tmp_path)

    with pytest.raises(SystemExit) as refused:
        abandon(repo, capsys, "r1", *extra)

    assert refused.value.code == 2


def test_delete_run_branch_without_abandon_is_refused_by_the_parser(tmp_path, capsys):
    repo = bare_repo(tmp_path)

    with pytest.raises(SystemExit) as refused:
        main(capsys, "clean", "r1", "--delete-run-branch", "--repo", str(repo))

    assert refused.value.code == 2
    assert "--abandon" in capsys.readouterr().err


def test_abandon_of_an_unknown_run_exits_1_and_names_the_run(tmp_path, capsys):
    repo = bare_repo(tmp_path)

    code, _, err = abandon(repo, capsys, "no-such-run")

    assert code == 1 and "no-such-run" in err


def test_status_of_an_abandoned_run_shows_the_state_and_the_reason(tmp_path, capsys, scripted):
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)
    assert abandon(repo, capsys, run_id)[0] == 0

    code, out, err = main(capsys, "status", run_id, "--repo", str(repo))
    assert (code, err) == (0, "")
    assert f"run {run_id}: abandoned" in out.splitlines()[0]
    assert f"note: abandoned: {REASON}" in out.splitlines()

    code, out, _ = main(capsys, "status", run_id, "--repo", str(repo), "--json")
    snapshot = json.loads(out)
    assert (snapshot["state"], snapshot["note"]) == ("abandoned", f"abandoned: {REASON}")


@pytest.mark.parametrize("extra", [[], ["--retry", "a"]])
def test_resume_of_an_abandoned_run_is_refused_and_changes_nothing(tmp_path, capsys, scripted, extra):
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)
    assert abandon(repo, capsys, run_id)[0] == 0
    before = (journal_path(repo, run_id).read_text(), branches(repo))
    scripted.calls.clear()

    code, _, err = resume_main(repo, capsys, run_id, *extra)

    assert code == 1
    assert "abandoned" in err and run_id in err
    assert (journal_path(repo, run_id).read_text(), branches(repo)) == before
    assert scripted.calls == []


def test_a_resume_that_is_a_dry_run_of_an_abandoned_run_is_refused(tmp_path, capsys, scripted):
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)
    assert abandon(repo, capsys, run_id)[0] == 0

    code, _, err = resume_main(repo, capsys, run_id, "--dry-run")

    assert code == 1 and "abandoned" in err


def test_clean_of_an_abandoned_run_says_that_it_was_abandoned(tmp_path, capsys, scripted):
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)
    assert abandon(repo, capsys, run_id)[0] == 0
    before = journal_path(repo, run_id).read_text()

    code, out, err = main(capsys, "clean", run_id, "--repo", str(repo))

    assert (code, err) == (0, "")
    assert "abandoned" in out and "stay for a retry" not in out
    assert journal_path(repo, run_id).read_text() == before


def test_watch_without_a_run_id_gives_the_state_abandoned_and_the_reason_in_its_line(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(
        repo, "r1", run_start("r1", "a"),
        ("run-end", {"result": "abandoned", "built": [], "failed": [], "skipped": [], "reason": REASON}),
    )

    code, out, err = main(capsys, "watch", "--repo", str(repo))

    assert (code, err) == (1, "")
    assert out.startswith("no run is active; the newest run r1 ended abandoned at ")
    assert out.rstrip("\n").endswith(f"; reason: {REASON}") and out.count("\n") == 1


def test_abandon_of_a_run_with_a_stale_lock_records_lock_broken_with_the_old_holder_before_run_end(tmp_path, capsys, scripted):
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)
    stale_lock(journal_path(repo, run_id))
    (lock_file,) = (state_dir(repo) / "locks").glob("*.lock")
    dead_pid = json.loads(lock_file.read_text())["pid"]

    code, out, err = abandon(repo, capsys, run_id)

    assert (code, err) == (0, "")
    events = read_journal_file(repo, run_id)
    (broken,) = [e for e in events if e["event"] == "lock-broken"]
    assert (broken["run_id"], broken["pid"]) == (run_id, dead_pid)
    names = event_names(events)
    assert names.index("lock-broken") < names.index("run-end")
    assert not lock_file.exists()


def test_abandon_of_a_run_with_no_lock_records_no_lock_broken(tmp_path, capsys, scripted):
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)

    code, out, err = abandon(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert "lock-broken" not in event_names(read_journal_file(repo, run_id))
