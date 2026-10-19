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
from datetime import datetime
from pathlib import Path
from typing import Protocol

from . import output
from .journal import ACTIVITY_FIELDS, JournalError, is_paused, last_change, read_new_events

# A part of a line and its paint role (`output.styled`). The empty role is the colour of the theme.
Part = tuple[str, str]

# The paint role of the result of a run that ended, in its one line (tokens.md): the result word is bold.
RESULT_ROLES: dict[str, str] = {"built": "ok bold", "failed": "bad bold"}


def _plain(parts: list[Part]) -> str:
    return "".join(text for _, text in parts)


def _painted(parts: list[Part]) -> str:
    return "".join(output.styled(role, text) for role, text in parts)


# One exit code for each reason that `watch` exits. Code 2 is also the exit code of a usage error.
EXIT_SUCCEEDED: int = 0
EXIT_FAILED: int = 1
EXIT_ERROR: int = 2
EXIT_PROBLEM: int = 3
EXIT_VERDICT: int = 4
EXIT_STALL: int = 5
EXIT_TIME_LIMIT: int = 6
EXIT_PAUSED: int = 7
# 128 + 2, the number of SIGINT: the exit status that a shell gives to a process that Ctrl-C stopped.
EXIT_INTERRUPTED: int = 130

# The condition of each exit code, for the reference page of `watch`, which generates its exit-code table from this
# table and from `EXIT_CODES`. The links point into that page.
EXIT_CODE_CONDITIONS: dict[int, str] = {
    EXIT_SUCCEEDED: "The journal holds `run-end` with the result `built`.",
    EXIT_FAILED: (
        "The journal holds `run-end` with the result `failed`, or, with no run id, "
        "[the newest run failed at start](#the-newest-run-that-failed-at-start) when no run is active."
    ),
    EXIT_ERROR: (
        "A usage error, a repository with no run, an unknown run id, a journal that is not valid, "
        "a `--from` position beyond the end of the journal, or, with no run id, "
        "[a newest run that stopped](#the-newest-run-that-stopped) when no run is active, "
        "or [more than one active run](#the-run-picker) when standard input or standard output is not a terminal, "
        "or no answer at the prompt."
    ),
    EXIT_PROBLEM: "An event is a problem. See [the problem events](#the-problem-events).",
    EXIT_VERDICT: "`--until verdict` is set and the journal holds a `verdict` event.",
    EXIT_STALL: "`--stall-minutes` is set and nothing changed for that time.",
    EXIT_TIME_LIMIT: "`--max-minutes` is set and the time is used.",
    EXIT_PAUSED: "The journal holds `paused`: a pause request stopped the run at a step boundary.",
    EXIT_INTERRUPTED: "Ctrl-C stopped the watch. The run goes on. See [Ctrl-C](#ctrl-c).",
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
    EXIT_PAUSED: "run paused",
    EXIT_INTERRUPTED: "interrupted",
}

# The role that each adapter-result event and each report event belongs to.
AGENT_RESULT_ROLES: dict[str, str] = {"adapter-result": "specialist", "fixup-result": "fix-up specialist", "verify-result": "verifier"}
AGENT_REPORT_ROLES: dict[str, str] = {"report-validation": "specialist", "fixup-report": "fix-up specialist", "verify-report": "verifier"}

# What the problem line of a refused tool tells the reader to do (see the Claude Code adapter).
REFUSED_HINT: str = "give the agents a settings file with DELEGATE_AGENT_SETTINGS"

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


@dataclass(frozen=True)
class EndedRun:
    """A run whose journal holds `run-end`: its id, its result, the time of the `run-end`, and its first problem.

    `problem` is `ticket <id>: <reason>` for the first problem event of the journal, or None when it has none.
    """

    run_id: str
    result: str
    ended: datetime
    problem: str | None

    @property
    def code(self) -> int:
        """The exit code of the result of the run."""
        return _result_code(self.result)

    def line(self) -> str:
        """The one line of `watch` for a newest run that ended, with the time of its end in the local time zone."""
        return _plain(self._parts())

    def painted(self) -> str:
        """The line with the paint of tokens.md on a terminal: the result in its role and bold, the time faint, and the
        first problem in `bad`."""
        return _painted(self._parts())

    def _parts(self) -> list[Part]:
        parts = [
            ("", f"no run is active; the newest run {self.run_id} ended "), (RESULT_ROLES.get(self.result, ""), self.result),
            ("", " at "), ("dim", self.ended.astimezone().strftime("%Y-%m-%d %H:%M")),
        ]
        if self.result != "built" and self.problem is not None:
            parts += [("", "; "), ("bad", f"first problem: {self.problem}")]
        return parts


def ended_run(journal: Path) -> EndedRun | None:
    """The run of `journal` when the newest event of its journal is `run-end`, or None when the run goes on.

    A retry continues a run that ended: it appends `retry`, then a new `step-end` and a new `run-end`. So a run whose
    journal holds an event after its newest `run-end` goes on, and the result and the time come from the newest
    `run-end`. The first problem is the first problem that a later event does not supersede (see `_superseded`). The run
    id is the name of the directory of the journal. Raise JournalError when the journal is not valid.
    """
    events, _ = read_new_events(journal, 0, 1)
    if not events or events[-1]["event"] != "run-end":
        return None
    superseded = _superseded(events)
    tracker = _Tracker()
    problem: str | None = None
    for event in events:
        reason = tracker.problem(event)  # the tracker reads every event, because a gate result counts for a later step end
        if problem is None and reason is not None and event["seq"] not in superseded:
            problem = f"ticket {event.get('ticket')}: {reason}"
    end = events[-1]
    return EndedRun(journal.parent.name, str(end["result"]), datetime.fromisoformat(str(end["time"])), problem)


@dataclass(frozen=True)
class StoppedRun:
    """A run with no `run-end` and no live process: its id, the time of the newest event of its journal, and whether it
    is paused. A pause, a crash, a kill and Ctrl-C stop a run so. `delegate run --resume` goes on with it."""

    run_id: str
    stopped: datetime
    paused: bool

    @property
    def code(self) -> int:
        """`EXIT_PAUSED` for a paused run, and `EXIT_ERROR` for a run that stopped: there is no run to follow."""
        return EXIT_PAUSED if self.paused else EXIT_ERROR

    def line(self) -> str:
        """The one line of `watch` for a newest run that stopped, with the time of its newest event in the local time zone."""
        return _plain(self._parts())

    def painted(self) -> str:
        """The line with the paint of tokens.md on a terminal: `paused` in `warn`, and the time faint.

        The line of a run that stopped with no pause is an error line on stderr, so the caller paints all of it.
        """
        return _painted(self._parts())

    def _parts(self) -> list[Part]:
        at = self.stopped.astimezone().strftime("%Y-%m-%d %H:%M")
        resume = f"go on with delegate run --resume {self.run_id}"
        if self.paused:
            return [("", f"no run is active; the newest run {self.run_id} "), ("warn", "paused"), ("", " at "), ("dim", at), ("", f"; {resume}")]
        return [("", f"no run is active; the newest run {self.run_id} stopped at {at} with no run-end, and no process works on it; {resume}")]


def newest_run(journal: Path) -> EndedRun | StoppedRun:
    """The one line of the newest run when no run is active: the run ended (`ended_run`), or it stopped.

    The caller knows that the run is not active. A run whose newest event is not `run-end` then has no live process.
    It is paused when its newest `paused` comes after its newest `resume`. Raise JournalError when the journal is not valid.
    """
    ended = ended_run(journal)
    if ended is not None:
        return ended
    events, _ = read_new_events(journal, 0, 1)
    if not events:
        raise JournalError(f"journal {journal} holds no event")
    return StoppedRun(journal.parent.name, datetime.fromisoformat(str(events[-1]["time"])), is_paused(events))


def _superseded(events: list[dict[str, object]]) -> set[object]:
    """The `seq` of each event of `events` that a later event of `events` makes history.

    A `run-end` or a `paused` that another event follows is history: a retry or a resume continues the run. An event
    of a ticket that a later `retry` of the same ticket follows is history: the retry continues the step of that ticket,
    so a problem of that ticket before the retry is not a problem of the run. A history event is reported, but it does not end a watch.
    """
    superseded: set[object] = set()
    retried: set[str] = set()
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        kind, ticket = event["event"], event.get("ticket")
        if (kind in RUN_STOPS and index < len(events) - 1) or (ticket is not None and str(ticket) in retried):
            superseded.add(event["seq"])
        if kind == "retry":
            retried.add(str(ticket))
    return superseded


# The events after a problem in the replay that show that the run went past it (#146): the engine continued or retried
# the step of the ticket of the problem, or a step started or ended, or the run ended, after the problem.
PAST_STEP: tuple[str, ...] = ("continuation", "retry")
PAST_RUN: tuple[str, ...] = ("step-start", "step-end", "run-end")


def _gone_past(replay: list[dict[str, object]]) -> set[object]:
    """The `seq` of each event of the replay that the run went past: a problem there does not end the watch (#146).

    The replay is the events of the journal when the watch starts. An event is gone past when a later event of the
    replay is a `continuation` or a `retry` of its ticket, or a `step-start`, a `step-end` or a `run-end`. A replay whose
    last event is a `run-end` or a `paused` is a run at its end, not a run that goes on, so no event of it is gone past,
    and a run that failed stops at its first problem, as before.
    """
    if not replay or replay[-1]["event"] in RUN_STOPS:
        return set()
    past: set[object] = set()
    run_later = False
    step_later: set[str] = set()
    for event in reversed(replay):
        kind, ticket = event["event"], str(event.get("ticket"))
        if run_later or ticket in step_later:
            past.add(event["seq"])
        if kind in PAST_RUN:
            run_later = True
        elif kind in PAST_STEP:
            step_later.add(ticket)
    return past


def _result_code(result: object) -> int:
    """The exit code of the result of a `run-end` event."""
    return EXIT_SUCCEEDED if result == "built" else EXIT_FAILED


# The events that stop the run process, and so end a watch: the end of the run, and a pause at a step boundary.
RUN_STOPS: tuple[str, ...] = ("run-end", "paused")


def _stop_code(event: dict[str, object]) -> int:
    """The exit code of an event of `RUN_STOPS`."""
    return EXIT_PAUSED if event["event"] == "paused" else _result_code(event["result"])


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
        elif kind in AGENT_RESULT_ROLES and event.get("refused"):
            self._idle.discard(ticket)  # the idle timeout belongs to this result event, as in the branch below
            refused = event["refused"]  # a list of {"tool", "mode"}, from `_refused_fields` of the engine
            tools = ", ".join(dict.fromkeys(str(item["tool"]) for item in refused))  # type: ignore[index, union-attr]
            modes = ", ".join(dict.fromkeys(str(item["mode"]) for item in refused))  # type: ignore[index, union-attr]
            return f"the {AGENT_RESULT_ROLES[kind]} was refused {tools} in {modes} mode; {REFUSED_HINT}"
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
                reason = event.get("reason")
                return f"the {role} returned no valid result" + (f": {reason}" if reason else "")
            status, detail = event.get("status"), event.get("blocked_reason")
            if status is not None and status != "committed":
                subject = "the step" if kind == "report-validation" else f"the {role}"
                return f"{subject} ended {status}" + (f": {detail}" if detail else "")
        elif kind == "continuation" and int(event["count"]) >= 2:  # type: ignore[call-overload]
            return f"the step continued {event['count']} times (limit {event['limit']}, trigger {event['trigger']})"
        elif kind == "step-end" and event["state"] == "failed":
            red = [command for command, green in self._gates.get(ticket, {}).items() if not green]
            if red:
                return f"the gates are not green: {'; '.join(red)}"
            return f"the step failed: {event['reason']}" if event.get("reason") else "the step failed"
        return None


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

    def wait(self, seconds: float) -> None:
        """Wait `seconds` before the next read of the journal. A sink can draw again while it waits (#147)."""

    def interrupted(self) -> None:
        """Ctrl-C ends the watch. The run goes on."""

    def end(self) -> None:
        """The watch ends. It comes after the reason to end, or after the `run-end` or the `paused` event."""


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

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def interrupted(self) -> None:
        return None  # the command prints the line of an interrupted watch on stderr

    def end(self) -> None:
        return None


def follow(journal: Path, options: WatchOptions, sink: Sink) -> WatchResult:
    """Follow a journal: tell each event, and the reason to end, to `sink` as it happens.

    `delegate.run.runs.journal_of` finds the journal of a run. Raise JournalError when the journal is not valid or
    `from_position` is beyond its end. Return the exit code and the `seq` of the last event that was reported.

    Ctrl-C (`KeyboardInterrupt`) while the watch follows the journal ends the watch with `EXIT_INTERRUPTED`. The sink
    gets `interrupted`, and then `end`, so it can draw its last frame.
    """
    result = _follow(options, journal, sink)
    sink.end()
    return result


def _resumed_at(events: list[dict[str, object]]) -> int:
    """The `seq` of the first `resume` after the newest `run-end` that a `resume` follows, or 0 when there is none.

    A run that stopped with no `run-end` (a crash) and was resumed keeps its events: a problem there still counts. So a
    `resume` after a crash does not move the end of the history.
    """
    at = 0
    ended = False
    for event in events:
        if event["event"] == "run-end":
            ended = True
        elif event["event"] == "resume" and ended:
            ended, at = False, int(event["seq"])  # type: ignore[call-overload]
    return at


def _follow(options: WatchOptions, journal: Path, sink: Sink) -> WatchResult:
    """Follow the journal. Return the exit code and the `seq` of the last event that was reported."""
    started = time.monotonic()
    offset, next_seq, position = 0, 1, options.from_position
    tracker = _Tracker()
    files: set[Path] = set()  # the event streams and the gate logs that the journal names
    last_event: dict[str, object] | None = None
    first_read = True
    resumed_at = 0
    past: set[object] = set()  # the events of the replay that the run went past; a later read adds none (#146)
    try:
        while True:
            events, offset = read_new_events(journal, offset, next_seq)
            next_seq += len(events)
            if first_read and next_seq - 1 < options.from_position:
                raise JournalError(f"journal {journal} holds {next_seq - 1} events, so --from {options.from_position} is beyond its end")
            if first_read:
                resumed_at = _resumed_at(events)
                past = _gone_past(events)
            else:
                past = set()
            first_read = False
            # An event that a later event of the same read supersedes (a retry continues the run) does not end the watch.
            superseded = _superseded(events)
            for event in events:
                last_event = event
                seq = int(event["seq"])  # type: ignore[call-overload]
                files.update(Path(str(event[field])) for field in ACTIVITY_FIELDS if field in event)
                problem = tracker.problem(event)  # an event before the position still counts for the events after it
                # A run that ended and was resumed works again. Its events before the first `resume` after its newest
                # `run-end` that a `resume` follows are its history, and so is an event that a later event of the same
                # read supersedes: a problem, a verdict or a `run-end` there does not end the watch.
                history = seq < resumed_at or seq in superseded
                # A problem in the replay that a later event of the replay handles is history too, but only for the
                # problem: a verdict or a stop there keeps its rule.
                if history or seq in past:
                    problem = None
                ends = event["event"] in RUN_STOPS and not history
                if seq <= options.from_position:
                    sink.event(event, False, None)
                    if ends:
                        return WatchResult(_stop_code(event), position)
                    continue
                sink.event(event, True, problem)
                position = seq  # after the sink reported the event, so Ctrl-C during the report does not skip it
                if problem is not None:
                    return WatchResult(EXIT_PROBLEM, position)
                if options.until == UNTIL_VERDICT and event["event"] == "verdict" and not history:
                    sink.verdict(event)
                    return WatchResult(EXIT_VERDICT, position)
                if ends:
                    return WatchResult(_stop_code(event), position)
            if options.stall_minutes is not None and time.time() - last_change(journal, files) >= options.stall_minutes * 60:
                sink.stall(options.stall_minutes, last_event)
                return WatchResult(EXIT_STALL, position)
            if options.max_minutes is not None and time.monotonic() - started >= options.max_minutes * 60:
                sink.time_limit(options.max_minutes)
                return WatchResult(EXIT_TIME_LIMIT, position)
            sink.poll()
            sink.wait(options.poll_seconds)
    except KeyboardInterrupt:
        # Ctrl-C: the watch ends, and the run goes on. `position` is the last event that the sink reported.
        sink.interrupted()
        return WatchResult(EXIT_INTERRUPTED, position)
