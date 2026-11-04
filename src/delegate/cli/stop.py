"""The `delegate stop` command: parse the arguments, build the git backend, and stop the run now.

The use case lives in `delegate.run.stop`, which never imports an adapter. This module is the composition point of
the command: it makes the git backend of the version-control port and hands it to the use case.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..adapters.git import GitVersionControl
from ..run import stop
from .epilog import COMMANDS_PAGE, HelpFormatter, epilog

# The seconds that the command waits for the lock of the run to be free, after it sent the signal.
WAIT_SECONDS: float = 30.0

# The exit status of a run that cannot be stopped (an unknown run, a run with no live process), and of a run whose lock is held at the end of the wait.
EXIT_REFUSED: int = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delegate stop",
        description="Stop a run now: send SIGTERM to its process, wait for the lock to be free, and print the command that goes on with the run.",
        epilog=epilog(COMMANDS_PAGE),
        formatter_class=HelpFormatter,
    )
    parser.add_argument("run_id", help="the run to stop; it must have a live process")
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository that holds the run; default is the current directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        outcome = stop.stop_run(args.repo, args.run_id, GitVersionControl(), WAIT_SECONDS)
    except stop.StopError as error:
        print(f"delegate stop: {error}", file=sys.stderr)
        return EXIT_REFUSED
    if not outcome.released:
        print(
            f"delegate stop: process {outcome.pid} still holds the lock of run {outcome.run_id} after {WAIT_SECONDS:g} seconds; "
            "it got SIGTERM. Look at the run with delegate status, or end the process by hand",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    print(f"run {outcome.run_id}: stopped")
    print(f"go on with delegate run --resume {outcome.run_id}")
    return 0
