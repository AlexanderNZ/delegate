"""The harness port: the interface that the engine drives to run one agent.

An adapter is a Python object behind one interface. The engine hands it a
request (the agent name, the model, the prompt, the working directory, the
report path) and receives a result (the exit status, the end state, the session
id if the harness gives one, and the path of the captured event stream).

The engine depends on this module only. The adapters in `delegate.adapters`
implement the interface, and the command-line driver picks one by name. A test
passes a scripted adapter through the same interface, so the engine runs end to
end with no model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# The three end states of an adapter run.
FINISHED: str = "finished"
FAILED: str = "failed"
CAPPED: str = "capped"
END_STATES: tuple[str, ...] = (FINISHED, FAILED, CAPPED)


class AdapterError(RuntimeError):
    """The harness could not run the agent at all, so no end state can describe the run.

    An unknown agent, an unknown flag, or a missing login does this. The same
    cause stops every later agent, so the engine records the error as a crash and
    ends the run.
    """


@dataclass(frozen=True)
class AdapterRequest:
    """What the engine gives an adapter for one agent run.

    `resume_session` is the session id of an earlier run that the adapter
    resumes. The engine sets it only for an adapter whose `supports_resume` is
    true, and only for a continuation. It is `None` for a new agent.
    """

    agent: str
    model: str
    prompt: str
    cwd: Path
    report_path: Path
    resume_session: str | None = None


@dataclass(frozen=True)
class AdapterResult:
    """What an adapter gives back. `end_state` is one of END_STATES."""

    exit_status: int
    end_state: str
    session_id: str | None
    event_stream: Path

    def __post_init__(self) -> None:
        if self.end_state not in END_STATES:
            raise ValueError(f"end state {self.end_state!r} is not one of: {', '.join(END_STATES)}")


class Adapter(Protocol):
    """The interface of a harness adapter.

    `tier_column` names the column of the tier table that holds this harness's
    models. The engine resolves the model from that column, so an adapter never
    chooses its own model.

    `supports_resume` is true when the harness can resume a session. For a
    continuation the engine then passes the session id of the last run in
    `AdapterRequest.resume_session`. Otherwise the engine starts a new agent.
    """

    tier_column: str
    supports_resume: bool

    def run(self, request: AdapterRequest) -> AdapterResult: ...
