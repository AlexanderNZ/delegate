"""The `delegate clean` command: parse the arguments, build the git backend, and clean the runs.

The use case lives in `delegate.run.clean`, which never imports an adapter. This module is the composition point of
the command: it makes the git backend of the version-control port and hands it to the use case.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..adapters.git import GitVersionControl
from ..run import clean

# The exit status of an error: an unknown run, a journal that cannot be read, or a removal that failed.
EXIT_ERROR: int = 1

# The exit status of a usage error, as argparse gives it.
EXIT_USAGE: int = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delegate clean",
        description=(
            "Remove the ticket worktrees, the merged ticket branches and the leftover verifier copies of runs that ended built. "
            "A run that is active, failed, paused or stopped keeps everything."
        ),
    )
    parser.add_argument("run_id", nargs="*", help="a run to clean: the run id or the short run id; give more than one to clean more")
    parser.add_argument("--ended", action="store_true", help="clean every run whose newest run-end has the result built")
    parser.add_argument("--merged", action="store_true", help="also delete the run branch of each cleaned run when the base branch holds it")
    parser.add_argument("--dry-run", action="store_true", help="print what the command would remove and keep, and change nothing")
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository that holds the runs; default is the current directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.run_id and not args.ended:
        parser.print_usage(sys.stderr)
        print("delegate clean: error: give a run id, or --ended for every run that ended built", file=sys.stderr)
        return EXIT_USAGE
    try:
        result = clean.clean(args.repo, args.run_id, args.ended, args.merged, args.dry_run, GitVersionControl())
    except clean.CleanError as error:
        print(f"delegate clean: {error}", file=sys.stderr)
        return EXIT_ERROR
    for run in result.runs:
        print(run.line(args.dry_run))
    for error in [*result.errors, *(f"run {run.run_id}: {e}" for run in result.runs for e in run.errors)]:
        print(f"delegate clean: {error}", file=sys.stderr)
    return 0 if result.ok else EXIT_ERROR
