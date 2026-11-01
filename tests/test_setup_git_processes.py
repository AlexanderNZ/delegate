"""The count of the `git` processes that the test code starts (#101).

On a machine with endpoint security, each program start is slow. The module
`tests/git_processes.py` counts each `git` process that test code starts, and
`pytest` writes the sum at the end of a run. These cases check the count, and
they hold the budget of the shared helpers.
"""

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
