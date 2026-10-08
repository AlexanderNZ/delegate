"""The OpenCode adapter: drive `opencode run` in JSON mode.

The command is `opencode run --format json --model <model> --agent <agent>`.
The prompt goes on standard input, never in argv, so a long brief meets no
argument limit and shows in no process list. The harness writes one JSON event
on each line of standard output. The adapter copies that stream to a file and
reads the end state and the session id from it.
"""

from __future__ import annotations

import subprocess

from .adapters import FAILED, FINISHED, AdapterRequest, AdapterResult, read_events, stream_path

# The command that starts the harness.
COMMAND: str = "opencode"

# The column of the tier table that holds the OpenCode models.
TIER_COLUMN: str = "opencode"


class OpenCodeAdapter:
    """Run one agent through `opencode run`."""

    tier_column: str = TIER_COLUMN
    supports_resume: bool = True

    def run(self, request: AdapterRequest) -> AdapterResult:
        command = [COMMAND, "run", "--format", "json", "--model", request.model, "--agent", request.agent]
        stream = stream_path(request.report_path)
        with stream.open("w") as out:
            process = subprocess.run(command, input=request.prompt, stdout=out, stderr=subprocess.PIPE, text=True, cwd=request.cwd)
        end_state, session_id = FAILED, None
        for event in read_events(stream):
            session_id = event.get("sessionID", session_id)
            if event.get("type") == "step_finish":
                end_state = FINISHED
        return AdapterResult(process.returncode, end_state, session_id, stream)
