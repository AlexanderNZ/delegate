"""The `delegate watch` command: parse the arguments, build the git backend, follow the journal through the run context and print each event.

The use case lives in `delegate.run.watch`, which never imports an adapter. This module is the composition point
of the command: it makes the git backend of the version-control port and hands it to the use case.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..adapters.git import GitVersionControl
from ..run import watch
from ..run.journal import JournalError
from ..run.runs import RunsError


def _positive(text: str) -> float:
    """An option value that is a number greater than 0."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0, not {text}")
    return value


def _position(text: str) -> int:
    """An option value that is a journal position: a whole number, 0 or more."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, not {text}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delegate watch", description="Follow the journal of a run, print each new event, and exit with the run's result.")
    parser.add_argument("run_id", nargs="?", help="the run to follow; default is the newest run of the repository")
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository that holds the run; default is the current directory")
    parser.add_argument(
        "--until", choices=[watch.UNTIL_VERDICT],
        help="also exit when a verifier verdict is written to the journal; default is to follow the run to its end",
    )
    parser.add_argument(
        "--stall-minutes", type=_positive, metavar="N",
        help="exit when the journal and the captured event streams have not changed for N minutes; N is a number greater than 0",
    )
    parser.add_argument(
        "--max-minutes", type=_positive, metavar="N",
        help="exit after N minutes, before a harness time limit, and print the position to go on from; N is a number greater than 0",
    )
    parser.add_argument(
        "--from", type=_position, default=0, metavar="POSITION", dest="from_position",
        help="go on after the event with this position, which an earlier watch printed; no event is reported twice",
    )
    parser.add_argument("--poll-seconds", type=_positive, default=1.0, help="how often to read the journal; default 1")
    return parser


def _emit(line: str) -> None:
    print(line, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = watch.WatchOptions(args.until, args.stall_minutes, args.max_minutes, args.from_position, args.poll_seconds)
    try:
        result = watch.follow(args.repo, args.run_id, options, GitVersionControl(), _emit)
    except (RunsError, JournalError) as error:
        print(f"delegate watch: {error}", file=sys.stderr)
        return watch.EXIT_ERROR
    print(f"position {result.position}")
    return result.code
