"""The run journal: an append-only JSONL file, one line for each event.

The engine writes each event as one line and never rewrites a line. Every
event holds `seq` (the position in the file, from 1), `time` (UTC, ISO 8601)
and `event` (the event name), plus the fields of that event.

A resume opens the journal of an earlier run and appends to it. `seq` goes on
from the last line.

An append writes one line with one write call. `seq` grows only after the write succeeded, so a failed append leaves
the journal as it was (#191). A power loss can cut the last line in half. `Journal.reopen` removes a last line that has
no newline at its end, and records the removal in `journal-repaired`.
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .events import JOURNAL_REPAIRED, RUN_BRANCH_ADVANCE, RUN_END, VERDICT

# The events whose line must be on the disk before the engine goes on: a run that ended, a move of the run branch, and a
# verdict. A power loss then cannot take a fact that the repository already shows.
SYNCED: frozenset[str] = frozenset({RUN_END, RUN_BRANCH_ADVANCE, VERDICT})


class JournalError(Exception):
    """A journal cannot be read. The message names the file."""


def read_events(path: Path, *, torn_tail: bool = False) -> list[dict[str, object]]:
    """The events of a journal file, in order. Raise JournalError when the file is missing or a line is not an event.

    With `torn_tail`, a last line that has no newline at its end is not read: a power loss cut it, and `Journal.reopen`
    removes it. A reader that changes nothing uses this form to read the journal that a resume or a clean goes on with.
    """
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise JournalError(f"journal {path} not found") from None
    if torn_tail and text and not text.endswith("\n"):
        text = text[: text.rfind("\n") + 1]
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


# The result of the `run-end` that `delegate clean --abandon` writes (#201). The engine never writes it.
ABANDONED: str = "abandoned"


def abandoned_end(events: list[dict[str, object]]) -> dict[str, object] | None:
    """The `run-end` of a run that the owner abandoned, or None: the newest `run-end` has the result `abandoned` (`newest_run_end`)."""
    end = newest_run_end(events)
    return end if end is not None and end["result"] == ABANDONED else None


def abandon_note(end: dict[str, object]) -> str:
    """The text that gives the state and the reason of an abandoned run: `abandoned: <reason>`. An old `run-end` has no reason."""
    return f"abandoned: {end['reason']}" if end.get("reason") else "abandoned"


# The events that a command writes to the journal of a run that ended, after its `run-end`. They do not continue the
# run: `delegate clean` writes `cleanup` (#168), and `lock-broken` when it removes a stale lock (#207). The engine
# writes its own `cleanup` before `run-end`.
AFTER_END: frozenset[str] = frozenset({"cleanup", "lock-broken"})


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


def halt_of(events: list[dict[str, object]]) -> dict[str, object] | None:
    """The newest `halted` event of the run of the events, or None: the run is halted when it comes after the newest `resume`.

    A failure of the harness halts a run (#177). The run process ends with no `run-end`, and a resume goes on.
    """
    for event in reversed(events):
        if event["event"] == "resume":
            return None
        if event["event"] == "halted":
            return event
    return None


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

        A last line with no newline at its end is a write that a power loss cut. It is removed, and `journal-repaired`
        records the number of bytes removed; the events that come back hold that event.

        Raise JournalError when the file is missing or a line is not an event.
        """
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise JournalError(f"journal {path} not found") from None
        removed = len(data) - (data.rfind(b"\n") + 1) if data and not data.endswith(b"\n") else 0
        if removed:
            os.truncate(path, len(data) - removed)
        events = read_events(path)
        journal = cls(path, seq=len(events), create=False)
        if removed:
            journal.append(JOURNAL_REPAIRED, bytes_removed=removed)
            events = read_events(path)
        return journal, events

    @property
    def next_seq(self) -> int:
        """The `seq` that the next event gets. A file that a crash record names holds this number."""
        return self._seq + 1

    def append(self, event: str, **fields: object) -> None:
        """Write one event as one line. `seq` grows only when the write succeeded; a failed write leaves no part of the line."""
        seq = self._seq + 1
        line = {"seq": seq, "time": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        data = (json.dumps(line, sort_keys=False) + "\n").encode()
        size = self.path.stat().st_size
        try:
            with self.path.open("ab") as handle:
                handle.write(data)
                if event in SYNCED:
                    handle.flush()
                    os.fsync(handle.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.truncate(self.path, size)  # a part of the line must not stay: the next line would join it
            raise
        self._seq = seq
