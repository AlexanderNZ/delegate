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
