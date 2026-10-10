"""The `delegate watch` command: the command-line driver of `delegate.run.watch`.

The use case lives in the run context, which never imports an adapter. This module is the composition point of
the command: it makes the git backend of the version-control port and hands it to the use case.
"""

from __future__ import annotations

from .adapters.git import GitVersionControl
from .run import watch


def main(argv: list[str] | None = None) -> int:
    return watch.main(argv, GitVersionControl())
