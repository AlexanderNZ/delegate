"""The git backend of the version-control port in `delegate.ports.vcs`.

Each method runs git through `subprocess`, with the path of the repository or
worktree as the working directory of the command (`git -C`). A command that
fails raises `VcsError` with the command and the message of git.

Every worktree and every copy that this backend makes runs with
`core.fsmonitor=false`, whatever the user sets. With the setting on, git starts a
`git fsmonitor--daemon` for each repository it touches, and the daemon outlives
its directory (issue #30). The worktree gets the setting in its own
configuration. The copy gets it in its own repository, which no other directory
shares.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..ports.vcs import CONFLICT, FAILED, REBASED, Commit, PathChange, RebaseResult, VcsError

# The limit, in seconds, for the command that stops the fsmonitor daemon of a directory.
_FSMONITOR_STOP_TIMEOUT: int = 30


def _run(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in `directory` and return the result, whatever the exit code is."""
    return subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True)


def _git_raw(directory: Path, *args: str) -> str:
    """Run git in `directory` and return its output as it is. Raise VcsError when git fails."""
    result = _run(directory, *args)
    if result.returncode != 0:
        raise VcsError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.returncode}")
    return result.stdout


def _git(directory: Path, *args: str) -> str:
    """Run git in `directory` and return its output without the surrounding white space. Raise VcsError when git fails."""
    return _git_raw(directory, *args).strip()


def _rebase_in_progress(worktree: Path) -> bool:
    """Tell whether a rebase is in progress in the worktree. `git rebase --abort` fails when none is."""
    return any(
        (worktree / _git(worktree, "rev-parse", "--git-path", name)).exists() for name in ("rebase-merge", "rebase-apply")
    )


class GitVersionControl:
    """The version-control port, kept by git."""

    def repository_root(self, path: Path) -> Path:
        return Path(_git(path, "rev-parse", "--show-toplevel"))

    def shared_data_directory(self, path: Path) -> Path:
        top = self.repository_root(path)
        return (top / _git(top, "rev-parse", "--git-common-dir")).resolve()

    def branch_exists(self, repo: Path, branch: str) -> bool:
        return _run(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0

    def create_branch(self, repo: Path, branch: str, start: str) -> None:
        _git(repo, "branch", branch, start)

    def branch_tip(self, repo: Path, branch: str) -> str:
        return _git(repo, "rev-parse", "--verify", f"refs/heads/{branch}")

    def commit_of(self, path: Path, ref: str) -> str:
        return _git(path, "rev-parse", "--verify", f"{ref}^{{commit}}")

    def head_commit(self, worktree: Path) -> str:
        return _git(worktree, "rev-parse", "HEAD")

    def ensure_worktree(self, repo: Path, worktree: Path, branch: str, start: str) -> None:
        worktree = worktree.absolute()
        if worktree.is_dir():
            if _rebase_in_progress(worktree):
                _git(worktree, "rebase", "--abort")
            current = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
            if current != branch:
                raise VcsError(f"worktree {worktree} holds {current!r}, not the branch {branch!r} of its ticket")
        else:
            _git(repo, "worktree", "prune")
            if self.branch_exists(repo, branch):
                _git(repo, "-c", "core.fsmonitor=false", "worktree", "add", "-q", str(worktree), branch)
            else:
                _git(repo, "-c", "core.fsmonitor=false", "worktree", "add", "-q", "-b", branch, str(worktree), start)
        # Without this extension `--worktree` writes to the configuration that every worktree shares.
        _git(repo, "config", "extensions.worktreeConfig", "true")
        _git(worktree, "config", "--worktree", "core.fsmonitor", "false")

    def remove_worktree(self, repo: Path, worktree: Path) -> None:
        worktree = worktree.absolute()
        self.stop_file_watcher(worktree)
        _git(repo, "worktree", "remove", "--force", str(worktree))

    def rebase_onto(self, worktree: Path, onto: str) -> RebaseResult:
        rebase = _run(worktree, "rebase", onto)
        if rebase.returncode == 0:
            return RebaseResult(REBASED)
        # A list of conflicts that git cannot give is an error, and the rebase is left as it is.
        conflicts = tuple(_git(worktree, "diff", "--name-only", "--diff-filter=U").splitlines())
        if _rebase_in_progress(worktree):
            _git(worktree, "rebase", "--abort")
        if conflicts:
            return RebaseResult(CONFLICT, conflicts=conflicts)
        return RebaseResult(FAILED, detail=rebase.stderr.strip() or str(rebase.returncode))

    def commits_since(self, path: Path, since: str, until: str | None = None) -> list[Commit]:
        # The trailing `--` tells git that both names are revisions, even when a file has the same name.
        out = _git(path, "log", "--format=%H %s", f"{since}..{until or 'HEAD'}", "--")
        commits = []
        for line in out.splitlines():
            commit_id, _, subject = line.partition(" ")
            commits.append(Commit(commit_id, subject))
        return commits

    def changed_paths(
        self, path: Path, since: str, until: str | None = None, *, from_merge_base: bool = False, follow_renames: bool = False
    ) -> list[str]:
        spec = f"{since}{'...' if from_merge_base else '..'}{until or 'HEAD'}"
        if follow_renames:
            return _git_raw(path, "diff", "--name-only", spec).splitlines()
        return _git(path, "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", spec).splitlines()

    def diff_text(self, path: Path, since: str, until: str, *, from_merge_base: bool = False) -> str:
        return _git_raw(path, "diff", f"{since}{'...' if from_merge_base else '..'}{until}")

    def uncommitted_changes(self, worktree: Path) -> list[PathChange]:
        # The state code starts with a space for a change that is not staged, so the output must not be stripped.
        out = _git_raw(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
        return [PathChange(entry[:2], entry[3:]) for entry in out.split("\0") if entry]

    def hooks_directory(self, worktree: Path) -> Path:
        return Path(_git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))

    def use_hooks_directory(self, repo: Path, worktree: Path, hooks: Path) -> None:
        # Without this extension `--worktree` writes to the configuration that every worktree shares.
        _git(repo, "config", "extensions.worktreeConfig", "true")
        _git(worktree, "config", "--worktree", "core.hooksPath", str(hooks))

    def is_ancestor(self, repo: Path, ancestor: str, descendant: str) -> bool:
        # Exit status 1 means no. Any other failure, such as a name that is no commit, is an error.
        result = _run(repo, "merge-base", "--is-ancestor", ancestor, descendant)
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise VcsError(
            f"git merge-base --is-ancestor {ancestor} {descendant} failed: {result.stderr.strip() or result.returncode}"
        )

    def fast_forward_branch(self, repo: Path, branch: str, to_commit: str) -> bool:
        tip = self.branch_tip(repo, branch)
        # Any failure of the ancestor check, such as a `to_commit` that names no commit, is the answer no.
        if _run(repo, "merge-base", "--is-ancestor", tip, to_commit).returncode != 0:
            return False
        target = self.commit_of(repo, to_commit)
        # The old value in the last argument makes the move fail if the branch moved in the meantime.
        _git(repo, "update-ref", f"refs/heads/{branch}", target, tip)
        return True

    def make_verifier_copy(self, repo: Path, copy: Path, branch: str, base: str) -> None:
        # The clone starts on the branch that the HEAD of the repository names, and that branch can be `base`.
        # So the clone checks out `branch` first, which frees the other names, and then sets `base` by force.
        copy = copy.absolute()
        existed = copy.exists()
        try:
            _git(repo, "clone", "-q", "-c", "core.fsmonitor=false", "--no-checkout", str(repo), str(copy))
            _git(copy, "checkout", "-q", "-B", branch, f"origin/{branch}")
            _git(copy, "branch", "-f", base, f"origin/{base}")
            _git(copy, "remote", "remove", "origin")
        except VcsError:
            # A failed copy leaves nothing behind, but a directory that was there before stays.
            if not existed:
                self.stop_file_watcher(copy)
                shutil.rmtree(copy, ignore_errors=True)
            raise

    def stop_file_watcher(self, path: Path) -> None:
        # Git exits with an error when no daemon runs, or when the directory is not a repository. Both are fine.
        try:
            subprocess.run(
                ["git", "-C", str(path), "fsmonitor--daemon", "stop"],
                capture_output=True, text=True, timeout=_FSMONITOR_STOP_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            pass
