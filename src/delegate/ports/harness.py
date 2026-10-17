"""The harness port: the interface that the engine drives to run one agent.

An adapter is a Python object behind one interface. The engine hands it a
request (the agent name, the model, the prompt, the working directory, the
report path, and the path of the event stream) and receives a result (the exit
status, the end state, the session id if the harness gives one, and the path of
the captured event stream). The engine chooses the path of the stream, so the
journal can name it before the agent starts.

An adapter also reads a stream of its harness, also the partial stream of an
agent that still runs, into one harness-neutral `Activity` value. The
`delegate status` command reads the streams that the journal names through it.

The engine depends on this module only. The adapters in `delegate.adapters`
implement the interface, and the command-line driver picks one by name. A test
passes a scripted adapter through the same interface, so the engine runs end to
end with no model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
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

    `event_stream` is the file where the adapter captures the event stream of
    the harness. The engine chooses it, and its directory exists. The file does
    not exist yet.

    `resume_session` is the session id of an earlier run that the adapter
    resumes. The engine sets it only for an adapter whose `supports_resume` is
    true, and only for a continuation. It is `None` for a new agent.

    `idle_timeout` (seconds) and `report_valid` let the adapter end a session
    that stays open after its work: when `report_valid()` is true and the
    harness wrote no event to the stream for `idle_timeout` seconds, the adapter
    ends the session and sets `ended_idle` in its result. `report_valid` is the
    check of the engine: it reads the report at `report_path` and tells whether
    the report is valid. An adapter that cannot end a session ignores the two
    fields.
    """

    agent: str
    model: str
    prompt: str
    cwd: Path
    report_path: Path
    event_stream: Path
    resume_session: str | None = None
    idle_timeout: float | None = None
    report_valid: Callable[[], bool] | None = None


@dataclass(frozen=True)
class AdapterResult:
    """What an adapter gives back.

    `end_state` is one of END_STATES: the end that the event stream of the
    harness gives. The exit status does not change it, because the engine weighs
    the exit status itself: a `finished` session with a valid report continues
    the step, whatever the exit status. `ended_idle` is true when the adapter
    ended the session at the idle timeout of the request. `refused` holds a
    (tool, mode) pair for each tool call that a permission mode of the harness
    refused, in the order of the stream, so the engine can name the cause.
    `final_text` is the last text of the agent in the stream, or `None`: the
    engine reads a missing verdict from it.
    """

    exit_status: int
    end_state: str
    session_id: str | None
    event_stream: Path
    ended_idle: bool = False
    refused: tuple[tuple[str, str], ...] = ()
    final_text: str | None = None

    def __post_init__(self) -> None:
        if self.end_state not in END_STATES:
            raise ValueError(f"end state {self.end_state!r} is not one of: {', '.join(END_STATES)}")


@dataclass(frozen=True)
class Activity:
    """What an event stream shows of the work of its agent, in a form that no harness owns.

    The agent used two kinds of tokens so far. `tokens_new` counts the input,
    the cache writes, and the output (with the thinking tokens): the tokens
    that the model read or wrote for the first time. `tokens_cache` counts the
    cache reads: each turn reads the context again from the cache, so this
    count grows with the turns times the size of the context. `last_event` is
    the time (UTC) of the newest event that has a time, or `None` when the
    stream holds no such event yet. When the newest event has no time and shows
    that the agent works (for example, a tool heartbeat), an adapter can give
    the modification time of the stream file.
    """

    tokens_new: int
    tokens_cache: int
    last_event: datetime | None


class Adapter(Protocol):
    """The interface of a harness adapter.

    `tier_column` names the column of the tier table that holds this harness's
    models. The engine resolves the model from that column, so an adapter never
    chooses its own model.

    `supports_resume` is true when the harness can resume a session. For a
    continuation the engine then passes the session id of the last run in
    `AdapterRequest.resume_session`. Otherwise the engine starts a new agent.

    `activity` reads a stream that this adapter captured. A live stream can end
    in half a line, which it ignores. It raises AdapterError when the file is
    missing or a line before the last one is not JSON, and the error of the file
    system when the file cannot be read. `delegate status` shows no tokens and no
    activity for a stream that raises one of these errors.
    """

    tier_column: str
    supports_resume: bool

    def run(self, request: AdapterRequest) -> AdapterResult: ...

    def activity(self, stream: Path) -> Activity: ...
