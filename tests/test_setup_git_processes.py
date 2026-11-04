"""The count of the `git` processes that the test code starts (#101).

On a machine with endpoint security, each program start is slow. The module
`tests/git_processes.py` counts each `git` process that test code starts, and
`pytest` writes the sum at the end of a run. These cases check the count, and
they hold the budget of the shared helpers.
"""

import os
import subprocess
import sys
from pathlib import Path

from delegate.cli import delegate

from . import git_processes
from .support import committed_repo, git, make_repo


def test_the_count_holds_each_git_process_that_the_test_code_starts(tmp_path):
    before = git_processes.started()

    git(tmp_path, "init", "-q")
    git(tmp_path, "status", "--short")

    assert git_processes.started() - before == 2
    assert (tmp_path / ".git").is_dir()


def test_a_second_repository_from_the_same_template_starts_no_git_process(tmp_path):
    make_repo(tmp_path / "first")
    before = git_processes.started()

    repo = make_repo(tmp_path / "second")

    assert git_processes.started() - before == 0
    assert git(repo, "log", "--format=%an <%ae> %s") == "Scratch <scratch@example.invalid> seed\n"


def test_a_second_repository_with_the_same_files_starts_no_git_process_and_holds_the_files_in_one_commit(tmp_path):
    files = {"a.txt": "a\n", "src/x.py": "x = 1\n"}
    committed_repo(tmp_path / "first", files, message="seed")
    before = git_processes.started()

    repo = committed_repo(tmp_path / "second", files, message="seed")

    assert git_processes.started() - before == 0
    assert git(repo, "log", "--format=%an <%ae> %s") == "Scratch <scratch@example.invalid> seed\n"
    assert git(repo, "ls-files") == "a.txt\nsrc/x.py\n"
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "branch", "--show-current") == "main\n"
    assert (repo / "src" / "x.py").read_text() == "x = 1\n"


def test_a_repository_with_no_files_has_no_commit_and_other_files_give_another_template(tmp_path):
    empty = committed_repo(tmp_path / "empty", {})
    other = committed_repo(tmp_path / "other", {"b.txt": "b\n"}, message="b")

    assert git(empty, "rev-parse", "--is-inside-work-tree") == "true\n"
    assert git(empty, "rev-list", "--all") == ""
    assert git(other, "log", "--format=%s") == "b\n"
    assert git(other, "ls-files") == "b.txt\n"


def test_a_worktree_of_a_copied_repository_is_a_git_worktree_of_the_copy(tmp_path):
    committed_repo(tmp_path / "first" / "main", {"app.py": "print('hi')\n"})
    main = committed_repo(tmp_path / "second" / "main", {"app.py": "print('hi')\n"})

    git(main, "worktree", "add", "-q", "-b", "feature", str(tmp_path / "second" / "wt"))

    assert git(tmp_path / "second" / "wt", "rev-parse", "--show-toplevel").strip() == str((tmp_path / "second" / "wt").resolve())
    assert git(tmp_path / "second" / "wt", "ls-files") == "app.py\n"
    assert not (tmp_path / "first" / "main" / ".git" / "worktrees").exists()


def test_the_scripted_adapter_starts_only_the_add_and_the_commit_of_its_work_in_a_run_of_one_ticket(tmp_path, capsys, scripted):
    scripted.files_by_ticket = {}  # the default work, `feature.txt`, makes the gate of the workflow green
    repo = make_repo(tmp_path)
    before = git_processes.started()

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])

    # The engine starts many git processes in this run; the count holds none of them.
    assert git_processes.started() - before == 2
    assert (code, capsys.readouterr().err) == (0, "")
    assert git(repo, "show", "run/demo:feature.txt") == "feature\n"
    assert len(scripted.calls) == len(scripted.verifier_calls) == 1


# The processes of one run of one ticket with the scripted adapter, from the start of `delegate run` to its end: the git
# processes of the engine, the add and the commit of the adapter, and the gate. Before #239 the run started 62. The
# issue asks for at least 30% fewer, so the budget is 43.
RUN_OF_ONE_TICKET_BEFORE_239: int = 62
RUN_OF_ONE_TICKET_BUDGET: int = 43


def test_a_run_of_one_ticket_starts_at_least_30_percent_fewer_processes_than_before_239(tmp_path, capsys, scripted):
    scripted.files_by_ticket = {}  # the default work, `feature.txt`, makes the gate of the workflow green
    repo = make_repo(tmp_path)
    before = git_processes.all_started()

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])

    started = git_processes.all_started() - before
    out, err = capsys.readouterr()
    # The run did all of its work: the build, the gate, the rebase, the verifier, and the advance of the run branch.
    assert (code, err) == (0, "")
    assert "\nrun built " in out, out
    assert git(repo, "show", "run/demo:feature.txt") == "feature\n"
    assert len(scripted.calls) == len(scripted.verifier_calls) == 1
    assert RUN_OF_ONE_TICKET_BUDGET <= RUN_OF_ONE_TICKET_BEFORE_239 * 7 // 10
    assert started <= RUN_OF_ONE_TICKET_BUDGET, f"the run started {started} processes; the budget is {RUN_OF_ONE_TICKET_BUDGET}"


def test_delegate_status_with_no_run_id_looks_up_the_repository_with_one_git_process(tmp_path, capsys, scripted):
    # The command finds the newest run and reports it; it looks up the repository and its state one time (#239).
    scripted.files_by_ticket = {}
    repo = make_repo(tmp_path)
    assert delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)]) == 0
    run_line = capsys.readouterr().out.splitlines()[0]
    before = git_processes.all_started()

    code = delegate.main(["status", "--repo", str(repo)])

    started = git_processes.all_started() - before
    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    assert run_line.removeprefix("run ") in out
    assert started == 1


def test_a_process_that_the_test_code_starts_counts_once_in_the_count_of_all_processes(tmp_path):
    before = git_processes.all_started()

    subprocess.run(["true"], check=True)
    os.system("true")

    assert git_processes.all_started() - before == 2


def test_a_git_process_that_code_outside_the_tests_starts_counts_in_all_processes_but_not_in_the_git_count(tmp_path):
    git_before, all_before = git_processes.started(), git_processes.all_started()

    with os.popen("git --version") as output:  # `os.popen` is in the `os` module, outside the tests
        text = output.read()

    assert text.startswith("git version ")
    assert (git_processes.started() - git_before, git_processes.all_started() - all_before) == (0, 1)


def test_the_summary_of_the_suite_gives_the_count_of_all_processes_and_of_the_git_processes_of_the_test_code():
    # One case of this module that starts two processes, one of them `git`: `git init` and `git status`.
    case = f"{Path(__file__).name}::test_the_count_holds_each_git_process_that_the_test_code_starts"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", case],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
    )

    assert r.returncode == 0, r.stdout + r.stderr
    assert "processes that the tests start: 2 in 1 tests\n" in r.stdout
    assert "git processes of the test code: 2 in 1 tests\n" in r.stdout
