"""The OpenCode adapter: drive `opencode run` in JSON mode.

The command is `opencode run --format json --model <model> --agent <agent>
--dir <working directory>`. The harness takes its project from the PWD variable
that a caller inherits, not from the working directory of the process, so the
command names the directory.
The prompt goes on standard input, never in argv, so a long brief meets no
argument limit and shows in no process list. The harness writes one JSON event
on each line of standard output. The adapter copies that stream to a file and
reads the end state and the session id from it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .adapters import CAPPED, FAILED, FINISHED, AdapterError, AdapterRequest, AdapterResult, read_events, stream_path

# The command that starts the harness.
COMMAND: str = "opencode"

# The column of the tier table that holds the OpenCode models.
TIER_COLUMN: str = "opencode"

# The `reason` of a `step_finish` event, mapped to the end state of the run.
# `stop` is a normal end. `length` is the model reaching its output limit. Any
# other reason (for example `tool-calls`) means the stream ended inside the work.
STEP_REASON_END_STATE: dict[str, str] = {"stop": FINISHED, "length": CAPPED}


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


class OpenCodeAdapter:
    """Run one agent through `opencode run`."""

    tier_column: str = TIER_COLUMN
    supports_resume: bool = True

    def run(self, request: AdapterRequest) -> AdapterResult:
        command = [COMMAND, "run", "--format", "json", "--model", request.model, "--agent", request.agent, "--dir", str(request.cwd)]
        if request.resume_session is not None:
            command += ["--session", request.resume_session]
        stream = stream_path(request.report_path)
        with stream.open("w") as out:
            process = subprocess.run(command, input=request.prompt, stdout=out, stderr=subprocess.PIPE, text=True, cwd=request.cwd)
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
        end_state = _end_state(events, stream)
        if process.returncode != 0 and end_state == FINISHED:
            end_state = FAILED
        return AdapterResult(process.returncode, end_state, session_id, stream)
