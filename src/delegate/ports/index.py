"""The run index port: the interface that the engine drives to register a run in a list that each host can read.

The run index is a list of the runs of each host, in a place that every host can read (ADR 0008). The journal of a run
stays the record of the run. The index is derived from the journals, so a failed write of the index loses nothing. The
words here are the words of the domain: record a run, list the records, forget a run. The port names no file system. A
backend in `delegate.adapters` implements the interface, and the command-line driver hands it to the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


class RunIndexError(Exception):
    """A backend cannot record, list or forget a run. The message says why and names the place."""


@dataclass(frozen=True)
class RunRecord:
    """The entry of one run in the index. The values come from the snapshot of the run at the write.

    `host` is the host of `run-start`. `repo` is the name of the repository, not its path. `started` is the time of
    `run-start` and `heard` is the time of the write, both as ISO 8601 text in UTC. `state` is the state of the snapshot.
    `eta` is the time of the estimated end as ISO 8601 text, or None when the snapshot has none.
    """

    run_id: str
    host: str
    repo: str
    started: str
    state: str
    tickets_ended: int
    tickets_total: int
    eta: str | None
    heard: str

    def started_time(self) -> datetime | None:
        """The time of `started`, or None when the text is not an ISO 8601 time. A caller decides what a record with no time means."""
        return _time_or_none(self.started)

    def heard_time(self) -> datetime | None:
        """The time of `heard`, or None when the text is not an ISO 8601 time. A caller decides what a record with no time means."""
        return _time_or_none(self.heard)


def _time_or_none(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


@runtime_checkable
class RunIndex(Protocol):
    """Keeps one record for each run."""

    def record(self, record: RunRecord) -> None:
        """Keep `record` as the record of its run. It replaces the older record of the same run, and a reader never sees half of it.

        Raise RunIndexError when the backend cannot keep it.
        """
        ...

    def list_records(self) -> list[RunRecord]:
        """The record of each run that the index holds, in no fixed order. A record that cannot be read is not in the list.

        An index that does not exist yet holds no record. Raise RunIndexError when the backend cannot be read.
        """
        ...

    def forget(self, run_id: str) -> None:
        """Remove the record of the run `run_id`. A run with no record is not an error. Raise RunIndexError when the backend cannot remove it."""
        ...
