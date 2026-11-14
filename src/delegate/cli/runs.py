"""The `delegate runs` command: parse the arguments, find the state root of this machine, and print the table of its runs (#307).

The use case lives in `delegate.run.fleet`, which never imports an adapter. This module is the composition point of the
command: it gives the use case the name of the machine, the time of the clock, and the stream reader of the harness
adapter that each run names (`delegate.cli.status.stream_reader_of`, as `delegate status` and `delegate watch` use it).

With `--watch` (#313), the command redraws the view in place on the terminal, as `delegate watch` redraws its frame (`delegate.cli.screen`):
once for each `POLL_SECONDS`, no alternate screen, no spinner, and the size of the terminal read again at each redraw. Each redraw reads each
journal only from its last position (`fleet.JournalReads`). The command ends with Ctrl-C, SIGTERM or SIGHUP, as the live view of `delegate watch`
does: the cursor shows again, and `delegate` prints `delegate: interrupted` and exits with 130. A journal that cannot be read goes on stderr
once, before the first frame when the first read finds it, and else after the last frame, so no line of stderr breaks a frame.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys

from ..run import clock, fleet
from ..run.host import host_name
from ..run.runs import RunsError, state_root
from .epilog import RUNS_PAGE, HelpFormatter, epilog
from .screen import Screen
from .status import stream_reader_of, utc_now
from .stop_signals import stop_signals_as_ctrl_c

# The exit status of a state root that cannot be used, or of a lock file that cannot be read.
EXIT_ERROR: int = 2

# The seconds between two redraws of `--watch`: the poll rate of `delegate watch`.
POLL_SECONDS: float = 1.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delegate runs",
        description="List the runs of this machine in one table, across each repository: the runs that have not ended, and the runs that ended in the last 6 hours.",
        epilog=epilog(RUNS_PAGE),
        formatter_class=HelpFormatter,
    )
    form = parser.add_mutually_exclusive_group()
    form.add_argument(
        "--json", action="store_true",
        help="print one object as JSON: the format, the host, the time, the window, and one object for each listed run; cannot be used with --watch",
    )
    form.add_argument(
        "--watch", action="store_true",
        help="redraw the table in place once a second, until Ctrl-C; it needs a terminal, and it cannot be used with --json",
    )
    parser.add_argument(
        "--timeline", action="store_true",
        help="draw one lane for each run over the last 6 hours, to the latest ETA, in place of the table; it cannot be used with --json",
    )
    return parser


def _watch(timeline: bool) -> int:
    """Redraw the table in place until Ctrl-C. Return 2 with no terminal, or when the state root cannot be used."""
    if not sys.stdout.isatty():
        print("delegate runs: --watch needs a terminal", file=sys.stderr)
        return EXIT_ERROR
    the_clock = clock.current()
    screen, reads = Screen(sys.stdout), fleet.JournalReads()
    reported: set[str] = set()  # the lines of the journals that cannot be read, so each goes on stderr once
    late: list[str] = []
    first = True
    try:
        try:
            with stop_signals_as_ctrl_c():
                while True:
                    found = fleet.fleet_of(state_root(), host_name(), utc_now(), stream_reader_of, reads)
                    new = [line for line in found.unreadable_lines("delegate runs") if line not in reported]
                    reported.update(new)
                    if first:
                        for line in new:
                            print(line, file=sys.stderr)  # before the first frame, as without --watch
                    else:
                        late += new
                    width, height = screen.size()
                    screen.draw(fleet.fleet_lines(found, width, height or None, timeline))
                    first = False
                    the_clock.sleep(POLL_SECONDS)
        finally:
            screen.restore()  # on each end, before the last lines
            for line in late:
                print(line, file=sys.stderr)
    except RunsError as error:
        print(f"delegate runs: {error}", file=sys.stderr)
        return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeline and args.json:
        parser.error("--timeline cannot be used with --json")
    if args.watch:
        return _watch(args.timeline)
    try:
        found = fleet.fleet_of(state_root(), host_name(), utc_now(), stream_reader_of)
    except RunsError as error:
        print(f"delegate runs: {error}", file=sys.stderr)
        return EXIT_ERROR
    for line in found.unreadable_lines("delegate runs"):
        print(line, file=sys.stderr)
    if args.json:
        print(json.dumps(found.to_json(), indent=2))
        return 0
    size = shutil.get_terminal_size(fallback=(80, 0))  # a terminal that gives no height: the lines are 0, and no row folds
    print("\n".join(fleet.fleet_lines(found, size.columns, size.lines or None, args.timeline)))
    return 0
