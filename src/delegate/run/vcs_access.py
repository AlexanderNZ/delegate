"""Three calls of the version-control port that the engine and the rules of a retry share (#339).

`vcs_errors` turns a failure of the port into an `EngineError` with the same message. `branch_tip` reads the tip of a branch
through it, and `holds_commit` tells whether a commit holds another. They start no process: the port does.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..ports.vcs import VcsError, VersionControl
from .errors import EngineError


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
