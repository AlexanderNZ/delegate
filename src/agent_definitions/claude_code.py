"""The Claude Code adapter: drive `claude` in headless mode.

The command is `claude --print --output-format stream-json --verbose --model
<model> --agent <agent>`. The prompt goes on standard input, never in argv, so
a long brief meets no argument limit and shows in no process list. The
harness writes one JSON event on each line of standard output. The adapter
copies that stream to a file and reads the final `result` event from it.
"""

from __future__ import annotations

import subprocess

from .adapters import CAPPED, FAILED, FINISHED, AdapterError, AdapterRequest, AdapterResult, read_events, stream_path

# The command that starts the harness.
COMMAND: str = "claude"

# The column of the tier table that holds the Claude Code models.
TIER_COLUMN: str = "claude-code"


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
        ]
        if request.resume_session is not None:
            command += ["--resume", request.resume_session]
        stream = stream_path(request.report_path)
        with stream.open("w") as out:
            process = subprocess.run(command, input=request.prompt, stdout=out, stderr=subprocess.PIPE, text=True, cwd=request.cwd)
        text = stream.read_text()
        if not text.strip():
            raise AdapterError(
                f"{COMMAND} exited with status {process.returncode} and wrote no event for agent {request.agent!r}: {process.stderr.strip()}"
            )
        end_state, session_id = FAILED, None
        for event in read_events(stream):
            if event.get("type") == "result":
                end_state, session_id = _end_state(event), event["session_id"]
        if process.returncode != 0 and end_state == FINISHED:
            end_state = FAILED
        return AdapterResult(process.returncode, end_state, session_id, stream)
