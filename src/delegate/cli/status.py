"""The `delegate status` command: parse the arguments, build the git backend, call the report of the run context and print it.

The use case lives in `delegate.run.status`, which never imports an adapter. This module is the composition point
of the command: it makes the git backend of the version-control port and hands it to the use case. It also looks up
the harness adapter of the run, and hands its stream reader to the use case. An adapter that this process does not
know, or that has no stream reader, gives no reader: the output then has the progress and no tokens.

With no run id, the active runs decide (`delegate.run.runs.runs_of`), as for `delegate watch`: the one active run, or
the newest run when no run is active. With more than one active run, the lines list each active run
(`delegate.run.status.active_line`), and `--json` is an error, because one snapshot cannot hold two runs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .. import adapters
from ..adapters.git import GitVersionControl
from ..ports.harness import Activity
from ..run import status
from ..run.journal import JournalError
from ..run.runs import RunsError, runs_of

# The exit status of an input that cannot be read: no run, an unknown run id, or a journal that is not valid. With
# `--json` and no run id, it is also the exit status when more than one run is active.
# An event stream that cannot be read is not an error: the output shows no tokens and no activity for its agent.
EXIT_ERROR: int = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delegate status", description="Print the state and the progress of each ticket of a run, from its journal and its event streams.")
    parser.add_argument(
        "run_id", nargs="?",
        help="the run to show; default is the one active run, or the newest run of the repository when no run is active",
    )
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository that holds the run; default is the current directory")
    parser.add_argument(
        "--json", action="store_true",
        help="print one snapshot of the run as JSON: the progress, the ETA, the tokens, and the activity of each agent",
    )
    return parser


def stream_reader_of(adapter: str) -> Callable[[Path], Activity] | None:
    """The stream reader of the harness adapter that the run names, or None when no adapter has the name or it has no reader.

    `delegate watch` uses the same reader for its live view.
    """
    try:
        found = adapters.get(adapter)
    except KeyError:
        return None
    return getattr(found, "activity", None)


def utc_now() -> datetime:
    """The time of the clock, in UTC."""
    return datetime.fromtimestamp(time.time(), timezone.utc)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run_id = args.run_id
        if run_id is None:
            runs = runs_of(args.repo, GitVersionControl())
            if runs.chosen is None:
                ids = ", ".join(active.parent.name for active in runs.active)
                if args.json:
                    raise RunsError(f"{len(runs.active)} runs are active: {ids}; give a run id to show one of them")
                now = utc_now()
                listed = [status.active_line(active, now) for active in runs.active]  # an error prints no part of the list
                print("\n".join([f"{len(runs.active)} runs are active; give a run id to show one of them:", *listed]))
                return 0
            run_id = runs.chosen.parent.name
        if args.json:
            snap = status.snapshot(args.repo, run_id, GitVersionControl(), stream_reader_of, utc_now())
            print(json.dumps(snap.to_json(), indent=2))
            return 0
        lines = status.report(args.repo, run_id, GitVersionControl(), stream_reader_of, utc_now())
    except (RunsError, JournalError) as error:
        print(f"delegate status: {error}", file=sys.stderr)
        return EXIT_ERROR
    print("\n".join(lines))
    return 0
