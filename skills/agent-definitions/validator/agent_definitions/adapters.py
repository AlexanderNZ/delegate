"""The harness adapter interface and the adapter registry.

An adapter is a Python object behind one interface. The engine hands it a
request (the agent name, the model, the prompt, the working directory, the
report path) and receives a result (the exit status, the end state, the session
id if the harness gives one, and the path of the captured event stream).

Adapters register by name. The workflow names the adapter. A test registers a
scripted adapter through the same interface, so the engine runs end to end with
no model.
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


@dataclass(frozen=True)
class AdapterRequest:
    """What the engine gives an adapter for one agent run."""

    agent: str
    model: str
    prompt: str
    cwd: Path
    report_path: Path


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
    """

    tier_column: str

    def run(self, request: AdapterRequest) -> AdapterResult: ...


_REGISTRY: dict[str, Adapter] = {}


def register(name: str, adapter: Adapter) -> None:
    """Register an adapter under a name. Raise ValueError when the name is taken."""
    if name in _REGISTRY:
        raise ValueError(f"adapter {name!r} is already registered")
    _REGISTRY[name] = adapter


def unregister(name: str) -> None:
    """Remove an adapter. Raise KeyError when no adapter has the name."""
    del _REGISTRY[name]


def get(name: str) -> Adapter:
    """The adapter registered under the name. Raise KeyError when there is none."""
    return _REGISTRY[name]


def registered_names() -> list[str]:
    return sorted(_REGISTRY)
