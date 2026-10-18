"""The count of the `git` processes that the test code starts (#101).

On a machine with endpoint security, each program start is slow. The module
`tests/git_processes.py` counts each `git` process that test code starts, and
`pytest` writes the sum at the end of a run. These cases check the count, and
they hold the budget of the shared helpers.
"""

from delegate.cli import delegate

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
