"""The `delegate runs` command: parse the arguments, find the state root of this machine, and print the table of its runs (#307).

The use case lives in `delegate.run.fleet`, which never imports an adapter. This module is the composition point of the
command: it gives the use case the name of the machine, the time of the clock, and the stream reader of the harness
adapter that each run names (`delegate.cli.status.stream_reader_of`, as `delegate status` and `delegate watch` use it).
"""

from __future__ import annotations

import argparse
import json
import sys

from ..run import fleet
from ..run.host import host_name
from ..run.runs import RunsError, state_root
from .epilog import RUNS_PAGE, HelpFormatter, epilog
from .status import stream_reader_of, utc_now

# The exit status of a state root that cannot be used, or of a lock file that cannot be read.
EXIT_ERROR: int = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delegate runs",
        description="List the runs of this machine in one table, across each repository: the runs that have not ended, and the runs that ended in the last 6 hours.",
        epilog=epilog(RUNS_PAGE),
        formatter_class=HelpFormatter,
    )
    parser.add_argument(
        "--json", action="store_true",
        help="print one object as JSON: the format, the host, the time, the window, and one object for each listed run",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    print("\n".join(fleet.fleet_lines(found)))
    return 0
