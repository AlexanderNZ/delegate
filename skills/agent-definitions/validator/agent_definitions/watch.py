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
EXIT_PROBLEM: int = 3
EXIT_VERDICT: int = 4
EXIT_STALL: int = 5
EXIT_TIME_LIMIT: int = 6

# The condition of each exit code, for the reference page of `watch`, which generates its exit-code table from this
# table and from `EXIT_CODES`. The links point into that page.
EXIT_CODE_CONDITIONS: dict[int, str] = {
    EXIT_SUCCEEDED: "The journal holds `run-end` with the result `built`.",
    EXIT_FAILED: "The journal holds `run-end` with the result `failed`.",
    EXIT_ERROR: (
        "A usage error, a repository with no run, an unknown run id, a journal that is not valid, "
        "or a `--from` position beyond the end of the journal."
    ),
    EXIT_PROBLEM: "An event is a problem. See [the problem events](#the-problem-events).",
    EXIT_VERDICT: "`--until verdict` is set and the journal holds a `verdict` event.",
    EXIT_STALL: "`--stall-minutes` is set and nothing changed for that time.",
    EXIT_TIME_LIMIT: "`--max-minutes` is set and the time is used.",
}

# The meaning of each exit code. The reference page of `watch` lists the same table.
EXIT_CODES: dict[int, str] = {
    EXIT_SUCCEEDED: "run succeeded",
    EXIT_FAILED: "run failed",
    EXIT_ERROR: "error",
    EXIT_PROBLEM: "problem",
    EXIT_VERDICT: "verdict",
    EXIT_STALL: "stall",
    EXIT_TIME_LIMIT: "time limit",
}

# The role that each adapter-result event and each report event belongs to.
AGENT_RESULT_ROLES: dict[str, str] = {"adapter-result": "specialist", "fixup-result": "fix-up specialist", "verify-result": "verifier"}
AGENT_REPORT_ROLES: dict[str, str] = {"report-validation": "specialist", "fixup-report": "fix-up specialist", "verify-report": "verifier"}

# The value of `--until` that makes `watch` exit at a verifier verdict.
UNTIL_VERDICT: str = "verdict"

# Event fields that the line of an event leaves out: the long ones.
LONG_FIELDS: frozenset[str] = frozenset({"output_tail"})


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
        "--until", choices=[UNTIL_VERDICT],
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


def format_event(event: dict[str, object]) -> str:
    """One line for an event: the position, the time, the name, then each scalar field as `name=value`."""
    fields = [
        f"{key}={value}" for key, value in event.items()
        if key not in ("seq", "time", "event") and key not in LONG_FIELDS and not isinstance(value, (list, dict))
    ]
    return " ".join([str(event["seq"]), str(event["time"]), str(event["event"]), *fields])


class _Tracker:
    """Reads the events of one journal in order and finds the problem events.

    It keeps the last result of each gate command of each ticket, because the
    gates of a step are not green only when the step ends with a red gate.
    """

    def __init__(self) -> None:
        self._gates: dict[str, dict[str, bool]] = {}

    def problem(self, event: dict[str, object]) -> str | None:
        """The reason that the event is a problem, or `None`. Call it for every event, in order."""
        kind, ticket = str(event["event"]), str(event.get("ticket"))
        if kind == "gate-result":
            self._gates.setdefault(ticket, {})[str(event["command"])] = bool(event["green"])
        elif kind in AGENT_RESULT_ROLES:
            state, status = event["end_state"], event["exit_status"]
            # A specialist that ends capped continues; any other agent that ends capped fails its step.
            failed = state == "failed" or (state == "finished" and status != 0) or (state == "capped" and kind != "adapter-result")
            if failed:
                return f"the {AGENT_RESULT_ROLES[kind]} ended {state} with exit status {status}"
        elif kind in AGENT_REPORT_ROLES:
            role = AGENT_REPORT_ROLES[kind]
            if not event["valid"]:
                return f"the {role} returned no valid result: {event['reason']}"
            status, detail = event.get("status"), event.get("blocked_reason")
            if status is not None and status != "committed":
                subject = "the step" if kind == "report-validation" else f"the {role}"
                return f"{subject} ended {status}" + (f": {detail}" if detail else "")
        elif kind == "continuation" and int(event["count"]) >= 2:  # type: ignore[call-overload]
            return f"the step continued {event['count']} times (limit {event['limit']}, trigger {event['trigger']})"
        elif kind == "step-end" and event["state"] == "failed":
            red = [command for command, green in self._gates.get(ticket, {}).items() if not green]
            return f"the gates are not green: {'; '.join(red)}" if red else f"the step failed: {event['reason']}"
        return None


def _last_change(journal: Path, streams: set[Path]) -> float:
    """The newest modification time of the journal and of the event streams that exist."""
    times = [journal.stat().st_mtime]
    for stream in streams:
        try:
            times.append(stream.stat().st_mtime)
        except FileNotFoundError:
            continue  # a stream that the journal names and the disk lacks cannot show a change
    return max(times)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        journal = journal_of(args.repo, args.run_id)
        code, position = _follow(args, journal)
    except (RunsError, JournalError) as error:
        print(f"delegate watch: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(f"position {position}")
    return code


def _follow(args: argparse.Namespace, journal: Path) -> tuple[int, int]:
    """Follow the journal. Return the exit code and the `seq` of the last event that was reported."""
    started = time.monotonic()
    offset, next_seq, position = 0, 1, args.from_position
    tracker = _Tracker()
    streams: set[Path] = set()
    last_event: dict[str, object] | None = None
    first_read = True
    while True:
        events, offset = read_new_events(journal, offset, next_seq)
        next_seq += len(events)
        if first_read and next_seq - 1 < args.from_position:
            raise JournalError(f"journal {journal} holds {next_seq - 1} events, so --from {args.from_position} is beyond its end")
        first_read = False
        for event in events:
            last_event = event
            seq = int(event["seq"])  # type: ignore[call-overload]
            if "event_stream" in event:
                streams.add(Path(str(event["event_stream"])))
            problem = tracker.problem(event)  # an event before the position still counts for the events after it
            if seq <= args.from_position:
                if event["event"] == "run-end":
                    return (EXIT_SUCCEEDED if event["result"] == "built" else EXIT_FAILED), position
                continue
            position = seq
            print(format_event(event), flush=True)
            if problem is not None:
                print(f"watch: problem: ticket {event.get('ticket')}: {problem}")
                return EXIT_PROBLEM, position
            if args.until == UNTIL_VERDICT and event["event"] == "verdict":
                findings = "; ".join(str(f) for f in event["findings"])  # type: ignore[union-attr]
                # The verdict of a chain names a stack and its tickets, not one ticket.
                subject = (
                    f"stack {event['stack']} (tickets {', '.join(str(t) for t in event['tickets'])})"  # type: ignore[attr-defined]
                    if "tickets" in event else f"ticket {event['ticket']}"
                )
                print(f"watch: verdict: {subject} round {event['round']}: {event['verdict']}" + (f": {findings}" if findings else ""))
                return EXIT_VERDICT, position
            if event["event"] == "run-end":
                return (EXIT_SUCCEEDED if event["result"] == "built" else EXIT_FAILED), position
        if args.stall_minutes is not None and time.time() - _last_change(journal, streams) >= args.stall_minutes * 60:
            print(
                f"watch: stall: the journal and the event streams have not changed for {args.stall_minutes:g} minutes; "
                f"last event: {format_event(last_event) if last_event else 'none'}"
            )
            return EXIT_STALL, position
        if args.max_minutes is not None and time.monotonic() - started >= args.max_minutes * 60:
            print(f"watch: time limit: {args.max_minutes:g} minutes are used; the run is not at its end")
            return EXIT_TIME_LIMIT, position
        time.sleep(args.poll_seconds)
