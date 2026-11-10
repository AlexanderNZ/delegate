"""The git backend of the version-control port, tested through the port on real temporary repositories.

Each case calls a method of the port and reads the result, or reads the state
that git reports afterwards. No case imports a private function of the backend.
The port itself names no git command and no flag, and the last cases check that.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from delegate.adapters.git import GitVersionControl
from delegate.ports import vcs as port
from delegate.ports.vcs import CONFLICT, FAILED, REBASED, Commit, PathChange, RebaseResult, VcsError, VersionControl

from . import git_processes
from .support import commit_head, git, make_repo
from .test_fsmonitor import alive, daemon_pid, why_no_daemon_can_start


@pytest.fixture(autouse=True)
def identity(git_identity):
    """A rebase and a commit need a committer."""


@pytest.fixture
def vcs() -> VersionControl:
    return GitVersionControl()


def git_status(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in `path` and return the result, whatever the exit code is."""
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


def with_entries(vcs: VersionControl, worktree: Path, directory: Path | None = None, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in `directory` (the worktree by default) with the entries that the port gives for `worktree`, whatever the exit code is."""
    return subprocess.run(
        ["git", "-C", str(directory or worktree), *args], capture_output=True, text=True,
        env={**os.environ, **vcs.worktree_environment(worktree)},
    )


def hooks_with_entries(vcs: VersionControl, worktree: Path, directory: Path) -> Path:
    """The hooks directory that git reads in `directory` with the entries that the port gives for `worktree`."""
    out = with_entries(vcs, worktree, directory, "rev-parse", "--path-format=absolute", "--git-path", "hooks").stdout
    return Path(out.strip()).resolve()


def commit(repo: Path, name: str, content: str, message: str) -> str:
    """Write a file in a checkout, commit it, and return the id of the new commit."""
    (repo / name).write_text(content)
    git(repo, "add", "-A")
    return commit_head(repo, "-m", message)[0]


def sha(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref).strip()


def user_enables_fsmonitor(monkeypatch, tmp_path: Path) -> None:
    """Give the user a global git configuration with `core.fsmonitor=true`.

    The suite turns the setting off in the git configuration environment, and
    that scope outranks every file. So this drops those entries for the test.
    """
    path = tmp_path / "global-gitconfig"
    path.write_text("[core]\n\tfsmonitor = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    monkeypatch.delenv("GIT_CONFIG_COUNT")


@pytest.fixture
def no_daemon_survives():
    """Stop every daemon in the given paths when the test ends, so a failing test leaks none."""
    paths: list[Path] = []
    yield paths
    for path in paths:
        if path.exists():
            git_status(path, "fsmonitor--daemon", "stop")


# The interface.


def test_the_git_backend_has_every_operation_of_the_port(vcs):
    assert isinstance(vcs, VersionControl)


# The default branch (#267).


def test_the_default_branch_is_the_branch_that_the_main_checkout_holds_when_the_repository_records_no_remote_default(tmp_path, vcs):
    repo = make_repo(tmp_path)
    git(repo, "branch", "other")

    assert vcs.default_branch(repo) == "main"


def test_the_default_branch_from_a_linked_worktree_is_the_branch_of_the_main_checkout(tmp_path, vcs):
    repo = make_repo(tmp_path)
    vcs.ensure_worktree(repo, tmp_path / "linked", "ticket", "main")

    assert vcs.default_branch(tmp_path / "linked") == "main"


def test_the_default_branch_is_the_local_branch_that_the_remote_default_names_and_not_the_branch_of_the_main_checkout(tmp_path, vcs):
    repo = make_repo(tmp_path)
    git(repo, "branch", "trunk")
    git(repo, "update-ref", "refs/remotes/origin/trunk", "trunk")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")

    assert vcs.default_branch(repo) == "trunk"


def test_the_default_branch_is_none_when_the_local_branch_of_the_remote_default_does_not_exist(tmp_path, vcs):
    repo = make_repo(tmp_path)
    git(repo, "update-ref", "refs/remotes/origin/trunk", "main")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")

    assert vcs.default_branch(repo) is None


def test_the_default_branch_is_none_when_the_main_checkout_holds_no_branch(tmp_path, vcs):
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-q", "--detach")

    assert vcs.default_branch(repo) is None


# Where the repository is.


def test_the_repository_root_is_the_top_of_the_checkout_for_a_path_inside_it(tmp_path, vcs):
    repo = make_repo(tmp_path)
    (repo / "sub" / "dir").mkdir(parents=True)

    assert vcs.repository_root(repo / "sub" / "dir").resolve() == repo.resolve()


def test_the_shared_data_directory_is_the_same_from_the_checkout_and_from_a_worktree(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")

    from_checkout = vcs.shared_data_directory(repo)
    from_worktree = vcs.shared_data_directory(worktree)

    assert from_checkout.is_absolute()
    assert from_checkout == from_worktree
    assert from_checkout.resolve() == (repo / ".git").resolve()


def test_the_shared_data_directory_from_a_subdirectory_of_a_worktree_costs_one_git_process(tmp_path, vcs):
    # Each process start is slow on a machine with endpoint security, and the engine asks for this directory many
    # times in each run (#229).
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    (worktree / "sub").mkdir()
    before = git_processes.all_started()

    shared = vcs.shared_data_directory(worktree / "sub")

    assert git_processes.all_started() - before == 1
    assert shared == (repo / ".git").resolve()


def test_the_repository_paths_of_a_subdirectory_of_a_worktree_are_its_top_and_the_shared_directory_from_one_git_process(tmp_path, vcs):
    # Each command looks up the two paths one time (#239), so the two come from one process.
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    (worktree / "sub").mkdir()
    before = git_processes.all_started()

    top, shared = vcs.repository_paths(worktree / "sub")

    assert git_processes.all_started() - before == 1
    assert top.resolve() == worktree.resolve()
    assert shared == (repo / ".git").resolve()


def test_the_repository_paths_of_a_directory_outside_any_repository_are_an_error(tmp_path, vcs, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    with pytest.raises(VcsError):
        vcs.repository_paths(outside)


def test_the_repository_root_of_a_directory_outside_any_repository_is_an_error(tmp_path, vcs, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    # No repository above the temporary directory counts.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    with pytest.raises(VcsError):
        vcs.repository_root(outside)


# Branches and commits.


def test_a_branch_exists_when_it_was_made_and_not_when_it_was_not(tmp_path, vcs):
    repo = make_repo(tmp_path)

    assert vcs.branch_exists(repo, "main")
    assert not vcs.branch_exists(repo, "run/demo")

    vcs.create_branch(repo, "run/demo", "main")

    assert vcs.branch_exists(repo, "run/demo")


def test_a_tag_is_not_a_branch(tmp_path, vcs):
    repo = make_repo(tmp_path)
    git(repo, "tag", "release")

    assert not vcs.branch_exists(repo, "release")


def test_create_branch_puts_the_branch_at_the_start_and_leaves_the_checkout_alone(tmp_path, vcs):
    repo = make_repo(tmp_path)
    seed = sha(repo, "main")
    commit(repo, "more.txt", "more\n", "more")

    vcs.create_branch(repo, "run/demo", seed)

    assert sha(repo, "run/demo") == seed
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_create_branch_refuses_a_name_that_is_taken(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError):
        vcs.create_branch(repo, "main", "main")


def test_create_branch_refuses_a_start_that_does_not_exist(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError):
        vcs.create_branch(repo, "run/demo", "no-such-start")

    assert not vcs.branch_exists(repo, "run/demo")


def test_the_branch_tip_is_the_newest_commit_of_the_branch(tmp_path, vcs):
    repo = make_repo(tmp_path)
    newest = commit(repo, "more.txt", "more\n", "more")

    assert vcs.branch_tip(repo, "main") == newest


def test_the_branch_tip_of_a_branch_that_does_not_exist_is_an_error(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError):
        vcs.branch_tip(repo, "no-such-branch")


def test_the_commit_of_a_branch_name_and_of_a_commit_id_is_the_commit(tmp_path, vcs):
    repo = make_repo(tmp_path)
    seed = sha(repo, "main")

    assert vcs.commit_of(repo, "main") == seed
    assert vcs.commit_of(repo, seed) == seed


def test_the_commit_of_a_name_that_names_nothing_is_an_error(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError):
        vcs.commit_of(repo, "no-such-ref")


def test_the_head_commit_of_a_worktree_is_the_commit_that_it_has_checked_out(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    newest = commit(worktree, "feature.txt", "feature\n", "feature")

    assert vcs.head_commit(worktree) == newest
    assert vcs.head_commit(repo) == sha(repo, "main")


# Worktrees.


def test_ensure_worktree_makes_a_worktree_on_a_new_branch_that_starts_at_the_start(tmp_path, vcs):
    repo = make_repo(tmp_path)
    seed = sha(repo, "main")
    worktree = tmp_path / "wt"

    vcs.ensure_worktree(repo, worktree, "feature", "main")

    assert (worktree / "seed.txt").read_text() == "seed\n"
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature"
    assert sha(repo, "feature") == seed
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_ensure_worktree_makes_the_missing_parent_directories(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "state" / "worktrees" / "run-1" / "a"

    vcs.ensure_worktree(repo, worktree, "feature", "main")

    assert (worktree / "seed.txt").exists()


def test_ensure_worktree_checks_out_a_branch_that_exists_and_ignores_the_start(tmp_path, vcs):
    repo = make_repo(tmp_path)
    git(repo, "branch", "feature", "main")
    git(repo, "checkout", "-q", "feature")
    kept = commit(repo, "kept.txt", "kept\n", "kept")
    git(repo, "checkout", "-q", "main")
    worktree = tmp_path / "wt"

    vcs.ensure_worktree(repo, worktree, "feature", "main")

    assert vcs.head_commit(worktree) == kept
    assert (worktree / "kept.txt").exists()


def test_ensure_worktree_reuses_a_worktree_with_the_work_that_it_holds(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    work = commit(worktree, "work.txt", "work\n", "work")
    (worktree / "loose.txt").write_text("loose\n")

    vcs.ensure_worktree(repo, worktree, "feature", "main")

    assert vcs.head_commit(worktree) == work
    assert (worktree / "loose.txt").read_text() == "loose\n"


def test_ensure_worktree_makes_a_worktree_again_when_its_directory_went_but_git_still_lists_it(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    work = commit(worktree, "work.txt", "work\n", "work")
    shutil.rmtree(worktree)

    vcs.ensure_worktree(repo, worktree, "feature", "main")

    assert vcs.head_commit(worktree) == work


def test_ensure_worktree_undoes_a_rebase_that_a_killed_run_left_half_done(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    mine = commit(worktree, "seed.txt", "mine\n", "mine")
    commit(repo, "seed.txt", "theirs\n", "theirs")
    stopped = git_status(worktree, "rebase", "main")
    assert stopped.returncode != 0
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip() == "HEAD"

    vcs.ensure_worktree(repo, worktree, "feature", "main")

    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature"
    assert vcs.head_commit(worktree) == mine


def test_ensure_worktree_refuses_a_worktree_that_holds_another_branch(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")

    with pytest.raises(VcsError, match="feature"):
        vcs.ensure_worktree(repo, worktree, "other", "main")


def test_ensure_worktree_names_the_ticket_in_the_refusal_of_a_worktree_that_holds_another_branch(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")

    with pytest.raises(VcsError) as refusal:
        vcs.ensure_worktree(repo, worktree, "other", "main")

    assert str(refusal.value) == f"worktree {worktree} holds 'feature', not the branch 'other' of its ticket"


def test_ensure_worktree_refuses_a_start_that_does_not_exist(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"

    with pytest.raises(VcsError):
        vcs.ensure_worktree(repo, worktree, "feature", "no-such-start")

    assert not worktree.exists()


def test_the_worktree_runs_with_fsmonitor_off_when_the_user_turns_it_on(tmp_path, vcs, monkeypatch, no_daemon_survives):
    repo = make_repo(tmp_path)
    user_enables_fsmonitor(monkeypatch, tmp_path)
    no_daemon_survives.append(repo)
    worktree = tmp_path / "wt"
    no_daemon_survives.append(worktree)
    assert git(repo, "config", "core.fsmonitor").strip() == "true"

    vcs.ensure_worktree(repo, worktree, "feature", "main")

    assert with_entries(vcs, worktree, None, "config", "core.fsmonitor").stdout.strip() == "false"
    assert git_status(worktree, "fsmonitor--daemon", "status").returncode != 0


def test_the_fsmonitor_rule_holds_for_a_worktree_that_is_reused(tmp_path, vcs, monkeypatch, no_daemon_survives):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    user_enables_fsmonitor(monkeypatch, tmp_path)
    no_daemon_survives.extend([repo, worktree])

    vcs.ensure_worktree(repo, worktree, "feature", "main")

    assert with_entries(vcs, worktree, None, "config", "core.fsmonitor").stdout.strip() == "false"


def test_the_fsmonitor_setting_of_a_worktree_leaves_the_repository_and_the_user_as_they_were(
    tmp_path, vcs, monkeypatch, no_daemon_survives
):
    repo = make_repo(tmp_path)
    user_enables_fsmonitor(monkeypatch, tmp_path)
    no_daemon_survives.append(repo)
    worktree = tmp_path / "wt"
    no_daemon_survives.append(worktree)
    global_before = (tmp_path / "global-gitconfig").read_text()

    vcs.ensure_worktree(repo, worktree, "feature", "main")

    assert git_status(repo, "config", "--local", "--get", "core.fsmonitor").returncode == 1
    assert git(repo, "config", "core.fsmonitor").strip() == "true"
    assert (tmp_path / "global-gitconfig").read_text() == global_before


def test_remove_worktree_removes_the_directory_and_keeps_the_branch_and_its_work(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    work = commit(worktree, "work.txt", "work\n", "work")

    vcs.remove_worktree(repo, worktree)

    assert not worktree.exists()
    assert str(worktree.resolve()) not in git(repo, "worktree", "list")
    assert sha(repo, "feature") == work


def test_remove_worktree_removes_a_worktree_with_changes_that_were_not_committed(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    (worktree / "loose.txt").write_text("loose\n")
    (worktree / "seed.txt").write_text("changed\n")

    vcs.remove_worktree(repo, worktree)

    assert not worktree.exists()


def test_a_branch_can_have_a_worktree_again_after_remove_worktree(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    work = commit(worktree, "work.txt", "work\n", "work")
    vcs.remove_worktree(repo, worktree)

    vcs.ensure_worktree(repo, worktree, "feature", "main")

    assert vcs.head_commit(worktree) == work


def test_remove_worktree_of_a_path_that_is_no_worktree_is_an_error(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError):
        vcs.remove_worktree(repo, tmp_path / "never-made")


# Delete a branch that another branch holds (#167).


def branch_with_work(tmp_path: Path, vcs: VersionControl, branch: str) -> tuple[Path, str]:
    """A repository on `main` with `branch`, which holds one commit that `main` does not hold. The worktree goes."""
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, branch, "main")
    work = commit(worktree, "work.txt", "work\n", "work")
    vcs.remove_worktree(repo, worktree)
    return repo, work


def test_delete_branch_deletes_a_branch_that_the_holder_holds_also_when_the_checkout_does_not_hold_it(tmp_path, vcs):
    repo, work = branch_with_work(tmp_path, vcs, "run/x-a")
    vcs.create_branch(repo, "run/x", work)

    assert vcs.delete_branch(repo, "run/x-a", "run/x") is True

    assert not vcs.branch_exists(repo, "run/x-a")
    assert sha(repo, "run/x") == work
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert git_status(repo, "config", "--get-regexp", r"^branch\.run/x-a\.").stdout == ""


def test_delete_branch_keeps_a_branch_that_the_holder_does_not_hold_and_leaves_no_setting(tmp_path, vcs):
    repo, work = branch_with_work(tmp_path, vcs, "run/x-a")
    vcs.create_branch(repo, "run/x", "main")

    assert vcs.delete_branch(repo, "run/x-a", "run/x") is False

    assert sha(repo, "run/x-a") == work
    assert git_status(repo, "config", "--get-regexp", r"^branch\.run/x-a\.").stdout == ""


def test_discard_branch_deletes_a_branch_that_no_other_branch_holds(tmp_path, vcs):
    repo, _ = branch_with_work(tmp_path, vcs, "run/x-a")

    vcs.discard_branch(repo, "run/x-a")

    assert not vcs.branch_exists(repo, "run/x-a")


def test_discard_branch_of_a_branch_that_does_not_exist_is_an_error(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError):
        vcs.discard_branch(repo, "run/no-such-branch")


def test_discard_branch_of_a_branch_that_a_worktree_has_checked_out_is_an_error_and_the_branch_stays(tmp_path, vcs):
    repo = make_repo(tmp_path)
    vcs.ensure_worktree(repo, tmp_path / "wt", "run/x-a", "main")

    with pytest.raises(VcsError):
        vcs.discard_branch(repo, "run/x-a")

    assert vcs.branch_exists(repo, "run/x-a")


def test_prune_worktrees_drops_the_record_of_a_worktree_whose_directory_is_gone_and_keeps_the_others(tmp_path, vcs):
    repo = make_repo(tmp_path)
    gone, kept = tmp_path / "gone", tmp_path / "kept"
    vcs.ensure_worktree(repo, gone, "run/x-a", "main")
    vcs.ensure_worktree(repo, kept, "run/x-b", "main")
    shutil.rmtree(gone)

    vcs.prune_worktrees(repo)

    listed = [line.removeprefix("worktree ") for line in git(repo, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]
    assert [Path(p).resolve() for p in listed] == [repo.resolve(), kept.resolve()]
    # The branch of a pruned worktree is free again: a delete needs no worktree that has it checked out.
    vcs.create_branch(repo, "run/x", "run/x-a")
    assert vcs.delete_branch(repo, "run/x-a", "run/x") is True


def test_delete_branch_of_a_branch_that_a_worktree_has_checked_out_is_an_error_and_the_branch_stays(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "run/x-a", "main")
    work = commit(worktree, "work.txt", "work\n", "work")
    vcs.create_branch(repo, "run/x", work)

    with pytest.raises(VcsError):
        vcs.delete_branch(repo, "run/x-a", "run/x")

    assert sha(repo, "run/x-a") == work
    assert git_status(repo, "config", "--get-regexp", r"^branch\.run/x-a\.").stdout == ""


# Rebase.


def worktree_behind_main(tmp_path: Path, vcs: VersionControl) -> tuple[Path, Path]:
    """A repository whose `main` moved on, and a worktree on `feature` that holds one commit of its own."""
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    commit(worktree, "feature.txt", "feature\n", "feature work")
    commit(repo, "main.txt", "main\n", "main work")
    return repo, worktree


def test_rebase_onto_puts_the_commits_of_the_branch_on_top_of_the_target(tmp_path, vcs):
    repo, worktree = worktree_behind_main(tmp_path, vcs)
    before = vcs.head_commit(worktree)

    result = vcs.rebase_onto(worktree, "main")

    assert result == RebaseResult(REBASED)
    assert vcs.head_commit(worktree) != before
    assert git(worktree, "rev-parse", "HEAD~1").strip() == sha(repo, "main")
    assert (worktree / "main.txt").exists()
    assert (worktree / "feature.txt").exists()
    assert git(worktree, "log", "-1", "--format=%s").strip() == "feature work"


def test_rebase_onto_a_target_that_the_branch_holds_already_is_a_rebase_with_no_change(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    work = commit(worktree, "feature.txt", "feature\n", "feature work")

    result = vcs.rebase_onto(worktree, "main")

    assert result.status == REBASED
    assert vcs.head_commit(worktree) == work


def test_rebase_onto_a_conflict_names_the_files_and_leaves_the_worktree_as_it_was(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    mine = commit(worktree, "seed.txt", "mine\n", "mine")
    commit(repo, "seed.txt", "theirs\n", "theirs")

    result = vcs.rebase_onto(worktree, "main")

    assert result.status == CONFLICT
    assert result.conflicts == ("seed.txt",)
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature"
    assert vcs.head_commit(worktree) == mine
    assert (worktree / "seed.txt").read_text() == "mine\n"
    assert git(worktree, "status", "--porcelain").strip() == ""


def test_rebase_onto_a_refusal_gives_a_failed_result_with_the_reason_and_changes_nothing(tmp_path, vcs):
    repo, worktree = worktree_behind_main(tmp_path, vcs)
    before = vcs.head_commit(worktree)
    (worktree / "feature.txt").write_text("changed but not committed\n")

    result = vcs.rebase_onto(worktree, "main")

    assert result.status == FAILED
    assert result.conflicts == ()
    assert result.detail != ""
    assert vcs.head_commit(worktree) == before
    assert (worktree / "feature.txt").read_text() == "changed but not committed\n"


def test_rebase_onto_a_target_that_does_not_exist_gives_a_failed_result(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")

    result = vcs.rebase_onto(worktree, "no-such-target")

    assert result.status == FAILED
    assert result.detail != ""


def test_rebase_onto_a_refusal_whose_list_of_conflicts_cannot_be_read_is_an_error(tmp_path, vcs):
    repo, worktree = worktree_behind_main(tmp_path, vcs)
    (worktree / "feature.txt").write_text("changed but not committed\n")
    # The rebase refuses to start and needs no diff. The command that lists the conflicts reads this setting and dies.
    git(repo, "config", "diff.algorithm", "no-such-algorithm")

    with pytest.raises(VcsError, match="diff --name-only --diff-filter=U failed"):
        vcs.rebase_onto(worktree, "main")


def test_a_rebase_result_with_an_unknown_status_is_refused():
    with pytest.raises(ValueError, match="rebase status"):
        RebaseResult("merged")


# Commits and history.


def test_commits_since_lists_the_commits_of_the_worktree_after_the_start_newest_first(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    first = commit(worktree, "one.txt", "1\n", "first change")
    second = commit(worktree, "two.txt", "2\n", "second change")

    assert vcs.commits_since(worktree, "main") == [Commit(second, "second change"), Commit(first, "first change")]


def test_commits_since_is_empty_when_nothing_is_new(tmp_path, vcs):
    repo = make_repo(tmp_path)

    assert vcs.commits_since(repo, "main") == []


def test_commits_since_an_end_branch_counts_that_branch_and_not_the_checkout(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    only = commit(worktree, "one.txt", "1\n", "only change")

    assert vcs.commits_since(repo, "main", "feature") == [Commit(only, "only change")]
    assert vcs.commits_since(repo, "main") == []
    assert vcs.commits_since(repo, "feature", "main") == []


def test_commits_since_a_ref_that_does_not_exist_is_an_error(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError):
        vcs.commits_since(repo, "no-such-ref")


def test_commits_since_keeps_a_subject_that_holds_a_space_or_a_percent_sign(tmp_path, vcs):
    repo = make_repo(tmp_path)
    newest = commit(repo, "one.txt", "1\n", "fix 100% of the thing (#7)")

    assert vcs.commits_since(repo, "HEAD~1") == [Commit(newest, "fix 100% of the thing (#7)")]


def test_is_ancestor_tells_whether_a_commit_holds_another(tmp_path, vcs):
    repo = make_repo(tmp_path)
    old = sha(repo, "main")
    new = commit(repo, "one.txt", "1\n", "one")

    assert vcs.is_ancestor(repo, old, new)
    assert vcs.is_ancestor(repo, new, new)
    assert not vcs.is_ancestor(repo, new, old)


def test_is_ancestor_takes_branch_names(tmp_path, vcs):
    repo = make_repo(tmp_path)
    git(repo, "branch", "side", "main")
    git(repo, "checkout", "-q", "side")
    commit(repo, "side.txt", "side\n", "side")
    git(repo, "checkout", "-q", "main")

    assert vcs.is_ancestor(repo, "main", "side")
    assert not vcs.is_ancestor(repo, "side", "main")


def test_is_ancestor_of_a_ref_that_does_not_exist_is_an_error(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError):
        vcs.is_ancestor(repo, "no-such-ref", "main")


# Moving a branch.


def test_fast_forward_branch_moves_the_branch_to_a_commit_that_holds_it(tmp_path, vcs):
    repo = make_repo(tmp_path)
    vcs.create_branch(repo, "run/demo", "main")
    git(repo, "checkout", "-q", "-b", "side")
    new = commit(repo, "side.txt", "side\n", "side")
    git(repo, "checkout", "-q", "main")

    moved = vcs.fast_forward_branch(repo, "run/demo", new)

    assert moved is True
    assert sha(repo, "run/demo") == new
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_fast_forward_branch_leaves_a_branch_that_holds_a_commit_the_target_lacks(tmp_path, vcs):
    repo = make_repo(tmp_path)
    git(repo, "branch", "side", "main")
    git(repo, "checkout", "-q", "side")
    side = commit(repo, "side.txt", "side\n", "side")
    git(repo, "checkout", "-q", "main")
    main_tip = commit(repo, "main.txt", "main\n", "main")

    moved = vcs.fast_forward_branch(repo, "main", side)

    assert moved is False
    assert sha(repo, "main") == main_tip


def test_fast_forward_branch_to_the_tip_it_has_already_moves_nothing_and_succeeds(tmp_path, vcs):
    repo = make_repo(tmp_path)
    tip = sha(repo, "main")

    assert vcs.fast_forward_branch(repo, "main", tip) is True
    assert sha(repo, "main") == tip


def test_fast_forward_branch_to_a_commit_that_does_not_exist_leaves_the_branch_and_moves_nothing(tmp_path, vcs):
    repo = make_repo(tmp_path)
    tip = sha(repo, "main")

    moved = vcs.fast_forward_branch(repo, "main", "0" * 40)

    assert moved is False
    assert sha(repo, "main") == tip


def test_fast_forward_branch_with_the_tip_that_the_caller_read_moves_the_branch(tmp_path, vcs):
    repo = make_repo(tmp_path)
    tip = sha(repo, "main")
    vcs.create_branch(repo, "run/demo", "main")
    git(repo, "checkout", "-q", "-b", "side")
    new = commit(repo, "side.txt", "side\n", "side")

    assert vcs.fast_forward_branch(repo, "run/demo", new, tip) is True
    assert sha(repo, "run/demo") == new


def test_fast_forward_branch_with_a_tip_that_the_branch_left_is_an_error_and_moves_nothing(tmp_path, vcs):
    # The caller read the tip, then a commit landed on the branch: the port must not move it from the new tip (#239).
    repo = make_repo(tmp_path)
    read = sha(repo, "main")
    vcs.create_branch(repo, "run/demo", "main")
    git(repo, "checkout", "-q", "run/demo")
    landed = commit(repo, "landed.txt", "landed\n", "landed")
    git(repo, "checkout", "-q", "-b", "side")
    new = commit(repo, "side.txt", "side\n", "side")

    with pytest.raises(VcsError):
        vcs.fast_forward_branch(repo, "run/demo", new, read)
    assert sha(repo, "run/demo") == landed


def test_fast_forward_branch_to_the_name_of_an_annotated_tag_moves_the_branch_to_its_commit(tmp_path, vcs):
    repo = make_repo(tmp_path)
    vcs.create_branch(repo, "run/demo", "main")
    git(repo, "checkout", "-q", "-b", "side")
    new = commit(repo, "side.txt", "side\n", "side")
    git(repo, "tag", "-a", "-m", "release", "v1", new)

    assert vcs.fast_forward_branch(repo, "run/demo", "v1") is True
    assert sha(repo, "run/demo") == new
    assert git(repo, "cat-file", "-t", "refs/heads/run/demo").strip() == "commit"


def test_fast_forward_branch_of_a_branch_that_does_not_exist_is_an_error(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError):
        vcs.fast_forward_branch(repo, "no-such-branch", sha(repo, "main"))


# What changed.


def repo_with_a_feature_branch(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose `main` moved on after `feature` left it. Returns the repository and the feature tip."""
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "feature")
    tip = commit(repo, "feature.txt", "feature\n", "feature work")
    git(repo, "checkout", "-q", "main")
    commit(repo, "main.txt", "main\n", "main work")
    return repo, tip


def test_changed_paths_lists_the_paths_that_the_checkout_changed_after_a_commit(tmp_path, vcs):
    repo = make_repo(tmp_path)
    start = sha(repo, "main")
    commit(repo, "a.txt", "a\n", "a")
    commit(repo, "b.txt", "b\n", "b")

    assert vcs.changed_paths(repo, start) == ["a.txt", "b.txt"]


def test_changed_paths_up_to_a_branch_counts_that_branch_and_not_the_checkout(tmp_path, vcs):
    repo, tip = repo_with_a_feature_branch(tmp_path)

    # The two tips differ in the work of both sides.
    assert vcs.changed_paths(repo, "main", "feature") == ["feature.txt", "main.txt"]


def test_changed_paths_from_the_merge_base_leaves_out_what_the_other_side_did(tmp_path, vcs):
    repo, tip = repo_with_a_feature_branch(tmp_path)

    assert vcs.changed_paths(repo, "main", "feature", from_merge_base=True) == ["feature.txt"]


def test_changed_paths_counts_a_rename_as_its_old_and_its_new_path(tmp_path, vcs):
    repo = make_repo(tmp_path)
    start = sha(repo, "main")
    git(repo, "mv", "seed.txt", "moved.txt")
    git(repo, "commit", "-q", "-m", "move")

    assert vcs.changed_paths(repo, start) == ["moved.txt", "seed.txt"]


def test_changed_paths_that_follow_renames_count_a_rename_as_its_new_path(tmp_path, vcs):
    repo = make_repo(tmp_path)
    start = sha(repo, "main")
    git(repo, "mv", "seed.txt", "moved.txt")
    git(repo, "commit", "-q", "-m", "move")

    assert vcs.changed_paths(repo, start, follow_renames=True) == ["moved.txt"]


def test_changed_paths_from_a_ref_that_does_not_exist_is_an_error(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError):
        vcs.changed_paths(repo, "no-such-ref")
    with pytest.raises(VcsError):
        vcs.changed_paths(repo, "no-such-ref", follow_renames=True)


def test_diff_text_from_the_merge_base_holds_the_work_of_one_side_only(tmp_path, vcs):
    repo, tip = repo_with_a_feature_branch(tmp_path)

    text = vcs.diff_text(repo, "main", "feature", from_merge_base=True)

    assert "feature.txt" in text
    assert "+feature" in text
    assert "main.txt" not in text


def test_diff_text_between_two_tips_shows_the_work_of_both_sides(tmp_path, vcs):
    repo, tip = repo_with_a_feature_branch(tmp_path)

    text = vcs.diff_text(repo, "main", "feature")

    assert "feature.txt" in text
    assert "main.txt" in text


def test_diff_text_keeps_the_text_as_git_wrote_it(tmp_path, vcs):
    repo = make_repo(tmp_path)
    (repo / "seed.txt").write_text("seed\n\n")
    git(repo, "commit", "-q", "-a", "-m", "blank line")

    text = vcs.diff_text(repo, "HEAD~1", "HEAD")

    assert text.endswith("+\n")


def test_diff_text_of_a_ref_that_does_not_exist_is_an_error(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError, match="failed"):
        vcs.diff_text(repo, "no-such-ref", "main")


def test_diff_text_is_empty_when_nothing_differs(tmp_path, vcs):
    repo = make_repo(tmp_path)

    assert vcs.diff_text(repo, "main", "main") == ""


def test_diff_stat_from_the_merge_base_names_the_path_of_one_side_only_with_its_count(tmp_path, vcs):
    repo, tip = repo_with_a_feature_branch(tmp_path)

    stat = vcs.diff_stat(repo, "main", "feature", from_merge_base=True)

    assert "feature.txt |" in stat
    assert "main.txt" not in stat
    assert "1 file changed, 1 insertion(+)" in stat


def test_diff_stat_keeps_a_long_path_whole(tmp_path, vcs):
    repo = make_repo(tmp_path)
    path = "a-directory-with-a-long-name/another-directory-with-a-long-name/" + "x" * 60 + ".txt"
    (repo / path).parent.mkdir(parents=True)
    (repo / path).write_text("one\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "long path")

    assert path in vcs.diff_stat(repo, "HEAD~1", "HEAD")


def test_diff_stat_is_empty_when_nothing_differs(tmp_path, vcs):
    repo = make_repo(tmp_path)

    assert vcs.diff_stat(repo, "main", "main") == ""


def test_diff_stat_of_a_ref_that_does_not_exist_is_an_error(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError, match="failed"):
        vcs.diff_stat(repo, "no-such-ref", "main")


def test_uncommitted_changes_names_each_changed_and_each_new_path_with_its_state(tmp_path, vcs):
    repo = make_repo(tmp_path)
    (repo / "seed.txt").write_text("changed\n")
    (repo / "new").mkdir()
    (repo / "new" / "inner.txt").write_text("inner\n")
    (repo / "staged.txt").write_text("staged\n")
    git(repo, "add", "staged.txt")

    changes = vcs.uncommitted_changes(repo)

    assert sorted(changes, key=lambda change: change.path) == [
        PathChange("??", "new/inner.txt"),
        PathChange(" M", "seed.txt"),
        PathChange("A ", "staged.txt"),
    ]


def test_uncommitted_changes_is_empty_for_a_clean_checkout(tmp_path, vcs):
    repo = make_repo(tmp_path)

    assert vcs.uncommitted_changes(repo) == []


def test_uncommitted_changes_leaves_out_a_path_that_git_ignores(tmp_path, vcs):
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text("ignored.txt\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ignore")
    (repo / "ignored.txt").write_text("x\n")

    assert vcs.uncommitted_changes(repo) == []


def test_uncommitted_changes_of_a_directory_that_is_no_checkout_is_an_error(tmp_path, vcs, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(VcsError):
        vcs.uncommitted_changes(outside)


# The hooks of a worktree.


def test_the_hooks_directory_of_a_checkout_is_the_directory_that_it_uses(tmp_path, vcs):
    repo = make_repo(tmp_path)

    assert vcs.hooks_directory(repo).resolve() == (repo / ".git" / "hooks").resolve()


def test_a_worktree_uses_the_hooks_directory_that_was_set_for_it_and_no_other_checkout_does(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    mine = tmp_path / "my-hooks"
    mine.mkdir()

    vcs.use_hooks_directory(repo, worktree, mine)

    assert hooks_with_entries(vcs, worktree, worktree) == mine.resolve()
    assert hooks_with_entries(vcs, worktree, repo) == (repo / ".git" / "hooks").resolve()
    # Without the entries, the worktree reads the hooks of the repository: a shell of a person gets no guard.
    assert vcs.hooks_directory(worktree).resolve() == (repo / ".git" / "hooks").resolve()


def test_a_hook_in_the_directory_that_was_set_runs_in_the_worktree(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    hooks = tmp_path / "my-hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    vcs.use_hooks_directory(repo, worktree, hooks)
    (worktree / "x.txt").write_text("x\n")
    git(worktree, "add", "x.txt")

    refused = with_entries(vcs, worktree, None, "commit", "-q", "-m", "x")

    assert refused.returncode != 0


def test_the_settings_of_a_worktree_hold_for_it_alone_when_the_path_of_the_repository_holds_a_wildcard(tmp_path, vcs):
    repo = make_repo(tmp_path / "odd[1]*?")
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    git(repo, "worktree", "add", "-q", "-b", "other", str(tmp_path / "other"), "main")
    mine = tmp_path / "my-hooks"
    mine.mkdir()

    vcs.use_hooks_directory(repo, worktree, mine)

    assert hooks_with_entries(vcs, worktree, worktree) == mine.resolve()
    assert hooks_with_entries(vcs, worktree, tmp_path / "other") == (repo / ".git" / "hooks").resolve()
    assert hooks_with_entries(vcs, worktree, repo) == (repo / ".git" / "hooks").resolve()


def test_the_settings_of_a_worktree_need_no_extension_of_the_repository(tmp_path, vcs):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    hooks = tmp_path / "my-hooks"
    hooks.mkdir()

    vcs.ensure_worktree(repo, worktree, "feature", "main")
    vcs.use_hooks_directory(repo, worktree, hooks)

    assert git_status(repo, "config", "--get-regexp", r"^extensions\.").stdout == ""
    assert git(repo, "config", "--get", "core.repositoryformatversion").strip() == "0"


def test_remove_worktree_leaves_the_configuration_of_the_repository_as_it_was(tmp_path, vcs):
    repo = make_repo(tmp_path)
    before = (repo / ".git" / "config").read_text()
    worktree = tmp_path / "wt"
    hooks = tmp_path / "my-hooks"
    hooks.mkdir()
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    vcs.use_hooks_directory(repo, worktree, hooks)
    assert (repo / ".git" / "config").read_text() == before

    vcs.remove_worktree(repo, worktree)

    assert (repo / ".git" / "config").read_text() == before


def test_ensure_worktree_drops_the_section_of_a_worktree_that_git_removed_and_keeps_an_include_of_a_person(tmp_path, vcs):
    repo = make_repo(tmp_path)
    person = tmp_path / "person.config"
    person.write_text("[person]\n\tsetting = kept\n")
    git(repo, "config", "includeIf.gitdir:/no/such/place/.path", str(person))
    git(repo, "config", "include.path", str(tmp_path / "missing.config"))
    gone = tmp_path / "gone"
    vcs.ensure_worktree(repo, gone, "gone", "main")
    gone_git_dir = Path(git(gone, "rev-parse", "--absolute-git-dir").strip()).resolve()
    # The section that an earlier version wrote for the worktree.
    git(repo, "config", f"includeIf.gitdir:{gone_git_dir}.path", str(gone_git_dir / "delegate.config"))
    git(repo, "worktree", "remove", "--force", str(gone))
    worktree = tmp_path / "wt"

    vcs.ensure_worktree(repo, worktree, "feature", "main")

    includes = git(repo, "config", "--local", "--name-only", "--get-regexp", r"^include").split()
    assert sorted(includes) == ["include.path", "includeif.gitdir:/no/such/place/.path"]
    assert with_entries(vcs, worktree, None, "config", "core.fsmonitor").stdout.strip() == "false"


def test_setting_the_hooks_directory_of_a_path_that_is_no_worktree_is_an_error(tmp_path, vcs, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(VcsError):
        vcs.use_hooks_directory(repo, outside, tmp_path)


# The verifier copy.


def repo_with_ticket_branch(tmp_path: Path, vcs: VersionControl) -> tuple[Path, str]:
    """A repository with a run branch and a ticket branch one commit ahead of it. Returns the repository and the ticket commit."""
    repo = make_repo(tmp_path)
    vcs.create_branch(repo, "run/demo", "main")
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "run/demo-a", "run/demo")
    ticket = commit(worktree, "feature.txt", "feature\n", "feature")
    vcs.remove_worktree(repo, worktree)
    return repo, ticket


def test_the_verifier_copy_holds_the_ticket_branch_checked_out_and_the_run_branch(tmp_path, vcs):
    repo, ticket = repo_with_ticket_branch(tmp_path, vcs)
    copy = tmp_path / "scratch" / "copy"

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD").strip() == "run/demo-a"
    assert vcs.head_commit(copy) == ticket
    assert (copy / "feature.txt").read_text() == "feature\n"
    assert sha(copy, "run/demo") == sha(repo, "run/demo")
    assert vcs.commits_since(copy, "run/demo") == [Commit(ticket, "feature")]


def test_the_verifier_copy_has_no_remote_and_a_break_in_it_does_not_reach_the_repository(tmp_path, vcs):
    repo, ticket = repo_with_ticket_branch(tmp_path, vcs)
    refs_before = git(repo, "for-each-ref")
    copy = tmp_path / "copy"

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert git(copy, "remote").strip() == ""
    commit(copy, "feature.txt", "broken\n", "the verifier breaks the copy")
    git(copy, "branch", "-f", "run/demo", "HEAD")
    git(copy, "branch", "-D", "main")
    assert git(repo, "for-each-ref") == refs_before
    assert (copy / ".git").is_dir()


def test_the_verifier_copy_is_made_when_the_checkout_of_the_repository_is_on_the_run_branch(tmp_path, vcs):
    repo, ticket = repo_with_ticket_branch(tmp_path, vcs)
    git(repo, "checkout", "-q", "run/demo")
    copy = tmp_path / "copy"

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD").strip() == "run/demo-a"
    assert sha(copy, "run/demo") == sha(repo, "run/demo")


def test_the_verifier_copy_of_a_branch_that_does_not_exist_is_an_error(tmp_path, vcs):
    repo, ticket = repo_with_ticket_branch(tmp_path, vcs)

    with pytest.raises(VcsError):
        vcs.make_verifier_copy(repo, tmp_path / "copy", "no-such-branch", "run/demo")

    assert not (tmp_path / "copy").exists()


def test_a_directory_that_was_there_before_a_failed_verifier_copy_stays(tmp_path, vcs):
    repo, ticket = repo_with_ticket_branch(tmp_path, vcs)
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "mine.txt").write_text("mine\n")

    with pytest.raises(VcsError):
        vcs.make_verifier_copy(repo, taken, "run/demo-a", "run/demo")

    assert (taken / "mine.txt").read_text() == "mine\n"


def test_the_verifier_copy_runs_with_fsmonitor_off_when_the_user_turns_it_on(tmp_path, vcs, monkeypatch, no_daemon_survives):
    repo, ticket = repo_with_ticket_branch(tmp_path, vcs)
    user_enables_fsmonitor(monkeypatch, tmp_path)
    copy = tmp_path / "copy"
    no_daemon_survives.extend([repo, copy])

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert git(copy, "config", "core.fsmonitor").strip() == "false"
    assert git_status(copy, "fsmonitor--daemon", "status").returncode != 0
    assert git_status(repo, "config", "--local", "--get", "core.fsmonitor").returncode == 1


# The file watcher.


def test_stop_file_watcher_is_quiet_when_nothing_runs_and_when_the_path_is_no_checkout(tmp_path, vcs):
    repo = make_repo(tmp_path)
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()

    vcs.stop_file_watcher(repo)
    vcs.stop_file_watcher(nowhere)
    vcs.stop_file_watcher(tmp_path / "gone")


def test_stop_file_watcher_stops_a_daemon_that_serves_the_checkout(vcs):
    reason = why_no_daemon_can_start()
    if reason is not None:
        pytest.skip(reason)
    # The socket of the daemon has a path limit of about 100 characters.
    short = Path(tempfile.mkdtemp(prefix="fw", dir="/tmp"))
    started: list[int] = []
    try:
        repo = make_repo(short)
        git(repo, "fsmonitor--daemon", "start")
        pid = daemon_pid(repo)
        assert pid is not None
        started.append(pid)

        vcs.stop_file_watcher(repo)

        assert daemon_pid(repo) is None
    finally:
        for pid in started:
            if alive(pid):
                os.kill(pid, 15)
        shutil.rmtree(short, ignore_errors=True)


# The port names no git command and no flag.

PORT_TEXT = Path(port.__file__).read_text(encoding="utf-8")

# The words of the tool: its name, its plumbing commands, and the settings that the backend uses.
GIT_WORDS = (
    "git", "rev-parse", "rev-list", "show-ref", "update-ref", "merge-base", "for-each-ref", "symbolic-ref",
    "fsmonitor", "worktree add", "worktree remove", "worktree prune", "core.", "refs/heads", "origin",
    "--abbrev-ref", "--is-ancestor", "--no-checkout", "--diff-filter", "--name-only", "--force",
)


@pytest.mark.parametrize("word", GIT_WORDS)
def test_the_port_does_not_name_a_git_word(word):
    assert word not in PORT_TEXT.lower()


def test_the_port_holds_no_command_line_flag():
    assert re.findall(r"(?<![\w-])--?[a-z][\w-]*", PORT_TEXT) == []


# The scratch repository of the guard probe (#219).


def test_a_scratch_repository_has_one_commit_no_remote_and_no_file(tmp_path, vcs, monkeypatch):
    for role in ("AUTHOR", "COMMITTER"):
        for key in ("NAME", "EMAIL"):
            monkeypatch.delenv(f"GIT_{role}_{key}", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)  # the user has no identity, and the commit needs none
    path = tmp_path / "scratch"

    vcs.make_scratch_repository(path)

    assert git(path, "rev-list", "--count", "HEAD").strip() == "1"
    assert git(path, "remote").strip() == ""
    assert git(path, "ls-files").strip() == ""
    assert git(path, "status", "--porcelain").strip() == ""


def test_a_scratch_repository_is_made_in_an_empty_directory_that_is_there_already(tmp_path, vcs):
    path = tmp_path / "scratch"
    path.mkdir()

    vcs.make_scratch_repository(path)

    assert vcs.head_commit(path) == sha(path, "HEAD")


def test_a_scratch_repository_that_cannot_be_made_is_a_vcs_error_and_leaves_nothing(tmp_path, vcs):
    blocked = tmp_path / "file"
    blocked.write_text("a file, not a directory\n")

    with pytest.raises(VcsError):
        vcs.make_scratch_repository(blocked / "scratch")

    assert not (blocked / "scratch").exists()


# The limits and the quiet start of each git command of the backend (#295). A shim of `git` is the system boundary: it
# stands for a hook, a signing program or a filter that waits on stdin, or hangs, and it passes each other call to git.

def git_shim(tmp_path: Path, monkeypatch, before: str) -> Path:
    """Put a `git` first on PATH that runs the shell text `before`, and then the real git. Return the shim directory."""
    shim = tmp_path / "shim"
    shim.mkdir()
    real = shutil.which("git")
    shell = shutil.which("sh")  # no fixed `/bin/...` path: the Nix build sandbox has no `/bin/bash`
    (shim / "git").write_text(f'#!{shell}\n{before}\nexec "{real}" "$@"\n')
    (shim / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    return shim


@pytest.fixture
def open_stdin():
    """Make fd 0 the read end of a pipe that nothing writes to and nothing closes: a read on it waits."""
    saved, (read_end, write_end) = os.dup(0), os.pipe()
    os.dup2(read_end, 0)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        for fd in (saved, read_end, write_end):
            os.close(fd)


def test_a_git_command_of_the_backend_reads_no_stdin_of_the_engine(tmp_path, vcs, monkeypatch, open_stdin):
    repo = make_repo(tmp_path)
    marker = tmp_path / "stdin"
    # With the stdin of the engine, a program that reads it waits for ever. With the null device, it reads the end of the input.
    git_shim(tmp_path, monkeypatch, f'if [ /dev/stdin -ef /dev/null ]; then echo null; else echo inherited; fi > "{marker}"')

    assert vcs.branch_exists(repo, "main")

    assert marker.read_text().strip() == "null"


def test_a_git_command_of_the_backend_asks_for_no_credential_on_a_terminal(tmp_path, vcs, monkeypatch):
    repo = make_repo(tmp_path)
    marker = tmp_path / "prompt"
    monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
    git_shim(tmp_path, monkeypatch, f'echo "${{GIT_TERMINAL_PROMPT-unset}}" > "{marker}"')

    vcs.branch_exists(repo, "main")

    assert marker.read_text().strip() == "0"


def test_a_git_command_that_runs_past_its_limit_is_a_vcs_error_that_says_it_timed_out(tmp_path, vcs, monkeypatch):
    repo = make_repo(tmp_path)
    git_shim(tmp_path, monkeypatch, 'case "$*" in *show-ref*) exec sleep 60;; esac')
    monkeypatch.setattr("delegate.adapters.git._GIT_TIMEOUT", 1)

    with pytest.raises(VcsError, match="timed out after 1 seconds") as error:
        vcs.branch_exists(repo, "main")

    assert error.value.directory == repo
    assert "show-ref" in str(error.value)


def test_the_clone_of_a_verifier_copy_has_its_own_longer_limit(tmp_path, vcs, monkeypatch):
    repo = make_repo(tmp_path)
    vcs.create_branch(repo, "run/demo", "main")
    vcs.create_branch(repo, "run/demo-a", "main")
    copy = tmp_path / "copy"
    git_shim(tmp_path, monkeypatch, 'case "$*" in *" clone "*) exec sleep 60;; esac')
    # The general limit is shorter than the clone limit: only the clone gets the time that it needs.
    monkeypatch.setattr("delegate.adapters.git._GIT_TIMEOUT", 1)
    monkeypatch.setattr("delegate.adapters.git._CLONE_TIMEOUT", 2)

    with pytest.raises(VcsError, match="timed out after 2 seconds"):
        vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert not copy.exists()
