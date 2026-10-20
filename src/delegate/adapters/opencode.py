"""The OpenCode adapter: drive `opencode run` in JSON mode.

The command is `opencode run --format json --model <model> --agent <agent>
--dir <working directory>`. The harness takes its project from the PWD variable
that a caller inherits, not from the working directory of the process, so the
command names the directory.
The MCP servers of the stack go to the harness in the variable
`OPENCODE_CONFIG_CONTENT`, an inline configuration that the harness merges
with its other configuration. The harness has no option that removes the MCP
servers of the user or of the project, so the agent also gets those.

The adapter does not give the `added_directories` of the request to the
session: OpenCode 1.15.10 has no option that adds a directory to a session, and
the `bash` map of a rendered verifier has no `cd` rule (#153).

The prompt goes on standard input, never in argv, so a long brief meets no
argument limit and shows in no process list. The harness writes one JSON event
on each line of standard output. The adapter copies that stream to the file
that the request names, and reads the end state and the session id from it.
The exit status does not change the end state (see `AdapterResult`). After a
valid report, the adapter ends a session that writes no event for the idle
timeout of the request (see `process.py`).

The activity of a stream comes from the `step_finish` events and the
`timestamp` field (epoch milliseconds). Each `step_finish` event carries
`part.tokens`, the tokens of that step, so the adapter adds them in two counts.
The cache reads (`cache.read`) are the cache tokens. The input, the output, the
reasoning, and the cache writes (`cache.write`) are the new tokens. A step with
no `cache` field has no cache reads and no cache writes.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..ports.harness import CAPPED, FAILED, FINISHED, Activity, AdapterError, AdapterRequest, AdapterResult, McpServer
from .environment import process_environment
from .process import run_harness
from .streams import read_events

# The command that starts the harness.
COMMAND: str = "opencode"

# The column of the tier table that holds the OpenCode models.
TIER_COLUMN: str = "opencode"

# The `reason` of a `step_finish` event, mapped to the end state of the run.
# `stop` is a normal end. `length` is the model reaching its output limit. Any
# other reason (for example `tool-calls`) means the stream ended inside the work.
STEP_REASON_END_STATE: dict[str, str] = {"stop": FINISHED, "length": CAPPED}

# The environment variable that holds an inline configuration, which the harness merges with its other configuration.
CONFIG_VARIABLE: str = "OPENCODE_CONFIG_CONTENT"

# The fields of `part.tokens` of a `step_finish` event that the count of new tokens adds. Each step has them.
NEW_FIELDS: tuple[str, ...] = ("input", "output", "reasoning")


def _environment(servers: tuple[McpServer, ...]) -> dict[str, str]:
    """The environment of the harness: the process environment of the engine, with the servers added to the inline configuration.

    The process environment of the engine (see `environment.process_environment`), unchanged, when there is no server.
    An inline configuration that the engine already has keeps its other fields
    and its own servers. Raise AdapterError when that configuration is not a
    JSON object.
    """
    environment = process_environment()
    if not servers:
        return environment
    try:
        config = json.loads(environment.get(CONFIG_VARIABLE) or "{}")
    except json.JSONDecodeError:
        config = None
    if not isinstance(config, dict) or not isinstance(config.get("mcp", {}), dict):
        raise AdapterError(f"{CONFIG_VARIABLE} must hold a JSON object to add the MCP servers of the stack")
    mcp = config.setdefault("mcp", {})
    for server in servers:
        if server.url is not None:
            mcp[server.name] = {"type": "remote", "url": server.url}
        else:
            mcp[server.name] = {"type": "local", "command": list(server.command)}
    return {**environment, CONFIG_VARIABLE: json.dumps(config)}


def _tokens_of(event: dict[str, object], stream: Path) -> tuple[int, int]:
    """The two token counts of a `step_finish` event: the new tokens, and the cache reads."""
    tokens = event.get("part", {}).get("tokens")  # type: ignore[union-attr]
    missing = [name for name in NEW_FIELDS if not isinstance(tokens, dict) or name not in tokens]
    if missing:
        raise AdapterError(f"{stream}: a step_finish event has no part.tokens.{missing[0]} field")
    cache = tokens.get("cache") or {}  # type: ignore[union-attr]
    new = sum(int(tokens[name]) for name in NEW_FIELDS) + int(cache.get("write") or 0)  # type: ignore[index]
    return new, int(cache.get("read") or 0)


def _end_state(events: list[dict[str, object]], stream: Path) -> str:
    """The end state of a stream.

    The harness reports an API error as an `error` event and still exits 0, so
    an `error` event anywhere decides the end state. Without one, the `reason`
    of the last `step_finish` event decides it. A stream with no `step_finish`
    event never reached the end of a step.
    """
    if any(event.get("type") == "error" for event in events):
        return FAILED
    finishes = [event for event in events if event.get("type") == "step_finish"]
    if not finishes:
        return FAILED
    try:
        reason = finishes[-1]["part"]["reason"]
    except (KeyError, TypeError):
        raise AdapterError(f"{stream}: the last step_finish event has no part.reason field") from None
    return STEP_REASON_END_STATE.get(reason, FAILED)


# The host variables of the claude-code adapter. OpenCode has no option that takes a settings file or a permission
# mode, so this adapter passes neither, and says so. A host sets OPENCODE_CONFIG instead, which OpenCode reads itself.
CLAUDE_CODE_HOST_VARIABLES: tuple[str, ...] = ("DELEGATE_AGENT_SETTINGS", "DELEGATE_PERMISSION_MODE")


def _warn_of_host_settings() -> None:
    """Write one line to standard error for each host variable of the claude-code adapter that is set."""
    for name in CLAUDE_CODE_HOST_VARIABLES:
        if os.environ.get(name):
            print(
                f"delegate: {name} has no effect with the opencode adapter; set OPENCODE_CONFIG to an OpenCode "
                "configuration file with a permission block instead",
                file=sys.stderr,
            )


class OpenCodeAdapter:
    """Run one agent through `opencode run`."""

    tier_column: str = TIER_COLUMN
    supports_resume: bool = True

    def run(self, request: AdapterRequest) -> AdapterResult:
        _warn_of_host_settings()
        command = [COMMAND, "run", "--format", "json", "--model", request.model, "--agent", request.agent, "--dir", str(request.cwd)]
        if request.resume_session is not None:
            command += ["--session", request.resume_session]
        stream = request.event_stream
        process = run_harness(command, request, env=_environment(request.mcp_servers))
        if not stream.read_text().strip():
            raise AdapterError(
                f"{COMMAND} exited with status {process.returncode} and wrote no event for agent {request.agent!r}: {process.stderr.strip()}"
            )
        # An agent that the harness does not find is not an error to the harness: it warns on standard error,
        # runs its default agent, and exits 0. The rules of the pair would then not apply, so the adapter stops.
        if f'agent "{request.agent}" not found' in process.stderr:
            raise AdapterError(
                f"{COMMAND} did not find agent {request.agent!r} and ran its default agent instead: {process.stderr.strip()}"
            )
        events = read_events(stream)
        session_id = next((event["sessionID"] for event in events if "sessionID" in event), None)
        # The last text part of the session is the final text of the agent.
        texts = [
            str(event["part"]["text"])  # type: ignore[index]
            for event in events
            if event.get("type") == "text" and isinstance(event.get("part"), dict) and "text" in event["part"]  # type: ignore[operator]
        ]
        final_text = texts[-1] if texts else None
        return AdapterResult(process.returncode, _end_state(events, stream), session_id, stream, process.ended_idle, final_text=final_text)

    def activity(self, stream: Path) -> Activity:
        new = cache = 0
        last = None
        for event in read_events(stream):
            if "timestamp" in event:
                last = datetime.fromtimestamp(int(event["timestamp"]) / 1000, timezone.utc)  # type: ignore[call-overload]
            if event.get("type") == "step_finish":
                more, read = _tokens_of(event, stream)
                new, cache = new + more, cache + read
        return Activity(new, cache, last)
