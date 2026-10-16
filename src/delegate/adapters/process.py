"""The helper that every harness adapter uses to run the harness process.

`run_harness` starts the command, gives it the prompt on standard input, and
writes its standard output to the event stream of the request. It waits for the
process to exit.

A request can set an idle timeout and a report check (see
`AdapterRequest`). Then the helper also watches the stream while the process
runs. When the stream did not grow for the idle timeout and the report check is
true, the session has done its work and only stays open, so the helper ends it:
it sends SIGTERM, and SIGKILL when the process has not exited after
`TERMINATE_GRACE_SECONDS`.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..ports.harness import AdapterRequest

# The seconds that a process has to exit after SIGTERM, before SIGKILL.
TERMINATE_GRACE_SECONDS: float = 10.0

# The longest time between two looks at the stream while the process runs.
POLL_SECONDS: float = 1.0


@dataclass(frozen=True)
class HarnessExit:
    """How the harness process ended: its exit status, its standard error, and whether the helper ended it idle."""

    returncode: int
    stderr: str
    ended_idle: bool


def run_harness(command: Sequence[str], request: AdapterRequest, env: Mapping[str, str] | None = None) -> HarnessExit:
    """Run `command` in `request.cwd` with the prompt on standard input, and write its output to the event stream."""
    with tempfile.TemporaryFile("w+") as prompt, tempfile.TemporaryFile("w+") as stderr, request.event_stream.open("w") as out:
        prompt.write(request.prompt)
        prompt.seek(0)
        process = subprocess.Popen(
            list(command), stdin=prompt, stdout=out, stderr=stderr, text=True, cwd=request.cwd,
            env=dict(env) if env is not None else None,
        )
        ended_idle = _wait(process, request)
        stderr.seek(0)
        return HarnessExit(process.returncode, stderr.read(), ended_idle)


def _wait(process: subprocess.Popen[str], request: AdapterRequest) -> bool:
    """Wait for the process to exit. End it when it is idle after a valid report, and return True then."""
    if request.idle_timeout is None or request.report_valid is None:
        process.wait()
        return False
    poll = min(POLL_SECONDS, request.idle_timeout / 5)
    size, changed = -1, time.monotonic()
    while True:
        try:
            process.wait(timeout=poll)
            return False
        except subprocess.TimeoutExpired:
            pass
        now_size = request.event_stream.stat().st_size
        if now_size != size:
            size, changed = now_size, time.monotonic()
        elif time.monotonic() - changed >= request.idle_timeout and request.report_valid():
            if process.poll() is not None:  # it exited by itself during the check
                return False
            process.terminate()
            try:
                process.wait(timeout=TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return True
