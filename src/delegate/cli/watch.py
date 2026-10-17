"""The `delegate watch` command: parse the arguments, build the git backend, follow the journal through the run context and show each event.

The use case lives in `delegate.run.watch`, which never imports an adapter. This module is the composition point
of the command: it makes the git backend of the version-control port, finds the journal, and hands the use case a sink.
When standard output is a terminal, the sink is the live view of `delegate.run.live`, with the stream reader of the
adapter of the run, as for `delegate status`, and this module draws its frames in place. Otherwise, and with `--raw`,
the sink prints the raw line of each event.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import TextIO

from ..adapters.git import GitVersionControl
from ..run import live, watch
from ..run.journal import JournalError
from ..run.runs import RunsError, journal_of
from .status import stream_reader_of, utc_now

# The terminal control sequences of the live view: clear the screen, move the cursor to the top left, clear to the end
# of the line, and clear the screen below the cursor.
CLEAR_SCREEN: str = "\x1b[2J"
HOME: str = "\x1b[H"
CLEAR_LINE_END: str = "\x1b[K"
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
        help="exit when the journal, the captured event streams and the gate logs have not changed for N minutes; N is a number greater than 0",
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
    parser.add_argument(
        "--raw", action="store_true",
        help="print the raw line of each event, as to a standard output that is not a terminal; default on a terminal is the live view",
    )
    return parser


def _emit(line: str) -> None:
    print(line, flush=True)


class _Screen:
    """The terminal that the live view draws on. The first frame clears the screen; each frame starts at the top left."""

    def __init__(self, out: TextIO) -> None:
        self._out = out
        self._drawn = False

    def size(self) -> tuple[int, int]:
        size = shutil.get_terminal_size()
        return size.columns, size.lines

    def draw(self, lines: list[str]) -> None:
        start = HOME if self._drawn else CLEAR_SCREEN + HOME
        self._drawn = True
        # The screen below the frame is cleared before the last newline, so a line printed after the frame starts clean.
        self._out.write(start + "\n".join(f"{line}{CLEAR_LINE_END}" for line in lines) + CLEAR_DOWN + "\n")
        self._out.flush()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = watch.WatchOptions(args.until, args.stall_minutes, args.max_minutes, args.from_position, args.poll_seconds)
    try:
        journal = journal_of(args.repo, args.run_id, GitVersionControl())
        sink: watch.Sink = watch.RawSink(_emit)
        if not args.raw and sys.stdout.isatty():
            screen = _Screen(sys.stdout)
            sink = live.LiveView(journal, stream_reader_of, utc_now, screen.size, screen.draw)
        result = watch.follow(journal, options, sink)
    except (RunsError, JournalError) as error:
        print(f"delegate watch: {error}", file=sys.stderr)
        return watch.EXIT_ERROR
    print(f"position {result.position}")
    return result.code
