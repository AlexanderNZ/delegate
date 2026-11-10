"""The run journal: an append-only JSONL file, one line for each event.

The engine writes each event as one line and never rewrites a line. Every
event holds `seq` (the position in the file, from 1), `time` (UTC, ISO 8601)
and `event` (the event name), plus the fields of that event.

A resume opens the journal of an earlier run and appends to it. `seq` goes on
from the last line.

An append writes one line with one write call. `seq` grows only after the write succeeded, so a failed append leaves
the journal as it was (#191). A power loss can cut the last line in half. `Journal.reopen` removes a last line that has
no newline at its end, and records the removal in `journal-repaired`.

A `Journal` keeps in memory each event that it read when it opened the file, and each event that it appended, with the
bytes of the file (#291). The engine decides from these events, and does not read the file again. Before each append
the journal checks that the file has the size that it wrote last. A file that is longer or shorter was changed by
another writer: the journal puts back its own bytes, keeps the bytes of the other writer in a file beside the journal,
writes `invariant-violation` with the kind `journal-changed`, and raises `JournalChanged`. The readers of other
processes (`status`, `watch`, `clean`, the dry run) read the file with `read_events` and `read_new_events`.
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .events import (
    CRASH_PHASE_CHAIN, CRASH_PHASE_SETUP, INVARIANT_VIOLATION, JOURNAL_CHANGED, JOURNAL_REPAIRED, RUN_BRANCH_ADVANCE, RUN_END, VERDICT,
)

# The events whose line must be on the disk before the engine goes on: a run that ended, a move of the run branch, and a
# verdict. A power loss then cannot take a fact that the repository already shows.
SYNCED: frozenset[str] = frozenset({RUN_END, RUN_BRANCH_ADVANCE, VERDICT})


class JournalError(Exception):
    """A journal cannot be read. The message names the file."""


class JournalChanged(JournalError):
    """Another writer changed the journal that a run writes (#291). The message names the journal and the byte offset.

    The journal put back its own bytes and wrote `invariant-violation` before it raised this error, so `journal` takes
    the next append.
    """

    def __init__(self, message: str, journal: Journal) -> None:
        super().__init__(message)
        self.journal = journal


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
    return _parse(path, text)


def _parse(path: Path, text: str) -> list[dict[str, object]]:
    """The events of the text of a journal file. Raise JournalError when a line is not an event."""
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


def crash_of(events: list[dict[str, object]]) -> dict[str, object] | None:
    """The newest `crashed` event of the run of the events, or None: the run crashed when it comes after the newest `resume`.

    A crash stops a run (#281). The run process ends with no `step-end` and no `run-end`, and a resume goes on with the
    open step. A journal of an earlier version records a crash in a `step-end` and a `run-end`, and has no `crashed`.
    """
    for event in reversed(events):
        if event["event"] == "resume":
            return None
        if event["event"] == "crashed":
            return event
    return None


def crash_where(ticket: object, phase: object) -> str:
    """Where a crash stopped the run, from the `ticket` and the `phase` of its `crashed` event: `the verifier of ticket a`."""
    if phase == CRASH_PHASE_SETUP:
        return f"the start of ticket {ticket}"
    if phase == CRASH_PHASE_CHAIN:
        return f"the verification of the chain at ticket {ticket}"
    return f"the {phase} of ticket {ticket}"


def crash_note(crash: dict[str, object], run_id: str) -> str:
    """The text of a crash: where it stopped the run, the error, the file of its traceback, and the command that goes on.

    The command of a crash of the step of a `--retry` (`retry` is true) names `--retry` again: the step keeps its old
    `step-end` with the state `failed`, so a plain resume does not go on with it.
    """
    trace = f" (traceback {crash['traceback']})" if crash.get("traceback") else ""
    where = crash_where(crash.get("ticket"), crash.get("phase"))
    again = f" --retry {crash.get('ticket')}" if crash.get("retry") else ""
    return f"crashed at {where}: {crash['error']}: {crash['message']}{trace}; go on with delegate run --resume {run_id}{again}"


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


# The prefix of the file that keeps the bytes of another writer of a journal: `journal-changed-<seq>.txt` beside the
# journal, where `<seq>` is the `seq` of the `invariant-violation` event that names it (#291).
CHANGED_BYTES_PREFIX: str = "journal-changed-"


class Journal:
    """Appends events to one file, and keeps in memory each event of the file (#291)."""

    def __init__(self, path: Path, seq: int = 0, *, create: bool = True) -> None:
        self.path = path
        self._seq = seq
        self._events: list[dict[str, object]] = []
        self._data = bytearray()
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=False)

    @classmethod
    def reopen(cls, path: Path) -> tuple[Journal, list[dict[str, object]]]:
        """Open the journal of an earlier run for appending. Return it with the events it holds.

        The file is read one time. A last line with no newline at its end is a write that a power loss cut. It is removed,
        and `journal-repaired` records the number of bytes removed; the events that come back hold that event.

        Raise JournalError when the file is missing or a line is not an event.
        """
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise JournalError(f"journal {path} not found") from None
        removed = len(data) - (data.rfind(b"\n") + 1) if data and not data.endswith(b"\n") else 0
        if removed:
            data = data[: len(data) - removed]
            os.truncate(path, len(data))
        events = _parse(path, data.decode())
        journal = cls(path, seq=len(events), create=False)
        journal._events, journal._data = events, bytearray(data)
        if removed:
            journal.append(JOURNAL_REPAIRED, bytes_removed=removed)
        return journal, journal.events

    @property
    def next_seq(self) -> int:
        """The `seq` that the next event gets. A file that a crash record names holds this number."""
        return self._seq + 1

    @property
    def events(self) -> list[dict[str, object]]:
        """Each event of the journal, in order: the events that it read when it opened the file, and the events that it appended."""
        return list(self._events)

    def append(self, event: str, **fields: object) -> None:
        """Write one event as one line. `seq` grows only when the write succeeded; a failed write leaves no part of the line.

        Raise JournalChanged when the file does not have the size that the journal wrote last: another writer changed it.
        The event is then not written (see `_put_back`).
        """
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            size = -1
        if size != len(self._data):
            raise self._put_back(max(size, 0))
        self._write(event, fields)

    def _write(self, event: str, fields: dict[str, object]) -> None:
        seq = self._seq + 1
        line = {"seq": seq, "time": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        data = (json.dumps(line, sort_keys=False) + "\n").encode()
        size = len(self._data)
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
        self._events.append(line)
        self._data += data

    def _put_back(self, found: int) -> JournalChanged:
        """Put back the bytes that the journal wrote, after another writer changed the file to `found` bytes. Return the error.

        The bytes of the other writer from the first byte that differs go to `journal-changed-<seq>.txt` beside the
        journal. The journal writes `invariant-violation` with the kind `journal-changed`, the byte offset of the first
        byte that differs, the size that it wrote, the size that it found, and the file of the other bytes, if any.
        """
        try:
            now = self.path.read_bytes()
        except FileNotFoundError:
            now = b""
        ours = bytes(self._data)
        offset = next((index for index, (a, b) in enumerate(zip(now, ours)) if a != b), min(len(now), len(ours)))
        other = now[offset:]
        saved: Path | None = self.path.with_name(f"{CHANGED_BYTES_PREFIX}{self.next_seq}.txt") if other else None
        if saved is not None:
            saved.write_bytes(other)
        restore = self.path.with_name(f"{self.path.name}.restore")
        with restore.open("wb") as handle:
            handle.write(ours)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(restore, self.path)
        message = (
            f"another writer changed the journal {self.path} at byte {offset}: the run wrote {len(ours)} bytes, "
            f"and the file had {found} bytes; the run put back its own bytes"
            + (f", and the bytes of the other writer are in {saved}" if saved is not None else "")
        )
        self._write(
            INVARIANT_VIOLATION,
            {
                "kind": JOURNAL_CHANGED, "journal": str(self.path), "offset": offset, "size_written": len(ours), "size_found": found,
                **({"changed_bytes": str(saved)} if saved is not None else {}),
            },
        )
        return JournalChanged(message, self)
