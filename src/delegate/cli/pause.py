"""The `delegate pause` command: parse the arguments, build the git backend, and ask the run to pause.

The use case lives in `delegate.run.pause`, which never imports an adapter. This module is the composition point of
the command: it makes the git backend of the version-control port and hands it to the use case.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..adapters.git import GitVersionControl
from ..run import pause
from .status import utc_now

# The exit status of a run that cannot be paused: an unknown run, a run that ended or is paused, or a run with no live process.
EXIT_REFUSED: int = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delegate pause",
        description="Ask a run to stop at its next step boundary: after the current step, or before the next gate or verifier.",
    )
    parser.add_argument("run_id", help="the run to pause; it must have a live process")
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository that holds the run; default is the current directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path = pause.request_pause(args.repo, args.run_id, GitVersionControl(), utc_now())
    except pause.PauseError as error:
        print(f"delegate pause: {error}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"run {args.run_id}: pause requested; the run stops at its next step boundary")
    print(f"request {path}")
    return 0
