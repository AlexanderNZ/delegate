"""The gate port: the interface that the engine drives to run a gate command.

A gate is a command that a stack names, such as its test command. The engine
runs each gate in the worktree of a ticket, and it reads two things: the exit
status, and the output. The engine never starts the process itself. An adapter
in `delegate.adapters` implements the interface, and the command-line driver
hands it to the engine. A test passes the real adapter, or any object with the
same method.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class GateResult:
    """The outcome of one gate command.

    `exit_status` is the exit status of the command. `output` is its standard
    output followed by its standard error, as text.
    """

    exit_status: int
    output: str

    @property
    def green(self) -> bool:
        """True when the command exited with status 0."""
        return self.exit_status == 0


@runtime_checkable
class GateRunner(Protocol):
    """Runs a gate command in a directory."""

    def run(self, command: str, worktree: Path) -> GateResult:
        """Run `command` with `worktree` as its working directory, wait for it, and return the result.

        A command that fails gives a result with a non-zero `exit_status`. It
        never raises.
        """
        ...
