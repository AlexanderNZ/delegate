"""The run journal: an append-only JSONL file, one line for each event.

The engine writes each event as one line and never rewrites a line. Every
event holds `seq` (the position in the file, from 1), `time` (UTC, ISO 8601)
and `event` (the event name), plus the fields of that event.

A resume opens the journal of an earlier run and appends to it. `seq` goes on
from the last line.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class JournalError(Exception):
    """A journal cannot be read. The message names the file."""


def read_events(path: Path) -> list[dict[str, object]]:
    """The events of a journal file, in order. Raise JournalError when the file is missing or a line is not an event."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise JournalError(f"journal {path} not found") from None
    events: list[dict[str, object]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise JournalError(f"journal {path}: line {number} is not valid JSON: {error}") from None
        if not isinstance(event, dict) or event.get("seq") != number or not isinstance(event.get("event"), str):
            raise JournalError(f"journal {path}: line {number} is not an event with seq {number}")
        events.append(event)
    return events


def read_new_events(path: Path, offset: int, next_seq: int) -> tuple[list[dict[str, object]], int]:
    """The events that a journal holds beyond byte `offset`, with the offset after the last one.

    `next_seq` is the `seq` that the first new event must have. A watcher calls
    this while the engine appends, so a last line without its newline is not
    complete yet: it is left for the next call. Raise JournalError when the file
    is missing or a complete line is not an event with the expected `seq`.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except FileNotFoundError:
        raise JournalError(f"journal {path} not found") from None
    events: list[dict[str, object]] = []
    consumed = 0
    for raw in data.split(b"\n")[:-1]:
        consumed += len(raw) + 1
        seq = next_seq + len(events)
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as error:
            raise JournalError(f"journal {path}: line {seq} is not valid JSON: {error}") from None
        if not isinstance(event, dict) or event.get("seq") != seq or not isinstance(event.get("event"), str):
            raise JournalError(f"journal {path}: line {seq} is not an event with seq {seq}")
        events.append(event)
    return events, offset + consumed


def plan_ids(events: list[dict[str, object]]) -> list[str]:
    """The ticket ids of the newest plan of a run, in plan order, from its events.

    It is the `order` of the newest `plan-change`, or else the `tickets` of
    `run-start`, the first event. The engine compares a workflow with this
    plan, and `delegate status` counts its tickets.
    """
    for event in reversed(events):
        if event["event"] == "plan-change":
            return [str(t) for t in event["order"]]  # type: ignore[union-attr]
    return [str(t) for t in events[0]["tickets"]]  # type: ignore[union-attr]


def newest_run_end(events: list[dict[str, object]]) -> dict[str, object] | None:
    """The `run-end` event of a run that ended, or None.

    A run that ended and was resumed works again, so its old `run-end` does not end it when a `resume` follows.
    `delegate status` gives the state of a run by this rule, and an active run has no such `run-end`.
    """
    for event in reversed(events):
        if event["event"] == "resume":
            return None
        if event["event"] == "run-end":
            return event
    return None


# The events that a command writes to the journal of a run that ended, after its `run-end`. They do not continue the
# run: `delegate clean` writes `cleanup` (#168). The engine writes its own `cleanup` before `run-end`.
AFTER_END: frozenset[str] = frozenset({"cleanup"})


def before_after_end(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """The events without the events of `AFTER_END` at their end. The newest event of a run that ended is then `run-end`."""
    end = len(events)
    while end and events[end - 1]["event"] in AFTER_END:
        end -= 1
    return events[:end]


def is_paused(events: list[dict[str, object]]) -> bool:
    """True when the run of the events is paused: its newest `paused` comes after its newest `resume`."""
    for event in reversed(events):
        if event["event"] == "resume":
            return False
        if event["event"] == "paused":
            return True
    return False


# The fields of an event that name a file whose change is activity: the event stream of an agent, the log of a gate,
# and the log of a `prepare` command.
ACTIVITY_FIELDS: tuple[str, ...] = ("event_stream", "gate_log", "prepare_log")


def activity_files(events: list[dict[str, object]]) -> set[Path]:
    """The event streams, the gate logs and the prepare logs that the events name (`ACTIVITY_FIELDS`)."""
    return {Path(str(event[field])) for event in events for field in ACTIVITY_FIELDS if field in event}


def last_change(journal: Path, files: set[Path]) -> float:
    """The newest modification time of the journal and of the files of `files` that exist.

    The stall check of `delegate watch` and the last activity of an active run use it.
    """
    times = [journal.stat().st_mtime]
    for file in files:
        try:
            times.append(file.stat().st_mtime)
        except FileNotFoundError:
            continue  # a file that the journal names and the disk lacks cannot show a change
    return max(times)


class Journal:
    """Appends events to one file."""

    def __init__(self, path: Path, seq: int = 0, *, create: bool = True) -> None:
        self.path = path
        self._seq = seq
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=False)

    @classmethod
    def reopen(cls, path: Path) -> tuple[Journal, list[dict[str, object]]]:
        """Open the journal of an earlier run for appending. Return it with the events it holds.

        Raise JournalError when the file is missing or a line is not an event.
        """
        events = read_events(path)
        return cls(path, seq=len(events), create=False), events

    def append(self, event: str, **fields: object) -> None:
        self._seq += 1
        line = {"seq": self._seq, "time": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        with self.path.open("a") as handle:
            handle.write(json.dumps(line, sort_keys=False) + "\n")
