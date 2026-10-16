"""The Claude Code adapter: drive `claude` in headless mode.

The command is `claude --print --output-format stream-json --verbose --model
<model> --agent <agent> --disallowedTools Monitor,TaskStop`, with
`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` in its environment, so each agent runs
every command in the foreground. The prompt goes on standard input, never in
argv, so a long brief meets no argument limit and shows in no process list. The
harness writes one JSON event on each line of standard output. The adapter
copies that stream to the file that the request names, and reads the final
`result` event from it. The end state comes from that event only: the exit
status does not change it (see `AdapterResult`). After a valid report, the
adapter ends a session that writes no event for the idle timeout of the request
(see `process.py`).

The activity of a stream comes from the `result` events, the `assistant`
events, and the `timestamp` field. The harness writes one `result` event at the
end of each session, and its `usage` holds the true count of the session. The
`usage` of an `assistant` event is partial: its output count is not final. The
harness writes one event for each content block of a message, and each event of
a message repeats the usage of that message. So the token count adds the
`usage` of each `result` event. For the part of the stream after the last
`result` event (a session that runs now), it adds the newest usage of each
message id once.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from ..ports.harness import CAPPED, FAILED, FINISHED, Activity, AdapterError, AdapterRequest, AdapterResult
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

# The fields of a `usage` object that its token count adds: the input, the cache, and the output.
USAGE_FIELDS: tuple[str, ...] = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def _tokens_of(usage: dict[str, object]) -> int:
    """The token count of a `usage` object: the input, the cache, and the output."""
    return sum(int(usage.get(name) or 0) for name in USAGE_FIELDS)  # type: ignore[call-overload]


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
            "--model", request.model, "--agent", request.agent,
            "--disallowedTools", ",".join(DISALLOWED_TOOLS),
        ]
        if request.resume_session is not None:
            command += ["--resume", request.resume_session]
        stream = request.event_stream
        process = run_harness(command, request, env={**os.environ, **FOREGROUND_ENVIRONMENT})
        if not stream.read_text().strip():
            raise AdapterError(
                f"{COMMAND} exited with status {process.returncode} and wrote no event for agent {request.agent!r}: {process.stderr.strip()}"
            )
        end_state, session_id = FAILED, None
        for event in read_events(stream):
            if event.get("type") == "result":
                end_state, session_id = _end_state(event), event["session_id"]
        return AdapterResult(process.returncode, end_state, session_id, stream, process.ended_idle)

    def activity(self, stream: Path) -> Activity:
        tokens = 0
        usage_of: dict[str, dict[str, object]] = {}  # the turns of the session after the last `result` event
        last: datetime | None = None
        for event in read_events(stream):
            if "timestamp" in event:
                last = datetime.fromisoformat(str(event["timestamp"]))
            message = event.get("message")
            if event.get("type") == "result" and isinstance(event.get("usage"), dict):
                tokens += _tokens_of(event["usage"])  # type: ignore[arg-type]
                usage_of = {}
            elif event.get("type") == "assistant" and isinstance(message, dict) and isinstance(message.get("usage"), dict):
                usage_of[str(message.get("id"))] = message["usage"]
        return Activity(tokens + sum(_tokens_of(usage) for usage in usage_of.values()), last)
