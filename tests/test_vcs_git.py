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
from delegate.ports.vcs import CONFLICT, FAILED, REBASED, Commit, RebaseResult, VcsError, VersionControl

from .support import git, make_repo
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


def commit(repo: Path, name: str, content: str, message: str) -> str:
    """Write a file in a checkout, commit it, and return the id of the new commit."""
    (repo / name).write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


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

    assert git(worktree, "config", "core.fsmonitor").strip() == "false"
    assert git_status(worktree, "fsmonitor--daemon", "status").returncode != 0


def test_the_fsmonitor_rule_holds_for_a_worktree_that_is_reused(tmp_path, vcs, monkeypatch, no_daemon_survives):
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    user_enables_fsmonitor(monkeypatch, tmp_path)
    no_daemon_survives.extend([repo, worktree])

    vcs.ensure_worktree(repo, worktree, "feature", "main")

    assert git(worktree, "config", "core.fsmonitor").strip() == "false"


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


def test_fast_forward_branch_of_a_branch_that_does_not_exist_is_an_error(tmp_path, vcs):
    repo = make_repo(tmp_path)

    with pytest.raises(VcsError):
        vcs.fast_forward_branch(repo, "no-such-branch", sha(repo, "main"))


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
