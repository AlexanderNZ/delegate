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
    cause stops every later agent, and the work is not at fault. So the engine
    halts the run: it records `halted` (the ticket, the role, the round and the
    message), writes no `step-end` and no `run-end`, and a resume goes on at the
    open step.
    """


@dataclass(frozen=True)
class McpServer:
    """One MCP server that a stack gives its agents, in a form that no harness owns.

    A local server has `command`: the program and its arguments. The harness
    starts it with its own environment. A remote server has `url` instead. A
    server has exactly one of the two. The adapter writes the server in the
    configuration form of its harness.
    """

    name: str
    command: tuple[str, ...] = ()
    url: str | None = None


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

    `agent_timeout` (seconds) is the time limit of the session. When the session
    runs longer, whatever it does, the adapter ends its process group and sets
    `ended_timeout` in its result. With `None`, the session has no limit. An
    adapter that cannot end a session ignores the field.

    `mcp_servers` holds the MCP servers of the stack of the agent. An empty
    tuple means that the stack names none. An adapter gives the agent these
    servers, and it gives no other server where its harness allows that.

    `added_directories` holds the directories outside `cwd` that the agent can
    read and change, for example the proof copy of a verifier (#153). An
    adapter adds them to the session where its harness allows that.

    `sealed` is the sealed agent of the run for `agent` (#217): the copy that the engine made at the start of the run,
    outside each tree. The engine hashed its files against the record of the journal before it made this request, and
    an adapter starts the session from the seal only. It is `None` when the run sealed no agent of this name. An
    adapter whose harness runs only from a seal refuses a request with no seal.
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
    mcp_servers: tuple[McpServer, ...] = ()
    added_directories: tuple[Path, ...] = ()
    agent_timeout: float | None = None
    sealed: SealedAgent | None = None


@dataclass(frozen=True)
class AdapterResult:
    """What an adapter gives back.

    `end_state` is one of END_STATES: the end that the event stream of the
    harness gives. The exit status does not change it, because the engine weighs
    the exit status itself: a `finished` session with a valid report continues
    the step, whatever the exit status. `ended_idle` is true when the adapter
    ended the session at the idle timeout of the request. `ended_timeout` is
    true when the adapter ended the session at the `agent_timeout` of the
    request. The engine then takes the session as one with no valid report,
    whatever the end state is. The event stream of such a session can hold no
    `result` event, and can be empty. `refused` holds a
    (tool, mode) pair for each tool call that a permission mode of the harness
    refused, in the order of the stream, so the engine can name the cause.
    `final_text` is the last text of the agent in the stream, or `None`: the
    engine reads a missing verdict from it. `reason` is the text that says why
    the adapter ended the session as failed when the stream does not say it, for
    example that the harness did not load the sealed agent (#217), or `None`. The
    engine puts it in the reason of the failed step.
    """

    exit_status: int
    end_state: str
    session_id: str | None
    event_stream: Path
    ended_idle: bool = False
    refused: tuple[tuple[str, str], ...] = ()
    final_text: str | None = None
    ended_timeout: bool = False
    reason: str | None = None

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


@dataclass(frozen=True)
class SealedFile:
    """One file of a seal: its path, and the SHA-256 of its content as a hex string."""

    path: Path
    sha256: str


@dataclass(frozen=True)
class SealedAgent:
    """An agent that an adapter sealed (#216): a copy of its definition in a directory that the adapter chose.

    `agent` is the name of the agent. `handle` is the name that the harness gets for the session, and the adapter
    chooses it. `directory` is the seal directory, and `files` are the files of the seal, each with its path and its
    SHA-256. A file of the seal can be outside `directory`: the Claude Code seal holds the user settings so that a
    change of them shows. The guard of the agent is in these files, so `Adapter.permits` judges a command against them,
    and a change of the source file after the seal does not change the seal. `source` is the agent file that the
    adapter sealed, or `None` when a record of the journal has no source.
    """

    agent: str
    handle: str
    directory: Path
    files: tuple[SealedFile, ...]
    source: Path | None = None


class Adapter(Protocol):
    """The interface of a harness adapter.

    `tier_column` names the column of the tier table that holds this harness's
    models. The engine resolves the model from that column, so an adapter never
    chooses its own model.

    `supports_resume` is true when the harness can resume a session. For a
    continuation the engine then passes the session id of the last run in
    `AdapterRequest.resume_session`. Otherwise the engine starts a new agent.

    `seal` finds the agent file of `agent` for the directory `source` (the project directory first, then the user
    directory, as the harness does), and writes the seal into the directory `into`. It gives the `SealedAgent`, or
    `None` when it finds no agent file. It changes nothing outside `into`, and `into` is empty or absent when it is
    called. The engine seals each agent of a run at the start of the run, hashes the files of the seal before each agent
    session (#217), and gives the seal to the session in `AdapterRequest.sealed`. The OpenCode adapter seals by a copy of
    the agent file, and its handle is the agent name. The Claude Code adapter seals a plugin, and its handle names the
    plugin and the agent. An adapter can refuse to seal, with an `AdapterError` that says why.

    `permits` runs the guard of a sealed agent on one command. It gives `None` when the guard permits the command, else
    the reason of the denial, as text. It reads the files of the seal, so it judges the seal and not the source.

    `agent_paths` gives the paths where the harness looks for the agent file of `agent` for the directory `source`, in
    the order of its search. A caller names them when `seal` finds nothing.

    `checks_setup` is true when `seal` and `permits` can serve the setup check of `delegate run --dry-run`. An adapter
    that has no such guard (a scripted adapter of a test) leaves it false, and the setup check says that it skipped the
    check. An adapter that does not have the attribute counts as false.

    `probes_guard` is true when the engine can start the guard probe through `run` (#219): a session from the sealed
    agent, in a temporary directory outside the repository, which runs the command `touch delegate-guard-probe` that the
    guard of a verifier must deny. The engine probes each verifier at the start of a run and in the dry run, and a result
    other than a denial ends the run. An adapter whose harness cannot run an agent from its seal outside the repository
    leaves it false, and an adapter that does not have the attribute counts as false.

    `activity` reads a stream that this adapter captured. A live stream can end
    in half a line, which it ignores. It raises AdapterError when the file is
    missing or a line before the last one is not JSON, and the error of the file
    system when the file cannot be read. `delegate status` shows no tokens and no
    activity for a stream that raises one of these errors.
    """

    tier_column: str
    supports_resume: bool
    checks_setup: bool
    probes_guard: bool

    def run(self, request: AdapterRequest) -> AdapterResult: ...

    def agent_paths(self, agent: str, source: Path) -> tuple[Path, ...]: ...

    def seal(self, agent: str, source: Path, into: Path) -> SealedAgent | None: ...

    def permits(self, sealed: SealedAgent, command: str) -> str | None: ...

    def activity(self, stream: Path) -> Activity: ...
