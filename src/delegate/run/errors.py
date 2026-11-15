"""The error of the run context that a command reports: a run that cannot start or go on.

It has its own module so that a view of the run context can name the error with no import of the engine (#331). The two errors of
a step that cannot go on, `StepFailed` and `Rejected`, are here too, so that the verification of a chain (`delegate.run.chain`,
#340) and the engine raise and catch the same classes with no import of each other.
"""

from __future__ import annotations


class EngineError(Exception):
    """The run cannot start or go on. The message names the input at fault."""


class StepFailed(Exception):
    """A fix-up round or a verifier run cannot go on. The message is the reason the step fails."""


class Rejected(StepFailed):
    """The verifier rejected the branch after the last fix-up round. `findings` holds the findings of the last verdict."""

    def __init__(self, message: str, findings: list[str]) -> None:
        super().__init__(message)
        self.findings = findings
