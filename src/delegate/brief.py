"""The `verifier-brief` command: the command-line driver of `delegate.run.brief`.

The use case lives in the run context, which never imports an adapter. This module is the composition point of
the command: it makes the git backend of the version-control port and hands it to the use case.
"""

from __future__ import annotations

import sys

from .adapters.git import GitVersionControl
from .run import brief


def main(argv: list[str] | None = None) -> int:
    return brief.main(argv, GitVersionControl())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
