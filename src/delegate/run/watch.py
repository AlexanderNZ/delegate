"""Follow the journal of a run, report each new event, and end with a code for the reason. The `delegate watch` command is in `delegate.cli.watch`.

It reads the journal of the run and nothing else. It needs no adapter, no
workflow file, and no tool beyond git, which finds the state directory.

Each reason to exit has its own exit code (see `EXIT_CODES`), so a coordinator
in any harness can run `watch` as a background task and act on the code.

`follow` gives each event, and the reason to end, to a sink (`Sink`). The raw
sink (`RawSink`) prints the raw line of each event: the form for a script and
for a standard output that is not a terminal. The live view of a terminal is
the sink in `delegate.run.live`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .journal import JournalError, read_new_events

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


@dataclass(frozen=True)
class WatchOptions:
    """How to follow a run. `from_position` is the `seq` of the last event that an earlier watch reported."""

    until: str | None = None
    stall_minutes: float | None = None
    max_minutes: float | None = None
    from_position: int = 0
    poll_seconds: float = 1.0


@dataclass(frozen=True)
class WatchResult:
    """Why the watch ended (`code`, one of `EXIT_CODES`) and the `seq` of the last event that it reported."""

    code: int
    position: int


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
    gates of a step are not green only when the step ends with a red gate. It
    also keeps each ticket whose agent the engine ended at the idle timeout, so
    the result event that follows is no problem.
    """

    def __init__(self) -> None:
        self._gates: dict[str, dict[str, bool]] = {}
        self._idle: set[str] = set()

    def problem(self, event: dict[str, object]) -> str | None:
        """The reason that the event is a problem, or `None`. Call it for every event, in order."""
        kind, ticket = str(event["event"]), str(event.get("ticket"))
        if kind == "gate-result":
            self._gates.setdefault(ticket, {})[str(event["command"])] = bool(event["green"])
        elif kind == "idle-timeout":
            self._idle.add(ticket)
        elif kind in AGENT_RESULT_ROLES and ticket in self._idle:
            # The engine ended the session after a valid report, so its end state is no problem.
            self._idle.discard(ticket)
        elif kind in AGENT_RESULT_ROLES:
            state, status = event["end_state"], event["exit_status"]
            # A specialist that ends capped continues; any other agent that ends capped fails its step. A finished agent
            # with a bad exit status continues when its report is valid, so the report event or the step end decides.
            failed = state == "failed" or (state == "capped" and kind != "adapter-result")
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


# The fields of an event that name a file whose change is activity: the event stream of an agent, and the log of a gate.
ACTIVITY_FIELDS: tuple[str, ...] = ("event_stream", "gate_log")


def _last_change(journal: Path, files: set[Path]) -> float:
    """The newest modification time of the journal and of the event streams and gate logs that exist."""
    times = [journal.stat().st_mtime]
    for file in files:
        try:
            times.append(file.stat().st_mtime)
        except FileNotFoundError:
            continue  # a file that the journal names and the disk lacks cannot show a change
    return max(times)


class Sink(Protocol):
    """What `follow` tells as it follows a journal: each event, the reason to end, and each poll."""

    def event(self, event: dict[str, object], reported: bool, problem: str | None) -> None:
        """An event of the journal, in order. `reported` is false for an event at or before `--from`, which the sink
        only reads. `problem` is the reason that a reported event is a problem, or None. A problem ends the watch."""

    def verdict(self, event: dict[str, object]) -> None:
        """`--until verdict` ends the watch at this verdict event, which `event` gave first."""

    def stall(self, minutes: float, last_event: dict[str, object] | None) -> None:
        """`--stall-minutes` ends the watch: nothing changed for `minutes`. `last_event` is the newest event, or None."""

    def time_limit(self, minutes: float) -> None:
        """`--max-minutes` ends the watch: `minutes` are used, and the run is not at its end."""

    def poll(self) -> None:
        """The events of one read of the journal are told, and the watch goes on."""

    def end(self) -> None:
        """The watch ends. It comes after the reason to end, or after the `run-end` event."""


class RawSink:
    """The raw lines of `watch`: one line for each reported event (`format_event`), and a line `watch: <reason>: ...`
    for each reason to end other than the end of the run."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit

    def event(self, event: dict[str, object], reported: bool, problem: str | None) -> None:
        if not reported:
            return
        self._emit(format_event(event))
        if problem is not None:
            self._emit(f"watch: problem: ticket {event.get('ticket')}: {problem}")

    def verdict(self, event: dict[str, object]) -> None:
        findings = "; ".join(str(f) for f in event["findings"])  # type: ignore[union-attr]
        # The verdict of a chain names a stack and its tickets, not one ticket.
        subject = (
            f"stack {event['stack']} (tickets {', '.join(str(t) for t in event['tickets'])})"  # type: ignore[attr-defined]
            if "tickets" in event else f"ticket {event['ticket']}"
        )
        self._emit(f"watch: verdict: {subject} round {event['round']}: {event['verdict']}" + (f": {findings}" if findings else ""))

    def stall(self, minutes: float, last_event: dict[str, object] | None) -> None:
        self._emit(
            f"watch: stall: the journal, the event streams and the gate logs have not changed for {minutes:g} minutes; "
            f"last event: {format_event(last_event) if last_event else 'none'}"
        )

    def time_limit(self, minutes: float) -> None:
        self._emit(f"watch: time limit: {minutes:g} minutes are used; the run is not at its end")

    def poll(self) -> None:
        return None

    def end(self) -> None:
        return None


def follow(journal: Path, options: WatchOptions, sink: Sink) -> WatchResult:
    """Follow a journal: tell each event, and the reason to end, to `sink` as it happens.

    `delegate.run.runs.journal_of` finds the journal of a run. Raise JournalError when the journal is not valid or
    `from_position` is beyond its end. Return the exit code and the `seq` of the last event that was reported.
    """
    result = _follow(options, journal, sink)
    sink.end()
    return result


def _follow(options: WatchOptions, journal: Path, sink: Sink) -> WatchResult:
    """Follow the journal. Return the exit code and the `seq` of the last event that was reported."""
    started = time.monotonic()
    offset, next_seq, position = 0, 1, options.from_position
    tracker = _Tracker()
    files: set[Path] = set()  # the event streams and the gate logs that the journal names
    last_event: dict[str, object] | None = None
    first_read = True
    while True:
        events, offset = read_new_events(journal, offset, next_seq)
        next_seq += len(events)
        if first_read and next_seq - 1 < options.from_position:
            raise JournalError(f"journal {journal} holds {next_seq - 1} events, so --from {options.from_position} is beyond its end")
        first_read = False
        for event in events:
            last_event = event
            seq = int(event["seq"])  # type: ignore[call-overload]
            files.update(Path(str(event[field])) for field in ACTIVITY_FIELDS if field in event)
            problem = tracker.problem(event)  # an event before the position still counts for the events after it
            if seq <= options.from_position:
                sink.event(event, False, None)
                if event["event"] == "run-end":
                    return WatchResult(EXIT_SUCCEEDED if event["result"] == "built" else EXIT_FAILED, position)
                continue
            position = seq
            sink.event(event, True, problem)
            if problem is not None:
                return WatchResult(EXIT_PROBLEM, position)
            if options.until == UNTIL_VERDICT and event["event"] == "verdict":
                sink.verdict(event)
                return WatchResult(EXIT_VERDICT, position)
            if event["event"] == "run-end":
                return WatchResult(EXIT_SUCCEEDED if event["result"] == "built" else EXIT_FAILED, position)
        if options.stall_minutes is not None and time.time() - _last_change(journal, files) >= options.stall_minutes * 60:
            sink.stall(options.stall_minutes, last_event)
            return WatchResult(EXIT_STALL, position)
        if options.max_minutes is not None and time.monotonic() - started >= options.max_minutes * 60:
            sink.time_limit(options.max_minutes)
            return WatchResult(EXIT_TIME_LIMIT, position)
        sink.poll()
        time.sleep(options.poll_seconds)
