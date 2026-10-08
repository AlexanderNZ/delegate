"""The run journal: an append-only JSONL file, one line for each event.

The engine writes each event as one line and never rewrites a line. Every
event holds `seq` (the position in the file, from 1), `time` (UTC, ISO 8601)
and `event` (the event name), plus the fields of that event.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class Journal:
    """Appends events to one file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._seq = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=False)

    def append(self, event: str, **fields: object) -> None:
        self._seq += 1
        line = {"seq": self._seq, "time": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        with self.path.open("a") as handle:
            handle.write(json.dumps(line, sort_keys=False) + "\n")
