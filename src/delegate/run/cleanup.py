"""The clean-up of the ticket worktrees and the ticket branches of a run. The engine and `delegate clean` share it.

The engine cleans up at the end of a run that ends `built` (#167). `delegate
clean` (`delegate.run.clean`) cleans up a run that ended `built` before that
clean-up existed, or with `keep-worktrees = true` (#168). Both use the rules of
`clean_tickets`:

- A ticket worktree with no change that is not committed goes, through the port,
  which stops its file watcher first. Its push-guard hooks directory goes too.
- A ticket worktree with a change that is not committed stays, and so does its
  branch. Its file watcher is stopped, because nothing else stops it.
- A ticket branch goes only when the holder branch (the run branch) holds its
  newest commit, with the safe delete of the port. Otherwise it stays.
- A failure of one item keeps that item, and the clean-up goes on with the others.

The module starts no process and imports no adapter: the port does the work.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..ports.vcs import VcsError, VersionControl

# The most paths of a kept worktree that the reason names.
CLEANUP_PATHS_NAMED: int = 10


@dataclass(frozen=True)
class TicketTree:
    """The worktree of one ticket, its branch, and the directory of its push-guard hooks."""

    worktree: Path
    branch: str
    hooks: Path


@dataclass
class Cleanup:
    """What a clean-up removed, what it kept and why, and the errors among the reasons.

    With `dry_run`, the lists say what the clean-up would remove and keep. An
    error is a reason that comes from a failure of the port, not from a rule.
    """

    worktrees_removed: list[str] = field(default_factory=list)
    branches_deleted: list[str] = field(default_factory=list)
    worktrees_kept: list[dict[str, str]] = field(default_factory=list)
    branches_kept: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def fields(self) -> dict[str, object]:
        """The fields of the `cleanup` event of the journal."""
        return {
            "worktrees_removed": self.worktrees_removed, "branches_deleted": self.branches_deleted,
            "worktrees_kept": self.worktrees_kept, "branches_kept": self.branches_kept,
        }

    def keep_branch(self, branch: str, reason: str, *, error: bool = False) -> None:
        self.branches_kept.append({"branch": branch, "reason": reason})
        if error:
            self.errors.append(f"branch {branch}: {reason}")


def clean_tickets(
    vcs: VersionControl, repo: Path, trees: list[TicketTree], holder: str, holder_name: str = "the run branch",
    *, dry_run: bool = False,
) -> Cleanup:
    """Remove the worktree and the hooks of each ticket, and delete each ticket branch that the branch `holder` holds.

    `holder_name` names the holder in the reason of a kept branch. When the
    holder does not exist, every ticket branch stays. A worktree whose
    directory does not exist is gone already, and its branch can still go.
    With `dry_run` nothing changes, and the result says what would change.
    """
    result = Cleanup()
    try:
        held = vcs.branch_exists(repo, holder)
    except VcsError as failure:
        held, result.errors = False, [f"branch {holder}: {failure}"]
    for tree in trees:
        kept: str | None = None
        if tree.worktree.is_dir():
            kept, error = _worktree_reason(vcs, tree.worktree)
            if kept is None:
                if not dry_run:
                    try:
                        vcs.remove_worktree(repo, tree.worktree)
                    except VcsError as failure:
                        kept, error = str(failure), True
            if kept is None:
                result.worktrees_removed.append(str(tree.worktree))
                if not dry_run:
                    shutil.rmtree(tree.hooks, ignore_errors=True)
            else:
                result.worktrees_kept.append({"worktree": str(tree.worktree), "reason": kept})
                if error:
                    result.errors.append(f"worktree {tree.worktree}: {kept}")
                if not dry_run:
                    vcs.stop_file_watcher(tree.worktree)
        try:
            if not vcs.branch_exists(repo, tree.branch):
                continue
            if kept is not None:
                result.keep_branch(tree.branch, "its worktree is kept")
            elif not held:
                result.keep_branch(tree.branch, f"{holder_name} {holder!r} does not exist, so nothing shows that its commits are held")
            elif _delete(vcs, repo, tree.branch, holder, dry_run):
                result.branches_deleted.append(tree.branch)
            else:
                result.keep_branch(tree.branch, f"{holder_name} {holder!r} does not hold its commits")
        except VcsError as failure:
            result.keep_branch(tree.branch, str(failure), error=True)
    return result


def remove_empty_directories(*directories: Path) -> None:
    """Remove each directory that is empty. A directory that holds a kept worktree, or that does not exist, stays as it is."""
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass


def delete_held_branch(vcs: VersionControl, repo: Path, branch: str, holder: str, *, dry_run: bool = False) -> bool:
    """Delete `branch` when `holder` holds its newest commit, and return True. With `dry_run`, only tell whether it would go.

    Raise VcsError when the port refuses the delete for another cause.
    """
    return _delete(vcs, repo, branch, holder, dry_run)


def _delete(vcs: VersionControl, repo: Path, branch: str, holder: str, dry_run: bool) -> bool:
    if dry_run:
        return vcs.is_ancestor(repo, f"refs/heads/{branch}", f"refs/heads/{holder}")
    return vcs.delete_branch(repo, branch, holder)


def _worktree_reason(vcs: VersionControl, worktree: Path) -> tuple[str | None, bool]:
    """The reason that the worktree stays, or None when it can go, and whether the reason is an error."""
    try:
        changes = vcs.uncommitted_changes(worktree)
    except VcsError as error:
        return str(error), True
    if not changes:
        return None, False
    paths = [change.path for change in changes]
    named = ", ".join(paths[:CLEANUP_PATHS_NAMED])
    if len(paths) > CLEANUP_PATHS_NAMED:
        named += f", and {len(paths) - CLEANUP_PATHS_NAMED} more"
    return f"it holds changes that are not committed: {named}", False
