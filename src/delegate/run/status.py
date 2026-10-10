"""`delegate status`: print the state of each ticket of a run.

It reads the journal of the run and nothing else. It needs no adapter, no
workflow file, and no tool beyond git, which finds the state directory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..ports.vcs import VersionControl
from .journal import JournalError, read_events
from .runs import RunsError, journal_of

# The states that a ticket shows. A ticket with no event is pending, a ticket
# with `step-start` and no `step-end` is running, and the rest follow `step-end`
# and `skip`.
PENDING: str = "pending"
RUNNING: str = "running"
BUILT: str = "built"
FAILED: str = "failed"
SKIPPED: str = "skipped"

# What the line of a ticket shows for a value that the journal does not hold.
NONE: str = "-"

# The exit status of an input that cannot be read: no run, an unknown run id, a journal that is not valid.
EXIT_ERROR: int = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delegate status", description="Print the state of each ticket of a run, from its journal.")
    parser.add_argument("run_id", nargs="?", help="the run to show; default is the newest run of the repository")
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository that holds the run; default is the current directory")
    return parser


def render(events: list[dict[str, object]], journal: Path) -> list[str]:
    """The lines of the report for a run, from its journal events."""
    start = events[0]
    if start["event"] != "run-start":
        raise JournalError(f"journal {journal}: the first event is {start['event']!r}, not 'run-start'")
    end = next((e for e in reversed(events) if e["event"] == "run-end"), None)
    lines = [f"run {start['run_id']}: {end['result'] if end else RUNNING} (mode {start['mode']}, adapter {start['adapter']})", f"journal {journal}"]
    tickets = [str(t) for t in start["tickets"]]  # type: ignore[union-attr]
    for ticket in tickets:
        own = [e for e in events if e.get("ticket") == ticket]
        state, reason = PENDING, None
        for event in own:
            kind = event["event"]
            if kind == "step-start":
                state = RUNNING
            elif kind == "step-end":
                state, reason = str(event["state"]), event["reason"]
            elif kind == "skip":
                state, reason = SKIPPED, event["reason"]
        # The verdict of a chain (`economy` mode) covers each ticket in its `tickets` field, not only the ticket it names.
        verdicts = [
            str(e["verdict"]) for e in events
            if e["event"] == "verdict" and (e.get("ticket") == ticket or ticket in e.get("tickets", []))  # type: ignore[operator]
        ]
        branch = next((str(e["branch"]) for e in own if e["event"] == "step-start"), NONE)
        last = str(own[-1]["time"]) if own else NONE
        lines.append(f"ticket {ticket}: state {state}, verdict {verdicts[-1] if verdicts else NONE}, branch {branch}, last event {last}")
        if reason:
            lines.append(f"  reason: {reason}")
    return lines


def main(argv: list[str] | None, vcs: VersionControl) -> int:
    """Run the command. The caller gives the version-control backend: this module imports no adapter."""
    args = build_parser().parse_args(argv)
    try:
        journal = journal_of(args.repo, args.run_id, vcs)
        events = read_events(journal)
        if not events:
            raise JournalError(f"journal {journal} holds no event")
        lines = render(events, journal)
    except (RunsError, JournalError) as error:
        print(f"delegate status: {error}", file=sys.stderr)
        return EXIT_ERROR
    print("\n".join(lines))
    return 0
