"""`delegate clean`: remove what ended runs left behind (#168).

A run that ended `built` before the clean-up of #167, or with `keep-worktrees =
true`, keeps its ticket worktrees and its ticket branches. `delegate clean`
removes them for each run that it names, or for each run that ended `built`
with `--ended`. `--merged` also deletes the run branch when the base branch
holds it. A run that is active, failed, paused or stopped keeps everything.
`--dry-run` changes nothing.

Each case makes real runs with `delegate.main` and the scripted adapter in a real
temporary git repository, then runs `delegate clean` and reads the git state,
the files and the journal.
"""

import json
import shutil
from pathlib import Path

import pytest

from delegate.cli import delegate

from .support import WORKFLOW, git, journal_path, kill_a_run, live_lock, make_repo, read_journal, read_journal_file


@pytest.fixture
def scripted(scripted):
    """The scripted adapter of conftest, with the default files: ticket a writes `feature.txt`, which the gate needs."""
    scripted.files_by_ticket = {}
    return scripted


def workflow_on(tmp_path: Path, run_branch: str) -> Path:
    """A copy of the shared workflow, which keeps its worktrees, with its own run branch. It is outside the repository."""
    path = tmp_path / f"{run_branch.replace('/', '-')}.toml"
    path.write_text(WORKFLOW.replace('run-branch = "run/demo"', f'run-branch = "{run_branch}"'))
    return path


def run(repo: Path, workflow: Path, capsys) -> tuple[int, str]:
    """Run a workflow to its end. Return the exit code and the run id."""
    code = delegate.main(["run", str(workflow), "--repo", str(repo)])
    out = capsys.readouterr().out
    return code, str(read_journal(out)[0]["run_id"])


def clean(repo: Path, capsys, *args: str) -> tuple[int, str, str]:
    code = delegate.main(["clean", *args, "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def worktrees(repo: Path) -> set[Path]:
    """The path of each worktree that git has registered, the main checkout too."""
    lines = git(repo, "worktree", "list", "--porcelain").splitlines()
    return {Path(line.removeprefix("worktree ")).resolve() for line in lines if line.startswith("worktree ")}


def branches(repo: Path) -> dict[str, str]:
    """Each branch with the commit at its tip."""
    lines = git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads/").splitlines()
    return dict(line.split(" ") for line in lines)


def ticket_worktree(repo: Path, run_id: str, ticket: str = "a") -> Path:
    events = read_journal_file(repo, run_id)
    return Path(str(next(e["worktree"] for e in events if e["event"] == "step-start" and e["ticket"] == ticket))).resolve()


def line_of(out: str, run_id: str) -> str:
    (line,) = [line for line in out.splitlines() if line.startswith(f"run {run_id}:")]
    return line


def test_clean_ended_removes_the_worktree_and_merged_ticket_branch_of_a_built_run_and_keeps_a_failed_and_an_active_run(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path)
    # A run killed after its specialist committed, on run/demo: a live process takes its lock below, so it is active.
    active, _ = kill_a_run(repo, 2)
    code, built = run(repo, workflow_on(tmp_path, "run/built"), capsys)
    assert code == 0
    scripted.failing_tickets = {"a"}
    code, failed = run(repo, workflow_on(tmp_path, "run/failed"), capsys)
    assert code == 1
    kept = {ticket_worktree(repo, run_id) for run_id in (active, failed)}
    assert ticket_worktree(repo, built) in worktrees(repo) and kept <= worktrees(repo)

    with live_lock(journal_path(repo, active)):
        code, out, err = clean(repo, capsys, "--ended")

    assert (code, err) == (0, "")
    assert worktrees(repo) == {repo.resolve(), *kept}
    assert not ticket_worktree(repo, built).exists()
    assert set(branches(repo)) == {"main", "run/built", "run/demo", "run/demo-a", "run/failed", "run/failed-a"}
    assert str(ticket_worktree(repo, built)) in line_of(out, built) and "run/built-a" in line_of(out, built)
    assert "active" in line_of(out, active)
    assert "failed" in line_of(out, failed)
    cleanup = read_journal_file(repo, built)[-1]
    assert cleanup["event"] == "cleanup"
    assert [Path(p).resolve() for p in cleanup["worktrees_removed"]] == [ticket_worktree(repo, built)]
    assert cleanup["branches_deleted"] == ["run/built-a"]
    assert all(e["event"] != "cleanup" for run_id in (active, failed) for e in read_journal_file(repo, run_id))


def test_a_run_that_stopped_keeps_everything_and_the_line_names_the_reason(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    stopped, _ = kill_a_run(repo, 2)
    before = (worktrees(repo), branches(repo), journal_path(repo, stopped).read_text())

    code, out, err = clean(repo, capsys, stopped)

    assert (code, err) == (0, "")
    assert "stopped" in line_of(out, stopped)
    assert (worktrees(repo), branches(repo), journal_path(repo, stopped).read_text()) == before


def test_merged_deletes_the_run_branch_only_when_the_base_branch_holds_it(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    code, merged = run(repo, workflow_on(tmp_path, "run/merged"), capsys)
    assert code == 0
    code, open_run = run(repo, workflow_on(tmp_path, "run/open"), capsys)
    assert code == 0
    git(repo, "merge", "-q", "--ff-only", "run/merged")
    open_tip = branches(repo)["run/open"]

    code, out, err = clean(repo, capsys, "--ended", "--merged")

    assert (code, err) == (0, "")
    assert set(branches(repo)) == {"main", "run/open"}
    assert branches(repo)["run/open"] == open_tip
    assert worktrees(repo) == {repo.resolve()}
    assert "run/merged" in line_of(out, merged)
    assert "run/open" in line_of(out, open_run) and "main" in line_of(out, open_run)
    assert read_journal_file(repo, merged)[-1]["branches_deleted"] == ["run/merged-a", "run/merged"]
    kept = read_journal_file(repo, open_run)[-1]["branches_kept"]
    assert [k["branch"] for k in kept] == ["run/open"]


def test_without_merged_the_run_branch_stays_also_when_the_base_branch_holds_it(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    code, run_id = run(repo, workflow_on(tmp_path, "run/merged"), capsys)
    git(repo, "merge", "-q", "--ff-only", "run/merged")

    code, out, err = clean(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert set(branches(repo)) == {"main", "run/merged"}


def test_dry_run_changes_nothing_and_prints_what_it_would_remove(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    code, run_id = run(repo, workflow_on(tmp_path, "run/merged"), capsys)
    git(repo, "merge", "-q", "--ff-only", "run/merged")
    worktree = ticket_worktree(repo, run_id)
    before = (worktrees(repo), branches(repo), journal_path(repo, run_id).read_text(), sorted(worktree.rglob("*")))

    code, out, err = clean(repo, capsys, "--ended", "--merged", "--dry-run")

    assert (code, err) == (0, "")
    assert (worktrees(repo), branches(repo), journal_path(repo, run_id).read_text(), sorted(worktree.rglob("*"))) == before
    line = line_of(out, run_id)
    assert str(worktree) in line and "run/merged-a" in line and "run/merged" in line


def test_a_worktree_with_a_change_that_is_not_committed_stays_with_its_branch(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    code, run_id = run(repo, workflow_on(tmp_path, "run/built"), capsys)
    worktree = ticket_worktree(repo, run_id)
    (worktree / "notes.txt").write_text("not committed\n")

    code, out, err = clean(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert (worktree / "notes.txt").read_text() == "not committed\n"
    assert worktree in worktrees(repo)
    assert "run/built-a" in branches(repo)
    line = line_of(out, run_id)
    assert "notes.txt" in line and "branch run/built-a: its worktree is kept" in line
    # Nothing went, so the journal gets no event.
    assert read_journal_file(repo, run_id)[-1]["event"] == "run-end"


def test_a_ticket_branch_whose_worktree_directory_a_person_removed_is_deleted(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    code, run_id = run(repo, workflow_on(tmp_path, "run/built"), capsys)
    shutil.rmtree(ticket_worktree(repo, run_id))  # git still records the worktree, and refuses to delete its branch

    code, out, err = clean(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert set(branches(repo)) == {"main", "run/built"}
    assert worktrees(repo) == {repo.resolve()}
    assert read_journal_file(repo, run_id)[-1]["branches_deleted"] == ["run/built-a"]


def test_clean_removes_a_verify_copy_and_a_proof_copy_that_a_run_left(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    code, run_id = run(repo, workflow_on(tmp_path, "run/built"), capsys)
    copy = Path(str(next(e["copy"] for e in read_journal_file(repo, run_id) if e["event"] == "verify-start")))
    # A run process that a kill ends during a verifier leaves its scratch directory: the copy and the proof copy.
    (copy / "src").mkdir(parents=True)
    (copy / "src" / "x.py").write_text("x\n")
    (copy.parent / "proof").mkdir()

    code, out, err = clean(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert not copy.parent.exists()
    assert str(copy) in read_journal_file(repo, run_id)[-1]["copies_removed"]


def test_a_journal_line_after_the_cleanup_keeps_the_run_built_for_watch_and_status(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    code, run_id = run(repo, workflow_on(tmp_path, "run/built"), capsys)
    assert clean(repo, capsys, run_id)[0] == 0
    assert read_journal_file(repo, run_id)[-1]["event"] == "cleanup"

    watch_code = delegate.main(["watch", run_id, "--repo", str(repo), "--raw"])
    capsys.readouterr()
    status_code = delegate.main(["status", run_id, "--repo", str(repo), "--json"])
    status = json.loads(capsys.readouterr().out)

    assert watch_code == 0
    assert status_code == 0 and status["state"] == "built"


def test_a_second_clean_finds_nothing_to_remove_and_writes_no_event(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    code, cleaned = run(repo, workflow_on(tmp_path, "run/built"), capsys)
    code, unmerged = run(repo, workflow_on(tmp_path, "run/open"), capsys)
    git(repo, "merge", "-q", "--ff-only", "run/built")
    assert clean(repo, capsys, "--ended", "--merged")[0] == 0
    journals = [journal_path(repo, run_id).read_text() for run_id in (cleaned, unmerged)]

    code, out, err = clean(repo, capsys, "--ended", "--merged")

    assert (code, err) == (0, "")
    assert line_of(out, cleaned) == f"run {cleaned}: nothing to remove"
    assert "branch run/open: the base branch 'main' does not hold its commits" in line_of(out, unmerged)
    assert [journal_path(repo, run_id).read_text() for run_id in (cleaned, unmerged)] == journals


def test_an_unknown_run_id_is_an_error_and_the_other_runs_are_cleaned(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    code, run_id = run(repo, workflow_on(tmp_path, "run/built"), capsys)

    code, out, err = clean(repo, capsys, "no-such-run", run_id)

    assert code == 1
    assert "no-such-run" in err
    assert "run/built-a" not in branches(repo)


@pytest.mark.parametrize("args", [[], ["--merged"], ["--dry-run"]])
def test_clean_with_no_run_id_and_no_ended_is_a_usage_error(tmp_path, capsys, args):
    code = delegate.main(["clean", *args, "--repo", str(tmp_path)])

    assert code == 2
    assert "--ended" in capsys.readouterr().err


def test_tiers_before_clean_is_refused_and_changes_nothing(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    code, run_id = run(repo, workflow_on(tmp_path, "run/built"), capsys)
    before = branches(repo)

    code = delegate.main(["--tiers", "x.toml", "clean", "--ended", "--repo", str(repo)])

    captured = capsys.readouterr()
    assert (code, captured.out) == (2, "")
    assert "not of clean" in captured.err
    assert branches(repo) == before
