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

A request can also set an agent timeout (#179). When the process runs longer than that, whatever it does and whatever
the report is, the helper ends it in the same way and tells the caller that it ended the session at the time limit.

The process starts in a session of its own, so it is the leader of a process group that holds each process it starts
(#178). `end_group` sends the signals to the group, not to the leader only: no child of the harness stays alive after
the helper ended it. An interrupt, as Ctrl-C or SIGTERM to the engine raises it, or any other exception while the helper
waits, ends the group before the exception leaves the helper, so the engine releases its lock after the group is gone.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..ports.harness import AdapterRequest
from .environment import process_environment

# The seconds that a process has to exit after SIGTERM, before SIGKILL.
TERMINATE_GRACE_SECONDS: float = 10.0

# The longest time between two looks at the stream while the process runs.
POLL_SECONDS: float = 1.0


def _signal_group(process: subprocess.Popen, signum: int) -> None:
    """Send `signum` to the process group of `process`, which is its own group. No group left is not an error."""
    try:
        os.killpg(process.pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def end_group(process: subprocess.Popen, grace: float | None = None) -> None:
    """End the process group of `process`: SIGTERM to the group, a wait for the process, then SIGKILL to the group.

    `process` must be the leader of its group (it started with `start_new_session`). The SIGKILL goes to the group also
    when the leader exited, so a child that ignored SIGTERM does not stay alive. The wait for the leader is `grace` seconds,
    `TERMINATE_GRACE_SECONDS` by default. An interrupt in the wait does not leave a member of the group alive.
    """
    _signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=TERMINATE_GRACE_SECONDS if grace is None else grace)
    except subprocess.TimeoutExpired:
        pass
    finally:
        _signal_group(process, signal.SIGKILL)
    process.wait()


@dataclass(frozen=True)
class HarnessExit:
    """How the harness process ended: its exit status, its standard error, and whether the helper ended it idle or at the agent timeout."""

    returncode: int
    stderr: str
    ended_idle: bool
    ended_timeout: bool = False


def run_harness(command: Sequence[str], request: AdapterRequest, env: Mapping[str, str] | None = None) -> HarnessExit:
    """Run `command` in `request.cwd` with the prompt on standard input, and write its output to the event stream.

    The process gets `env` (the process environment of the engine when it is None), with the entries of `request.environment` added over it (#221).
    """
    if request.environment:
        env = {**(process_environment() if env is None else env), **request.environment}
    with tempfile.TemporaryFile("w+") as prompt, tempfile.TemporaryFile("w+") as stderr, request.event_stream.open("w") as out:
        prompt.write(request.prompt)
        prompt.seek(0)
        process = subprocess.Popen(
            list(command), stdin=prompt, stdout=out, stderr=stderr, text=True, cwd=request.cwd,
            env=dict(env) if env is not None else None, start_new_session=True,
        )
        try:
            ended_idle, ended_timeout = _wait(process, request)
        except BaseException:
            end_group(process)  # an interrupt or an error: no process of the harness stays alive after the helper
            raise
        stderr.seek(0)
        return HarnessExit(process.returncode, stderr.read(), ended_idle, ended_timeout)


def _wait(process: subprocess.Popen[str], request: AdapterRequest) -> tuple[bool, bool]:
    """Wait for the process to exit. Return whether the helper ended it idle after a valid report, and whether it ended it at the agent timeout.

    The agent timeout comes first: a session that runs past it ends, also when its stream still grows.
    """
    idle = request.idle_timeout is not None and request.report_valid is not None
    if not idle and request.agent_timeout is None:
        process.wait()
        return False, False
    started = time.monotonic()
    poll = POLL_SECONDS
    if idle:
        poll = min(poll, request.idle_timeout / 5)  # type: ignore[operator]
    if request.agent_timeout is not None:
        poll = min(poll, request.agent_timeout / 5)
    size, changed = -1, started
    while True:
        wait = poll
        if request.agent_timeout is not None:
            wait = min(poll, max(started + request.agent_timeout - time.monotonic(), 0.01))
        try:
            process.wait(timeout=wait)
            return False, False
        except subprocess.TimeoutExpired:
            pass
        if request.agent_timeout is not None and time.monotonic() - started >= request.agent_timeout:
            if process.poll() is not None:  # it exited by itself at the limit
                return False, False
            end_group(process)
            return False, True
        if not idle:
            continue
        now_size = request.event_stream.stat().st_size
        if now_size != size:
            size, changed = now_size, time.monotonic()
        elif time.monotonic() - changed >= request.idle_timeout and request.report_valid():  # type: ignore[operator]
            if process.poll() is not None:  # it exited by itself during the check
                return False, False
            end_group(process)
            return True, False
