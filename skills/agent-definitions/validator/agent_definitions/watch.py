"""`delegate watch`: follow the journal of a run, print each new event, and exit with a code for the reason.

It reads the journal of the run and nothing else. It needs no adapter, no
workflow file, and no tool beyond git, which finds the state directory.

Each reason to exit has its own exit code (see `EXIT_CODES`), so a coordinator
in any harness can run `watch` as a background task and act on the code.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .journal import JournalError, read_new_events
from .runs import RunsError, journal_of

# One exit code for each reason that `watch` exits. Code 2 is also the exit code of a usage error.
EXIT_SUCCEEDED: int = 0
EXIT_FAILED: int = 1
EXIT_ERROR: int = 2

# Event fields that the line of an event leaves out: the long ones.
LONG_FIELDS: frozenset[str] = frozenset({"output_tail"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delegate watch", description="Follow the journal of a run, print each new event, and exit with the run's result.")
    parser.add_argument("run_id", nargs="?", help="the run to follow; default is the newest run of the repository")
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository that holds the run; default is the current directory")
    parser.add_argument("--poll-seconds", type=float, default=1.0, help="how often to read the journal; default 1")
    return parser


def format_event(event: dict[str, object]) -> str:
    """One line for an event: the position, the time, the name, then each scalar field as `name=value`."""
    fields = [
        f"{key}={value}" for key, value in event.items()
        if key not in ("seq", "time", "event") and key not in LONG_FIELDS and not isinstance(value, (list, dict))
    ]
    return " ".join([str(event["seq"]), str(event["time"]), str(event["event"]), *fields])


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.poll_seconds <= 0:
        print(f"delegate watch: --poll-seconds must be greater than 0, not {args.poll_seconds}", file=sys.stderr)
        return EXIT_ERROR
    try:
        journal = journal_of(args.repo, args.run_id)
        offset, next_seq = 0, 1
        while True:
            events, offset = read_new_events(journal, offset, next_seq)
            next_seq += len(events)
            for event in events:
                print(format_event(event), flush=True)
                if event["event"] == "run-end":
                    return EXIT_SUCCEEDED if event["result"] == "built" else EXIT_FAILED
            time.sleep(args.poll_seconds)
    except (RunsError, JournalError) as error:
        print(f"delegate watch: {error}", file=sys.stderr)
        return EXIT_ERROR
