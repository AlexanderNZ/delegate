"""The `delegate status` command: parse the arguments, build the git backend, call the report of the run context and print it.

The use case lives in `delegate.run.status`, which never imports an adapter. This module is the composition point
of the command: it makes the git backend of the version-control port and hands it to the use case. For `--json` and
`--follow` it also looks up the harness adapter of the run, and hands its stream reader to the use case.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .. import adapters
from ..adapters.git import GitVersionControl
from ..ports.harness import Activity, AdapterError
from ..run import status
from ..run.journal import JournalError
from ..run.runs import RunsError

# The exit status of an input that cannot be read: no run, an unknown run id, a journal that is not valid,
# an adapter that is not known, or an event stream that cannot be read.
EXIT_ERROR: int = 2

# The seconds between two snapshots of `--follow`, when `--interval` does not set them.
DEFAULT_INTERVAL: float = 2.0

# The terminal control sequences of `--follow`: move the cursor up the lines of the last snapshot, and clear to the end of the screen.
CURSOR_UP: str = "\x1b[{lines}A"
CLEAR_DOWN: str = "\x1b[J"


def _positive(text: str) -> float:
    """An option value that is a number greater than 0."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0, not {text}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delegate status", description="Print the state of each ticket of a run, from its journal.")
    parser.add_argument("run_id", nargs="?", help="the run to show; default is the newest run of the repository")
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository that holds the run; default is the current directory")
    view = parser.add_mutually_exclusive_group()
    view.add_argument(
        "--json", action="store_true",
        help="print one snapshot of the run as JSON: the progress, the ETA, the tokens, and the activity of each agent",
    )
    view.add_argument(
        "--follow", action="store_true",
        help="draw the snapshot in the terminal, draw it again after each interval, and exit when the run ends",
    )
    parser.add_argument(
        "--interval", type=_positive, default=DEFAULT_INTERVAL, metavar="SECONDS",
        help=f"the seconds between two snapshots of --follow; default {DEFAULT_INTERVAL:g}",
    )
    return parser


def _reader_of(adapter: str) -> Callable[[Path], Activity]:
    """The stream reader of the harness adapter that the run names. Raise AdapterError when no adapter has the name."""
    try:
        return adapters.get(adapter).activity
    except KeyError:
        raise AdapterError(f"adapter {adapter!r} of the run is not known, so its event streams cannot be read") from None


def _now() -> datetime:
    return datetime.fromtimestamp(time.time(), timezone.utc)


def _follow(repo: Path, run_id: str | None, interval: float) -> int:
    """Draw a snapshot, then draw it again after each interval until the run ends.

    On a terminal each snapshot replaces the last one in place. Otherwise each snapshot is printed as plain lines.
    """
    terminal = sys.stdout.isatty()
    drawn = 0
    while True:
        snap = status.snapshot(repo, run_id, GitVersionControl(), _reader_of, _now())
        run_id = snap.run_id  # follow this run, also when a newer run starts
        lines = status.view_lines(snap, shutil.get_terminal_size().columns)
        if terminal and drawn:
            sys.stdout.write(CURSOR_UP.format(lines=drawn) + CLEAR_DOWN)
        sys.stdout.write("".join(f"{line}\n" for line in lines))
        sys.stdout.flush()
        drawn = len(lines)
        if snap.state != status.RUNNING:
            return 0
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.follow:
            return _follow(args.repo, args.run_id, args.interval)
        if args.json:
            snap = status.snapshot(args.repo, args.run_id, GitVersionControl(), _reader_of, _now())
            print(json.dumps(snap.to_json(), indent=2))
            return 0
        lines = status.report(args.repo, args.run_id, GitVersionControl())
    except (RunsError, JournalError, AdapterError) as error:
        print(f"delegate status: {error}", file=sys.stderr)
        return EXIT_ERROR
    print("\n".join(lines))
    return 0
