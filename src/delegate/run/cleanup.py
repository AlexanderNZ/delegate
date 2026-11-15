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
  newest commit. Otherwise it stays. A caller can name more holders (`Holder`):
  the branch goes when one of them holds it (#267). The port reads the branches
  one time and each holder one time, and then deletes all the branches that go
  with one call (#347).
- A branch that a retry with a new text kept (`TicketTree.failed`) goes whatever
  it holds, when the caller allows it (`discard_failed`). The engine does not
  allow it at the end of a run: the coordinator reads that work first.
- A failure of one item keeps that item, and the clean-up goes on with the others.

The module starts no process and imports no adapter: the port does the work.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..ports.vcs import VcsError, VersionControl

# The most paths of a kept worktree that the reason names.
CLEANUP_PATHS_NAMED: int = 10


@dataclass(frozen=True)
class Holder:
    """A branch that can hold the commits of another branch, and the words that name it in a reason (#267)."""

    branch: str
    name: str = "the run branch"


@dataclass(frozen=True)
class TicketTree:
    """The worktree of one ticket, its branch, and the directory of its push-guard hooks.

    `failed` marks a branch that a retry with a new text kept (#204): it holds the work of a step that failed, which
    the run branch does not hold. It has no worktree.
    """

    worktree: Path
    branch: str
    hooks: Path
    failed: bool = False


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
    *, dry_run: bool = False, discard_failed: bool = False, also: Sequence[Holder] = (),
) -> Cleanup:
    """Remove the worktree and the hooks of each ticket, and delete each ticket branch that the branch `holder` holds.

    `holder_name` names the holder in the reason of a kept branch. When the
    holder does not exist, every ticket branch stays. A worktree whose
    directory does not exist is gone already, and its branch can still go.
    With `dry_run` nothing changes, and the result says what would change.
    With `discard_failed` the branch of a tree with `failed` goes, whatever it holds.
    A branch in `also` can hold the ticket branch in place of `holder` (#267): the branch goes when `holder` or one of them holds it.
    """
    result = Cleanup()
    # One read of all branches serves each check of a ticket branch, and one read for each holder says which branches it holds (#347).
    # When that read fails, no branch is known: each ticket branch stays, with the failure as its reason.
    unread: str | None = None
    try:
        tips = vcs.branch_tips(repo)
        holders = _holders(tips, [Holder(holder, holder_name), *also])
    except VcsError as failure:
        tips, holders, result.errors, unread = {}, [], [f"branch {holder}: {failure}"], str(failure)
    held = _Held(vcs, repo, [h for h, exists in holders if exists])
    pending: list[str] = []  # the branches to delete, in the order of the tickets
    for tree in trees:
        if unread is not None and tree.failed:
            result.keep_branch(tree.branch, unread, error=True)
            continue
        if tree.failed:
            if tree.branch not in tips:
                continue
            if discard_failed:
                pending.append(tree.branch)
            else:
                result.keep_branch(tree.branch, "it holds the work of a step that a retry started again; delegate clean removes it")
            continue
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
        if unread is not None:
            result.keep_branch(tree.branch, unread, error=True)
            continue
        if tree.branch not in tips:
            continue
        if kept is not None:
            result.keep_branch(tree.branch, "its worktree is kept")
            continue
        try:
            holds = held.holds(tree.branch)
        except VcsError as failure:
            result.keep_branch(tree.branch, str(failure), error=True)
            continue
        if holds:
            pending.append(tree.branch)
        else:
            result.keep_branch(tree.branch, _reason(holders))
    _delete_all(vcs, repo, pending, result, dry_run)
    return result


class _Held:
    """Which branches the holders hold. The first question reads each holder one time: one git process for each holder."""

    def __init__(self, vcs: VersionControl, repo: Path, holders: Sequence[Holder]) -> None:
        self._vcs, self._repo, self._holders = vcs, repo, holders
        self._held: frozenset[str] | None = None

    def holds(self, branch: str) -> bool:
        """Tell whether one of the holders holds the newest commit of `branch`. Raise VcsError when a holder cannot be read."""
        if self._held is None:
            held: set[str] = set()
            for holder in self._holders:
                held |= self._vcs.branches_held_by(self._repo, holder.branch)
            self._held = frozenset(held)
        return branch in self._held


def _delete_all(vcs: VersionControl, repo: Path, branches: list[str], result: Cleanup, dry_run: bool) -> None:
    """Delete `branches` with one call of the port, and add each to `result.branches_deleted`. With `dry_run`, delete none.

    When the one call fails, the port deleted the branches that it could. The branches are read again: a branch that is gone
    went, and each branch that is left is deleted alone, so that a failure keeps that branch only.
    """
    if not dry_run and branches:
        try:
            vcs.discard_branches(repo, branches)
        except VcsError:
            try:
                left = vcs.branch_tips(repo).keys()
            except VcsError as failure:
                for branch in branches:
                    result.keep_branch(branch, str(failure), error=True)
                return
            for branch in branches:
                if branch not in left:
                    result.branches_deleted.append(branch)
                    continue
                try:
                    vcs.discard_branch(repo, branch)
                except VcsError as failure:
                    result.keep_branch(branch, str(failure), error=True)
                else:
                    result.branches_deleted.append(branch)
            return
    result.branches_deleted += branches


def remove_empty_directories(*directories: Path) -> None:
    """Remove each directory that is empty. A directory that holds a kept worktree, or that does not exist, stays as it is."""
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass


def delete_held_branch(vcs: VersionControl, repo: Path, branch: str, holders: Sequence[Holder], *, dry_run: bool = False) -> bool:
    """Delete `branch` when one of `holders` holds its newest commit, and return True. With `dry_run`, only tell whether it would go.

    A holder that does not exist holds nothing, and it is no error (#267). Raise VcsError when the port refuses the
    delete for another cause.
    """
    return _delete_held(vcs, repo, branch, [h for h, exists in _holders(vcs.branch_tips(repo), holders) if exists], dry_run)


def not_held_reason(vcs: VersionControl, repo: Path, holders: Sequence[Holder]) -> str:
    """The reason that a branch stays when no holder holds it: it names each holder that was checked (#267)."""
    return _reason(_holders(vcs.branch_tips(repo), holders))


def _reason(holders: Sequence[tuple[Holder, bool]]) -> str:
    """The reason of `not_held_reason` from the holders with the fact that each branch exists. A holder that does not exist is named so."""
    if len(holders) == 1:
        (holder, exists), = holders
        if not exists:
            return f"{holder.name} {holder.branch!r} does not exist, so nothing shows that its commits are held"
        return f"{holder.name} {holder.branch!r} does not hold its commits"
    if all(exists for _, exists in holders):
        return "neither " + " nor ".join(f"{h.name} {h.branch!r}" for h, _ in holders) + " holds its commits"
    return "; ".join(
        f"{h.name} {h.branch!r} {'does not hold its commits' if exists else 'does not exist'}" for h, exists in holders
    )


def _holders(branches: Mapping[str, str], holders: Sequence[Holder]) -> list[tuple[Holder, bool]]:
    """Each holder with the fact that its branch is in `branches`. A branch that two holders name counts once, with the first name."""
    seen: set[str] = set()
    found: list[tuple[Holder, bool]] = []
    for holder in holders:
        if holder.branch not in seen:
            seen.add(holder.branch)
            found.append((holder, holder.branch in branches))
    return found


def _delete_held(vcs: VersionControl, repo: Path, branch: str, holders: Sequence[Holder], dry_run: bool) -> bool:
    """Delete `branch` when the first of `holders` that holds it does; True when it went, or with `dry_run` when it would."""
    return any(_delete(vcs, repo, branch, holder.branch, dry_run) for holder in holders)


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
