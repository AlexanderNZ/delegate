"""The store of last answers: the newest answer of each other host that `delegate runs` read on this machine (#323).

The store is the directory `last-answers/` in the state root of this machine, with one file for each host, `<host>.json`. The
file holds the time that this machine heard the answer and the document of `delegate runs --local --json` of the host. When a
host is unreachable, `delegate runs` shows the runs of its last answer, dim, with `heard <duration> ago`. The store writes a
temporary file in the same directory and renames it, as the run index does, so a reader never sees half a file.

The store is a copy of what the hosts said. A write that fails, and a file that cannot be read, lose nothing that a later read
of the host does not give again, so the store reports no error.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# The name of the store in the state root of this machine.
LAST_ANSWERS_NAME: str = "last-answers"


@dataclass(frozen=True)
class LastAnswer:
    """The newest answer of a host: the time that this machine heard it, and the document."""

    heard: datetime
    document: dict[str, object]


class LastAnswers:
    """The store of last answers in `directory`."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, host: str) -> Path | None:
        """The file of `host`, or None for a name that does not name a file of the store."""
        if not host or host.startswith(".") or os.sep in host or (os.altsep and os.altsep in host):
            return None
        return self.directory / f"{host}.json"

    def keep(self, host: str, heard: datetime, document: dict[str, object]) -> None:
        """Keep `document` as the last answer of `host`, heard at `heard`. A write that fails keeps the older answer."""
        path = self._path(host)
        if path is None:
            return
        temporary = self.directory / f".{host}.{os.getpid()}.tmp"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({"heard": heard.isoformat(), "document": document}) + "\n")
            os.replace(temporary, path)
        except (OSError, TypeError, ValueError):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # the store is a copy; the next read of the host writes it again

    def last(self, host: str) -> LastAnswer | None:
        """The last answer of `host`, or None when the store has none that can be read."""
        path = self._path(host)
        if path is None:
            return None
        try:
            kept = json.loads(path.read_text())
            heard = datetime.fromisoformat(kept["heard"])
            document = kept["document"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if heard.tzinfo is None or not isinstance(document, dict):
            return None
        return LastAnswer(heard, document)
