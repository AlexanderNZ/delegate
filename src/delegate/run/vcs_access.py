"""The calls of the version-control port that the engine, the run of the gates and the rules of a retry share (#339, #341).

`vcs_errors` turns a failure of the port into an `EngineError` with the same message. `branch_tip` reads the tip of a branch
through it, and `holds_commit` tells whether a commit holds another. `head_commit` and `worktree_environment` read the HEAD and the
environment of a worktree. They start no process: the port does.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..ports.vcs import VcsError, VersionControl, WorktreeState
from .errors import EngineError


class KnownTree:
    """What a phase of a step knows about its worktree for the next phase, so that the next phase starts no git process for it (#348).

    A phase `give`s the HEAD when it read it after the last session, rebase or gate that can move HEAD. The next phase
    `take`s it. A phase that read the paths of the changes after a session that leaves the tree as it was gives them with
    `give_paths`, and the next phase takes them with `take_paths`. A `take` empties the note, so a value is used once: a
    phase that finds no note reads the value itself.
    """

    def __init__(self) -> None:
        self._head: str | None = None
        self._paths: frozenset[str] | None = None

    def give(self, head: str) -> None:
        self._head = head

    def take(self) -> str | None:
        head, self._head = self._head, None
        return head

    def give_paths(self, paths: frozenset[str]) -> None:
        self._paths = paths

    def take_paths(self) -> frozenset[str] | None:
        paths, self._paths = self._paths, None
        return paths


@contextmanager
def vcs_errors() -> Iterator[None]:
    """Turn a failure of the version-control port into an EngineError with the same message."""
    try:
        yield
    except VcsError as error:
        raise EngineError(str(error)) from None


def branch_tip(vcs: VersionControl, repo: Path, branch: str) -> str:
    """The id of the newest commit of the branch. Raise EngineError when there is no such branch."""
    with vcs_errors():
        return vcs.branch_tip(repo, branch)


def holds_commit(vcs: VersionControl, repo: Path, ancestor: str, descendant: str) -> bool:
    """Tell whether `descendant` holds the commit `ancestor`. A name that is no commit does not hold anything."""
    try:
        return vcs.is_ancestor(repo, ancestor, descendant)
    except VcsError:
        return False


def head_commit(vcs: VersionControl, worktree: Path) -> str:
    """The id of the commit that the worktree has checked out."""
    with vcs_errors():
        return vcs.head_commit(worktree)


def worktree_state(vcs: VersionControl, worktree: Path) -> WorktreeState:
    """The HEAD of the worktree and its uncommitted changes, from one read of the port (#348)."""
    with vcs_errors():
        return vcs.worktree_state(worktree)


def worktree_environment(vcs: VersionControl, worktree: Path) -> dict[str, str]:
    """The environment entries that carry the settings of the worktree (the push guard, the file watcher off) to a process of its ticket (#221)."""
    with vcs_errors():
        return vcs.worktree_environment(worktree)
