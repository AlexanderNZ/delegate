"""The `delegate status` command: parse the arguments, build the git backend, call the report of the run context and print it.

The use case lives in `delegate.run.status`, which never imports an adapter. This module is the composition point
of the command: it makes the git backend of the version-control port and hands it to the use case.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..adapters.git import GitVersionControl
from ..run import status
from ..run.journal import JournalError
from ..run.runs import RunsError

# The exit status of an input that cannot be read: no run, an unknown run id, a journal that is not valid.
EXIT_ERROR: int = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delegate status", description="Print the state of each ticket of a run, from its journal.")
    parser.add_argument("run_id", nargs="?", help="the run to show; default is the newest run of the repository")
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository that holds the run; default is the current directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        lines = status.report(args.repo, args.run_id, GitVersionControl())
    except (RunsError, JournalError) as error:
        print(f"delegate status: {error}", file=sys.stderr)
        return EXIT_ERROR
    print("\n".join(lines))
    return 0
