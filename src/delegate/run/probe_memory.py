"""The kept result of a denied guard probe, on the machine (#346).

The guard probe (#219) starts one harness session for each verifier at each start, each resume and each dry run, and
each session takes some seconds. Its result depends only on the sealed agent and on the harness. So a denied probe is
kept in the state root of the machine, in `<state root>/guard-probes/`, one file for each key. The key is the SHA-256 of
the seal of the verifier (`seal_digest`) and the version of the harness (`Adapter.harness_version`). A probe with the
same key in the 24 hours after the kept result starts no session: the result is `denied`, from the kept result.

- A new seal, a new harness version, or a kept result of 24 hours or more runs the probe.
- A result other than `denied` is never kept.
- An adapter that gives no harness version keeps no result, so each probe runs.
- A kept file that cannot be read, or that does not agree with its key, counts as no kept result. A kept file that
  cannot be written is no error: the next probe runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..ports.harness import Adapter, SealedAgent
from . import clock
from .runs import RunsError, state_root

# The directory of the kept results, in the state root.
DIRECTORY = "guard-probes"

# How long a kept result counts.
KEEP_FOR = timedelta(hours=24)

# The only result that is kept.
KEPT_RESULT = "denied"


def seal_digest(sealed: SealedAgent) -> str:
    """The SHA-256 of the seal of an agent: its handle, and the name and the SHA-256 of each file of the seal.

    The name of a file in the seal directory is its path relative to the directory, so a seal of the same agent in
    another directory (a dry run and the run that follows it) has the same digest. A file outside the directory, as the
    user settings of the Claude Code seal, has its absolute path.
    """
    lines = [sealed.handle]
    for file in sealed.files:
        try:
            name = file.path.relative_to(sealed.directory).as_posix()
        except ValueError:
            name = str(file.path)
        lines.append(f"{name}\t{file.sha256}")
    return hashlib.sha256("\n".join([lines[0], *sorted(lines[1:])]).encode()).hexdigest()


@dataclass(frozen=True)
class ProbeMemory:
    """The kept results of the denied probes, in `directory`, for the harness of `version`. With None for either, it keeps nothing."""

    directory: Path | None
    version: str | None

    def kept_time(self, sealed: SealedAgent) -> str | None:
        """The time of the kept denied probe of `sealed`, as ISO 8601 text, or None when no kept result counts now."""
        path = self._path(sealed)
        if path is None:
            return None
        try:
            kept = json.loads(path.read_text())
            time = str(kept["time"])
            when = datetime.fromisoformat(time)
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if (kept.get("seal"), kept.get("harness_version"), kept.get("result")) != (seal_digest(sealed), self.version, KEPT_RESULT):
            return None
        if when.tzinfo is None:
            return None
        age = clock.utc_now(clock.current()) - when
        return time if timedelta(0) <= age < KEEP_FOR else None

    def keep(self, sealed: SealedAgent, result: str) -> None:
        """Keep the result of a probe of `sealed` that ran now, when it is `denied`. Write nothing for another result."""
        path = self._path(sealed)
        if path is None or result != KEPT_RESULT:
            return
        now = clock.utc_now(clock.current()).isoformat(timespec="seconds")
        text = json.dumps({"seal": seal_digest(sealed), "harness_version": self.version, "result": result, "time": now}) + "\n"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".kept-", suffix=".tmp")
        except OSError:
            return
        try:
            with os.fdopen(handle, "w") as file:
                file.write(text)
            os.replace(temporary, path)  # a process that reads the file at the same time sees the old file or the new one
        except OSError:
            Path(temporary).unlink(missing_ok=True)

    def _path(self, sealed: SealedAgent) -> Path | None:
        if self.directory is None or not self.version:
            return None
        key = hashlib.sha256(f"{seal_digest(sealed)}\n{self.version}".encode()).hexdigest()
        return self.directory / f"{key}.json"


def memory_of(adapter: Adapter) -> ProbeMemory:
    """The kept results for the harness of `adapter`, in the state root of the machine.

    An adapter with no `harness_version`, or one that gives None, gets a memory that keeps nothing. So does a state root
    that is not an absolute path.
    """
    version_of = getattr(adapter, "harness_version", None)
    version = version_of() if callable(version_of) else None
    try:
        directory: Path | None = state_root() / DIRECTORY
    except RunsError:
        directory = None
    return ProbeMemory(directory, version)
