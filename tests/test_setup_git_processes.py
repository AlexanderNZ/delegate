"""The count of the `git` processes that the test code starts (#101).

On a machine with endpoint security, each program start is slow. The module
`tests/git_processes.py` counts each `git` process that test code starts, and
`pytest` writes the sum at the end of a run. These cases check the count, and
they hold the budget of the shared helpers.
"""

from . import git_processes
from .support import git, make_repo


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
