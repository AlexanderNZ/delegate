"""The gate port: the interface that the engine drives to run a gate command.

A gate is a command that a stack names, such as its test command. The engine
runs each gate in the worktree of a ticket, and it reads two things: the exit
status, and the output. While the gate runs, the runner also writes the output
to a log file that the engine names, so `delegate status` and `delegate watch`
see that a long gate is active. The engine never starts the process itself. An adapter
in `delegate.adapters` implements the interface, and the command-line driver
hands it to the engine. A test passes the real adapter, or any object with the
same method.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class GateResult:
    """The outcome of one gate command.

    `exit_status` is the exit status of the command. `output` is its standard
    output followed by its standard error, as text. `timed_out` is true when the
    runner ended the command because it ran past the time limit of the call
    (see `GateRunner.run`).
    """

    exit_status: int
    output: str
    timed_out: bool = False

    @property
    def green(self) -> bool:
        """True when the command exited with status 0 and the runner did not end it at a time limit."""
        return self.exit_status == 0 and not self.timed_out


@runtime_checkable
class GateRunner(Protocol):
    """Runs a gate command in a directory."""

    def run(
        self, command: str, worktree: Path, log: Path | None = None, timeout: float | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> GateResult:
        """Run `command` with `worktree` as its working directory, wait for it, and return the result.

        `environment` holds entries that the runner adds over the environment of the command (#221): the settings of the
        worktree, which the version-control port gives. With None, the runner adds none.

        `timeout` (seconds) is the time limit of the command. When the command
        runs longer, the runner ends the process group of the command, keeps
        the output up to that point, and returns a result with `timed_out`
        set. That result is not green, whatever the exit status is. With
        `timeout` None, the runner waits for the command without a limit.

        When `log` is not None, the runner appends the output of the command
        to that file while the command runs, so a reader of the file sees the
        progress of a long gate. The file exists before the call. The engine
        gives a new file for each gate run.

        A command that fails gives a result with a non-zero `exit_status`. It
        never raises.
        """
        ...
