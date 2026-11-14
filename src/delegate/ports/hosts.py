"""The host reader port: the interface that `delegate runs` drives to read the live runs of another host (#318, ADR 0008).

The run index lists the other hosts of a fleet. This port reads the runs of one of them. The answer is the document of
`delegate runs --local --json` of that host, as a plain mapping, or `Unreachable` with a reason. The port names no way to
reach a host: a backend in `delegate.adapters` implements the interface, and the command-line driver hands it to the use case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Unreachable:
    """The answer for a host that could not be read: the read failed, the time limit passed, or the host gave no document.

    `reason` is for a person who looks into the failure: the view shows it in the row under the row of the host (#323).
    """

    reason: str


@runtime_checkable
class HostReader(Protocol):
    """Reads the runs of one host."""

    def read_runs(self, host: str, timeout_seconds: float) -> dict[str, object] | Unreachable:
        """The document of `delegate runs --local --json` of `host` as a mapping, or `Unreachable`.

        The read ends after `timeout_seconds` at the latest. A failure, a time limit, and an answer that is not a JSON object give
        `Unreachable`; this method raises nothing for an answer of the host. The format of the document is the business of the caller.
        """
        ...
