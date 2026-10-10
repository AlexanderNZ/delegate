"""The version-control port: the interface that the engine drives to keep the work of a run.

The engine speaks of branches, commits, worktrees and rebases. It never names a
command of the tool that keeps them. An adapter in `delegate.adapters`
implements the interface, and the command-line driver hands it to the engine.
A test passes the real adapter on a temporary repository, or any object with
the same methods.

A `ref` is the name of a branch or the id of a commit. A `repo` is the path of
the main checkout, or of any directory inside it. A `worktree` is the path of
a working directory that the port made with `ensure_worktree`.

Every operation that fails raises `VcsError`. The message gives the cause.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# The three outcomes of a rebase.
REBASED: str = "rebased"
CONFLICT: str = "conflict"
FAILED: str = "failed"
REBASE_STATUSES: tuple[str, ...] = (REBASED, CONFLICT, FAILED)


class VcsError(RuntimeError):
    """An operation of the port failed. The message names the operation and the cause.

    A backend that runs a command also gives the parts of the message: the command
    (`operation`), the directory where it ran (`directory`) and the reason (`cause`).
    A caller that must name the directory in its own message reads them.
    """

    def __init__(
        self, message: str, *, operation: str | None = None, directory: Path | None = None, cause: str | None = None
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.directory = directory
        self.cause = cause


@dataclass(frozen=True)
class Commit:
    """One commit: its id and the first line of its message."""

    id: str
    subject: str


@dataclass(frozen=True)
class PathChange:
    """One path of a checkout that differs from its newest commit, and how it differs.

    `code` is the two-letter state of the path: the first letter is the state in
    the staging area, the second is the state in the working directory, and a
    space means no change there. `??` is a path that no commit holds.
    """

    code: str
    path: str


@dataclass(frozen=True)
class RebaseResult:
    """What a rebase gave back. `status` is one of REBASE_STATUSES.

    `conflicts` holds the paths that the two sides changed in different ways, and
    it is not empty only for `CONFLICT`. `detail` holds the reason when the
    rebase did not start or did not end, and it is empty for `REBASED`.
    A rebase that stops is undone: the branch and the worktree hold the state
    from before the rebase.
    """

    status: str
    conflicts: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in REBASE_STATUSES:
            raise ValueError(f"rebase status {self.status!r} is not one of: {', '.join(REBASE_STATUSES)}")


@runtime_checkable
class VersionControl(Protocol):
    """The interface of a version-control adapter."""

    def repository_root(self, path: Path) -> Path:
        """The top directory of the checkout that holds `path`."""
        ...

    def shared_data_directory(self, path: Path) -> Path:
        """The absolute path of the directory that the main checkout and all its worktrees share.

        The engine keeps its state beside the data of the repository, so that a
        worktree and the main checkout find the same state.
        """
        ...

    def branch_exists(self, repo: Path, branch: str) -> bool:
        """Tell whether a branch of that name exists. A tag of that name does not count."""
        ...

    def create_branch(self, repo: Path, branch: str, start: str) -> None:
        """Make the branch `branch` at `start`, with no checkout. Raise VcsError when the branch exists."""
        ...

    def branch_tip(self, repo: Path, branch: str) -> str:
        """The id of the newest commit of the branch. Raise VcsError when there is no such branch."""
        ...

    def commit_of(self, path: Path, ref: str) -> str:
        """The id of the commit that `ref` names. Raise VcsError when it names no commit."""
        ...

    def head_commit(self, worktree: Path) -> str:
        """The id of the commit that the worktree has checked out."""
        ...

    def ensure_worktree(self, repo: Path, worktree: Path, branch: str, start: str) -> None:
        """Make a worktree at `worktree` with `branch` checked out, or reuse the one that is there.

        A branch that does not exist starts at `start`. A worktree that a killed
        run left is reused as it is, except that a rebase which the kill stopped
        is undone. The worktree starts no background watcher of the file system,
        and the setting that stops one holds for this worktree only.
        Raise VcsError when `worktree` holds another branch.
        """
        ...

    def remove_worktree(self, repo: Path, worktree: Path) -> None:
        """Remove the worktree and its directory, whatever it holds. The branch stays."""
        ...

    def rebase_onto(self, worktree: Path, onto: str) -> RebaseResult:
        """Put the commits of the worktree's branch on top of `onto`.

        A conflict or a refusal gives a result, not an error, and leaves the
        worktree as it was before.
        """
        ...

    def commits_since(self, path: Path, since: str, until: str | None = None) -> list[Commit]:
        """The commits that `until` holds and `since` does not, newest first.

        `until` is the commit that `path` has checked out when it is None.
        """
        ...

    def changed_paths(
        self, path: Path, since: str, until: str | None = None, *, from_merge_base: bool = False, follow_renames: bool = False
    ) -> list[str]:
        """The paths whose content differs between `since` and `until`, in the order of their names.

        `until` is the commit that `path` has checked out when it is None. With
        `from_merge_base` the comparison starts at the newest commit that both
        hold, so the work that `since` gained on its own does not count. A
        renamed path counts as its old name and its new name. With
        `follow_renames` it counts as its new name only. Raise VcsError when a
        name is no commit.
        """
        ...

    def diff_text(self, path: Path, since: str, until: str, *, from_merge_base: bool = False) -> str:
        """The text of the changes between `since` and `until`, as a reader sees them, exactly as the tool wrote it.

        `from_merge_base` is as in `changed_paths`. The text is empty when
        nothing differs. Raise VcsError when a name is no commit.
        """
        ...

    def uncommitted_changes(self, worktree: Path) -> list[PathChange]:
        """Each path of the worktree that differs from its newest commit, one entry for each file.

        A new file in a new directory is listed as a file. A path that the
        repository ignores is not listed. A rename is two entries. The order is
        the order of the paths.
        """
        ...

    def hooks_directory(self, worktree: Path) -> Path:
        """The absolute path of the directory whose hooks run for the worktree."""
        ...

    def use_hooks_directory(self, repo: Path, worktree: Path, hooks: Path) -> None:
        """Make the hooks in the directory `hooks` run for `worktree`, and for no other checkout of the repository."""
        ...

    def is_ancestor(self, repo: Path, ancestor: str, descendant: str) -> bool:
        """Tell whether the commit `descendant` holds the commit `ancestor`. A commit holds itself."""
        ...

    def fast_forward_branch(self, repo: Path, branch: str, to_commit: str) -> bool:
        """Move the branch to `to_commit` when the branch holds only commits that `to_commit` holds.

        Return True when the branch moved. Return False, and leave the branch
        where it is, when it holds a commit that `to_commit` does not hold, or
        when `to_commit` names no commit.
        Raise VcsError when there is no such branch.
        """
        ...

    def make_verifier_copy(self, repo: Path, copy: Path, branch: str, base: str) -> None:
        """Make a self-contained copy of the repository at `copy`, with `branch` checked out and `base` at its place.

        The copy has its own data and no link to `repo`, so that its user can
        break it and cannot reach `repo` through it. The copy starts no
        background watcher of the file system.
        """
        ...

    def stop_file_watcher(self, path: Path) -> None:
        """Stop the background watcher of the file system for the checkout at `path`, before its directory goes.

        A watcher outlives its directory. This is a clean-up and it is quiet:
        nothing to stop, or a path that is no checkout, is not an error.
        """
        ...
