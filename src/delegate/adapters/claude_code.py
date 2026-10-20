"""The Claude Code adapter: drive `claude` in headless mode.

The command is `claude --print --output-format stream-json --verbose --model
<model> --agent <agent> --strict-mcp-config [--mcp-config <servers>]
--disallowedTools Monitor,TaskStop`, with
`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` in its environment, so each agent runs
every command in the foreground. `--strict-mcp-config` makes the harness ignore
the MCP servers of the user, of the project, and of the plugins, so the agent
gets only the MCP servers of its stack: none, or the servers that
`--mcp-config` names. A host can give every agent a settings file and
a permission mode: `DELEGATE_AGENT_SETTINGS` adds `--settings <path>`, and
`DELEGATE_PERMISSION_MODE` adds `--permission-mode <mode>`. With neither set, the
command does not change. An error tool result that says that a permission mode
refused a tool goes into `refused` of the result. The prompt goes on standard input, never in
argv, so a long brief meets no argument limit and shows in no process list. The
harness writes one JSON event on each line of standard output. The adapter
copies that stream to the file that the request names, and reads the final
`result` event from it. The end state comes from that event only: the exit
status does not change it (see `AdapterResult`). After a valid report, the
adapter ends a session that writes no event for the idle timeout of the request
(see `process.py`).

The activity of a stream comes from the `result` events, the `assistant`
events, and the `timestamp` field. Some events of an agent that works have no
`timestamp`: a `tool_progress` heartbeat while a tool runs, and a `system`
event with the subtype `thinking_tokens` while the model thinks. When the newest
event of the stream is one of these, the time is the modification time of the
stream file: the time that the harness last wrote to it. The harness writes
one `result` event at the end of each session, and its `usage` holds the true
count of the session. The
`usage` of an `assistant` event is partial: its output count is not final. The
harness writes one event for each content block of a message, and each event of
a message repeats the usage of that message. So the token counts add the
`usage` of each `result` event. For the part of the stream after the last
`result` event (a session that runs now), they add the newest usage of each
message id once. A `usage` object gives two counts: the new tokens (the input,
the cache writes, and the output) and the cache reads.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from ..ports.harness import CAPPED, FAILED, FINISHED, Activity, AdapterError, AdapterRequest, AdapterResult, McpServer
from .environment import process_environment
from .process import run_harness
from .streams import read_events

# The command that starts the harness.
COMMAND: str = "claude"

# The column of the tier table that holds the Claude Code models.
TIER_COLUMN: str = "claude-code"

# The tools that the adapter disallows for every agent. Under `--print` the
# notification of a monitor never comes to the agent, and a monitor that stays
# open keeps the session alive after the report. `TaskStop` only stops a
# background task, and the environment below removes the background tasks.
DISALLOWED_TOOLS: tuple[str, ...] = ("Monitor", "TaskStop")

# The environment that removes the background tasks. The `run_in_background`
# parameter of the Bash tool and of the subagent tool is not a tool name, so
# `--disallowedTools` cannot remove it.
FOREGROUND_ENVIRONMENT: dict[str, str] = {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}

# The environment variables of a host that give every agent a settings file and a permission mode. A host whose
# user settings refuse each tool that is not read-only (for example `defaultMode` "dontAsk" with an empty allow
# list) sets them, so the agents of a run can work. The verifier guard hook of the agent file applies on top.
SETTINGS_VARIABLE: str = "DELEGATE_AGENT_SETTINGS"
PERMISSION_MODE_VARIABLE: str = "DELEGATE_PERMISSION_MODE"

# The text of a tool result when a permission mode refused the tool, as Claude Code wrote it on 2026-10-18.
REFUSAL = re.compile(r"Permission to use (\S+) has been denied because Claude Code is running in (.+?) mode")

# The fields of a `usage` object that its count of new tokens adds: the input, the cache writes, and the output.
NEW_FIELDS: tuple[str, ...] = ("input_tokens", "cache_creation_input_tokens", "output_tokens")

# The fields of a `usage` object that its count of cache tokens adds: the cache reads.
CACHE_FIELDS: tuple[str, ...] = ("cache_read_input_tokens",)


def _mcp_config(servers: tuple[McpServer, ...]) -> str:
    """The `--mcp-config` value of the servers: one JSON object in the form of the harness configuration."""
    entries: dict[str, dict[str, object]] = {}
    for server in servers:
        if server.url is not None:
            entries[server.name] = {"type": "http", "url": server.url}
        else:
            entries[server.name] = {"command": server.command[0], "args": list(server.command[1:])}
    return json.dumps({"mcpServers": entries})


def _is_work_with_no_time(event: dict[str, object]) -> bool:
    """True for an event that the harness writes while the agent works, with no `timestamp`.

    The harness writes a `tool_progress` heartbeat every 30 seconds while a tool
    runs, and a `system` event with the subtype `thinking_tokens` while the model
    thinks. Other events with no `timestamp` (`init`, `rate_limit_event`, `result`)
    are not work that goes on.
    """
    kind = event.get("type")
    return kind == "tool_progress" or (kind == "system" and event.get("subtype") == "thinking_tokens")


def _tokens_of(usage: dict[str, object]) -> tuple[int, int]:
    """The two token counts of a `usage` object: the new tokens, and the cache reads."""
    def count(fields: tuple[str, ...]) -> int:
        return sum(int(usage.get(name) or 0) for name in fields)  # type: ignore[call-overload]

    return count(NEW_FIELDS), count(CACHE_FIELDS)


def _host_permissions() -> list[str]:
    """The options that give the agent the settings file and the permission mode of the host, or none.

    A settings file that does not exist stops the adapter before it starts an
    agent: with no settings the agent cannot work on such a host, and the
    failure would show only as a refused tool.
    """
    options: list[str] = []
    settings = os.environ.get(SETTINGS_VARIABLE)
    if settings:
        # The agent runs in its worktree, not in the directory of the engine, so a relative path can name another file.
        if not Path(settings).is_absolute():
            raise AdapterError(f"{SETTINGS_VARIABLE} is {settings!r}, which is not an absolute path")
        if not Path(settings).is_file():
            raise AdapterError(f"{SETTINGS_VARIABLE} names {settings}, which is not a file")
        options += ["--settings", settings]
    mode = os.environ.get(PERMISSION_MODE_VARIABLE)
    if mode:
        options += ["--permission-mode", mode]
    return options


def _refusals(event: dict[str, object]) -> list[tuple[str, str]]:
    """The (tool, mode) pair of each error tool result of the event that says that a permission mode refused the tool.

    Only a result with `is_error` true counts. A Read or a grep of a file that
    quotes the refusal text gives a result that is no error.
    """
    message = event.get("message")
    if event.get("type") != "user" or not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return []
    found: list[tuple[str, str]] = []
    for block in message["content"]:
        if not isinstance(block, dict) or block.get("type") != "tool_result" or block.get("is_error") is not True:
            continue
        content = block.get("content")
        if isinstance(content, str):
            texts = [content]
        elif isinstance(content, list):
            texts = [str(part.get("text", "")) for part in content if isinstance(part, dict)]
        else:
            texts = []
        found += [(match.group(1), match.group(2)) for text in texts for match in REFUSAL.finditer(text)]
    return found


def _end_state(result: dict[str, object]) -> str:
    """The end state that a `result` event gives.

    The harness sets `subtype` to `error_max_turns` when the agent reaches the
    turn cap. An API error sets `is_error` to true and leaves `subtype` as
    `success`, so `is_error` decides all other failures.
    """
    if result.get("subtype") == "error_max_turns":
        return CAPPED
    if result.get("is_error") or str(result.get("subtype", "")).startswith("error"):
        return FAILED
    return FINISHED


class ClaudeCodeAdapter:
    """Run one agent through `claude --print`."""

    tier_column: str = TIER_COLUMN
    supports_resume: bool = True

    def run(self, request: AdapterRequest) -> AdapterResult:
        command = [
            COMMAND, "--print", "--output-format", "stream-json", "--verbose",
            "--model", request.model, "--agent", request.agent, "--strict-mcp-config",
            *_host_permissions(),
        ]
        if request.mcp_servers:
            command += ["--mcp-config", _mcp_config(request.mcp_servers)]
        # The proof copy of a verifier is outside its working directory, and a dontAsk host refuses a tool call there
        # unless the directory is added to the session (#153).
        for directory in request.added_directories:
            command += ["--add-dir", str(directory)]
        # `--mcp-config` and `--add-dir` take more than one value, so this option ends their values.
        command += ["--disallowedTools", ",".join(DISALLOWED_TOOLS)]
        if request.resume_session is not None:
            command += ["--resume", request.resume_session]
        stream = request.event_stream
        process = run_harness(command, request, env={**process_environment(), **FOREGROUND_ENVIRONMENT})
        if not stream.read_text().strip():
            raise AdapterError(
                f"{COMMAND} exited with status {process.returncode} and wrote no event for agent {request.agent!r}: {process.stderr.strip()}"
            )
        end_state, session_id, final_text = FAILED, None, None
        refused: list[tuple[str, str]] = []
        for event in read_events(stream):
            if event.get("type") == "result":
                end_state, session_id = _end_state(event), event["session_id"]
                final_text = event["result"] if isinstance(event.get("result"), str) else final_text
            refused += _refusals(event)
        return AdapterResult(
            process.returncode, end_state, session_id, stream, process.ended_idle, tuple(refused), final_text=final_text
        )

    def activity(self, stream: Path) -> Activity:
        new = cache = 0
        usage_of: dict[str, dict[str, object]] = {}  # the turns of the session after the last `result` event
        last: datetime | None = None
        events = read_events(stream)
        for event in events:
            if "timestamp" in event:
                last = datetime.fromisoformat(str(event["timestamp"]))
            message = event.get("message")
            if event.get("type") == "result" and isinstance(event.get("usage"), dict):
                more, read = _tokens_of(event["usage"])  # type: ignore[arg-type]
                new, cache = new + more, cache + read
                usage_of = {}
            elif event.get("type") == "assistant" and isinstance(message, dict) and isinstance(message.get("usage"), dict):
                usage_of[str(message.get("id"))] = message["usage"]
        if events and "timestamp" not in events[-1] and _is_work_with_no_time(events[-1]):
            last = datetime.fromtimestamp(stream.stat().st_mtime, timezone.utc)
        for usage in usage_of.values():
            more, read = _tokens_of(usage)
            new, cache = new + more, cache + read
        return Activity(new, cache, last)
