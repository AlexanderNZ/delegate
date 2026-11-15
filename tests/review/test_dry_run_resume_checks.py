"""`delegate run --resume ID --dry-run` makes each check of the resume that needs no write, with the message of the resume (#357).

Each case runs `delegate.main` against a real temporary git repository with the scripted adapter. The expected message is the
message that the real resume gives for the same run: the dry run is a promise of that message.
"""

from __future__ import annotations

from ..support import (
    git, journal_path, kill_a_run, make_repo, read_journal_file, resume_main, run_main, state_dir, tree, without_take_over,
)
from ..support import HEAD, ticket

TWO = HEAD + ticket("a") + ticket("b", ("a",))


def run_id_of(repo) -> str:
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    return run_dir.name


def dry_run(repo, capsys, run_id: str) -> tuple[int, str, str]:
    return run_main(capsys, str(repo / "workflow.toml"), "--resume", run_id, "--repo", str(repo), "--dry-run")


def error_line(err: str) -> str:
    (line,) = [line for line in err.splitlines() if line.startswith("delegate run: ")]
    return line


def test_the_dry_run_of_the_resume_of_a_run_that_ended_exits_1_with_the_message_of_the_resume(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    code, _, _ = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 0
    run_id = run_id_of(repo)

    dry = dry_run(repo, capsys, run_id)
    real = resume_main(repo, capsys, run_id)

    assert real[0] == 1
    assert f"run {run_id!r} has ended with the result 'built'; it cannot be resumed" in real[2]
    assert dry[0] == 1
    assert error_line(dry[2]) == error_line(real[2])


def test_the_dry_run_of_the_resume_of_a_run_whose_run_branch_moved_exits_1_with_the_message_of_the_resume(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    run_id, _ = kill_a_run(repo, 3)  # killed as the specialist of ticket b starts, after ticket a landed
    git(repo, "update-ref", "refs/heads/run/demo", git(repo, "rev-parse", "main").strip())  # the run branch goes back to the base
    journal_before = journal_path(repo, run_id).read_bytes()

    dry = dry_run(repo, capsys, run_id)

    # The dry run writes nothing: the journal is as it was.
    assert journal_path(repo, run_id).read_bytes() == journal_before
    real = resume_main(repo, capsys, run_id)
    assert real[0] == 1
    assert "run cannot be resumed: the run branch run/demo is at" in real[2]
    assert dry[0] == 1
    assert error_line(dry[2]) == error_line(real[2])


def test_the_dry_run_of_the_resume_of_a_run_that_a_resume_halted_for_a_moved_run_branch_gives_the_message_of_the_resume(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path, TWO)
    run_id, _ = kill_a_run(repo, 3)
    git(repo, "update-ref", "refs/heads/run/demo", git(repo, "rev-parse", "main").strip())
    assert resume_main(repo, capsys, run_id)[0] == 1  # the resume writes run-branch-moved
    assert any(e["event"] == "run-branch-moved" for e in read_journal_file(repo, run_id))

    dry = dry_run(repo, capsys, run_id)
    real = resume_main(repo, capsys, run_id)

    assert real[0] == 1 and "start a new run" in real[2]
    assert dry[0] == 1
    assert error_line(dry[2]) == error_line(real[2])


def test_the_dry_run_of_the_resume_of_a_run_whose_run_branch_is_gone_exits_1_with_the_message_of_the_resume(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    run_id, _ = kill_a_run(repo, 3)
    git(repo, "branch", "-q", "-D", "run/demo")

    dry = dry_run(repo, capsys, run_id)
    real = resume_main(repo, capsys, run_id)

    assert real[0] == 1 and "is not a branch of the repository" in real[2]
    assert dry[0] == 1
    assert error_line(dry[2]) == error_line(real[2])


def test_the_dry_run_of_the_resume_of_a_run_that_nothing_blocks_exits_0_and_changes_nothing(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    run_id, _ = kill_a_run(repo, 3)
    journal_before = journal_path(repo, run_id).read_bytes()
    branch_before = git(repo, "rev-parse", "run/demo").strip()

    code, out, err = dry_run(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert f"run {run_id}: no plan change" in out
    assert journal_path(repo, run_id).read_bytes() == journal_before
    assert git(repo, "rev-parse", "run/demo").strip() == branch_before
    assert "b.txt" not in tree(repo, "run/demo")
    assert without_take_over(resume_main(repo, capsys, run_id)[2]) == ""
