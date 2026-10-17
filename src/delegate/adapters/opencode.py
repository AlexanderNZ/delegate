"""The OpenCode adapter: drive `opencode run` in JSON mode.

The command is `opencode run --format json --model <model> --agent <agent>
--dir <working directory>`. The harness takes its project from the PWD variable
that a caller inherits, not from the working directory of the process, so the
command names the directory.
The prompt goes on standard input, never in argv, so a long brief meets no
argument limit and shows in no process list. The harness writes one JSON event
on each line of standard output. The adapter copies that stream to the file
that the request names, and reads the end state and the session id from it.
The exit status does not change the end state (see `AdapterResult`). After a
valid report, the adapter ends a session that writes no event for the idle
timeout of the request (see `process.py`).

The activity of a stream comes from the `step_finish` events and the
`timestamp` field (epoch milliseconds). Each `step_finish` event carries
`part.tokens.total`, the tokens of that step, so the adapter adds them.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..ports.harness import CAPPED, FAILED, FINISHED, Activity, AdapterError, AdapterRequest, AdapterResult
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
        process = run_harness(command, request)
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
        return AdapterResult(process.returncode, _end_state(events, stream), session_id, stream, process.ended_idle)

    def activity(self, stream: Path) -> Activity:
        tokens, last = 0, None
        for event in read_events(stream):
            if "timestamp" in event:
                last = datetime.fromtimestamp(int(event["timestamp"]) / 1000, timezone.utc)  # type: ignore[call-overload]
            if event.get("type") == "step_finish":
                try:
                    tokens += int(event["part"]["tokens"]["total"])  # type: ignore[index]
                except (KeyError, TypeError):
                    raise AdapterError(f"{stream}: a step_finish event has no part.tokens.total field") from None
        return Activity(tokens, last)
