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
from .epilog import COMMANDS_PAGE, HelpFormatter, epilog

# The exit status of an error: an unknown run, a journal that cannot be read, or a removal that failed.
EXIT_ERROR: int = 1

# The exit status of a usage error, as argparse gives it.
EXIT_USAGE: int = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delegate clean",
        description=(
            "Remove the ticket worktrees, the merged ticket branches and the leftover verifier copies of runs that ended built. "
            "A run that is active, failed, paused or stopped keeps everything. "
            "With --abandon, end a run that is not built for good, and remove what it left."
        ),
        epilog=epilog(COMMANDS_PAGE),
        formatter_class=HelpFormatter,
    )
    parser.add_argument("run_id", nargs="*", help="a run to clean: the run id or the short run id; give more than one to clean more")
    parser.add_argument("--ended", action="store_true", help="clean every run whose newest run-end has the result built")
    parser.add_argument("--merged", action="store_true", help="also delete the run branch of each cleaned run when the base branch or the default branch holds it")
    parser.add_argument("--dry-run", action="store_true", help="print what the command would remove and keep, and change nothing")
    parser.add_argument(
        "--abandon", metavar="RUN_ID",
        help="end the run for good: it is paused, halted, failed or stopped, and no process works on it; write run-end with the result abandoned, "
        "then remove its worktrees, ticket branches and hooks, and keep the run branch; needs --reason",
    )
    parser.add_argument("--reason", help="with --abandon, the reason: the text goes to the journal of the run and to delegate status")
    parser.add_argument("--delete-run-branch", action="store_true", help="with --abandon, also delete the run branch")
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository that holds the runs; default is the current directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.abandon is not None:
        return _abandon(parser, args)
    if args.reason is not None or args.delete_run_branch:
        parser.error("--reason and --delete-run-branch need --abandon")
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


def _abandon(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """`delegate clean --abandon <run-id> --reason <text>`: end the run for good and remove what it left (#201)."""
    if args.reason is None or not args.reason.strip():
        parser.error("--abandon needs --reason with the reason, which goes to the journal of the run")
    if args.run_id or args.ended or args.merged or args.dry_run:
        parser.error("--abandon takes one run and no run id, --ended, --merged or --dry-run")
    try:
        result = clean.abandon(args.repo, args.abandon, args.reason.strip(), args.delete_run_branch, GitVersionControl())
    except clean.AbandonError as error:
        print(f"delegate clean: {error}", file=sys.stderr)
        return EXIT_ERROR
    for line in result.lines():
        print(line)
    for error in result.run.errors:
        print(f"delegate clean: run {result.run.run_id}: {error}", file=sys.stderr)
    return EXIT_ERROR if result.run.errors else 0
