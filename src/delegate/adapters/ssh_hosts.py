"""The ssh backend of the host reader port (`delegate.ports.hosts`, #318).

It runs `ssh -o BatchMode=yes -o ConnectTimeout=5 <host> delegate runs --local --json` and reads the document from its standard output. `BatchMode` makes
`ssh` ask for no password and no passphrase, so a host that needs one is unreachable and the read never waits for a person. The process starts in a session of
its own, and the backend ends its group at the time limit (`process.end_group`), so no process of the read stays alive after the limit.
It ends the group in the same way when the caller sets `stop` (#328): Ctrl-C does not wait for the time limit. The output of `ssh` is read as bytes and
decoded as UTF-8 with replacement, so a banner that is not UTF-8 does not hide the reason. This is the only code of `delegate runs` that starts a process.

The reason of an unreachable host says what failed (#323): `ssh` cannot reach the host (exit status 255, with the last line that `ssh` wrote on
standard error), the host has no `delegate` command (exit status 127), the `delegate` of the host is older than the first version with
`delegate runs` (it does not know the subcommand: the kind `OLDER_VERSION`, and the use case makes the sentence, #333), or `delegate runs` on the host failed (with its last line of standard error).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time

from ..ports.hosts import OLDER_VERSION, Unreachable
from .environment import process_environment
from .process import end_group

# The seconds that `ssh` waits for the connection of a host (`-o ConnectTimeout`).
CONNECT_TIMEOUT_SECONDS: int = 5

# The exit status of `ssh` when it cannot reach the host, and of a shell that finds no command.
SSH_FAILED: int = 255
NO_COMMAND: int = 127

# The words of standard error of a `delegate` that has no subcommand `runs`: the umbrella command of each earlier version, and argparse.
NO_RUNS_COMMAND: tuple[str, ...] = ("unknown subcommand 'runs'", "invalid choice: 'runs'")

# The bytes of standard error that the reason reads at most.
_STDERR_LIMIT: int = 64 * 1024

# The longest time between two looks at the `stop` of the caller while `ssh` runs (#328).
STOP_POLL_SECONDS: float = 0.05

# The seconds that a process that ignores SIGTERM has before SIGKILL at the time limit.
READ_TERMINATE_GRACE_SECONDS: float = 1.0


class SshHostReader:
    """Reads the runs of a host with `ssh`."""

    def read_runs(self, host: str, timeout_seconds: float, stop: threading.Event | None = None) -> dict[str, object] | Unreachable:
        command = [
            "ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}", host, "delegate", "runs", "--local", "--json",
        ]
        with tempfile.TemporaryFile("w+b") as out, tempfile.TemporaryFile("w+b") as err:
            try:
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=process_environment(), start_new_session=True,
                )
            except OSError as error:
                return Unreachable(f"ssh cannot start: {error.strerror or error}")
            try:
                ended = _wait(process, timeout_seconds, stop)
            except BaseException:
                end_group(process, READ_TERMINATE_GRACE_SECONDS)  # an interrupt: no process of the read stays alive
                raise
            if not ended:
                end_group(process, READ_TERMINATE_GRACE_SECONDS)
                if stop is not None and stop.is_set():
                    return Unreachable(f"the read of {host} stopped")
                return Unreachable(f"no answer from {host} in {timeout_seconds:g} seconds")
            if process.returncode != 0:
                err.seek(0)
                return _failure(host, process.returncode, _text(err.read(_STDERR_LIMIT)))
            out.seek(0)
            try:
                document = json.loads(_text(out.read()))
            except ValueError:
                return Unreachable(f"{host} gave no JSON document")
        return document if isinstance(document, dict) else Unreachable(f"{host} gave a document that is not an object")


def _wait(process: subprocess.Popen[bytes], timeout_seconds: float, stop: threading.Event | None) -> bool:
    """Wait for the exit of `process`. Return False when the time limit passed, or when `stop` is set, first."""
    deadline = time.monotonic() + timeout_seconds
    while stop is None or not stop.is_set():
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        try:
            process.wait(timeout=left if stop is None else min(left, STOP_POLL_SECONDS))
            return True
        except subprocess.TimeoutExpired:
            continue
    return process.poll() is not None


def _text(output: bytes) -> str:
    """The text of the output of `ssh`. A byte that is not UTF-8, as in a banner of the host, becomes U+FFFD (#328)."""
    return output.decode("utf-8", errors="replace")


def _failure(host: str, status: int, stderr: str) -> Unreachable:
    """The answer of a read that exited with `status` and wrote `stderr`. A host with no `delegate runs` has the kind `OLDER_VERSION`; the use case says it (#333)."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    said = f": {lines[-1]}" if lines else ""
    if status == SSH_FAILED:
        return Unreachable(f"ssh cannot reach {host}{said}")
    if status == NO_COMMAND:
        return Unreachable(f"{host} has no delegate command on its PATH{said}")
    if any(words in stderr for words in NO_RUNS_COMMAND):
        return Unreachable(f"{host} has no delegate runs command{said}", OLDER_VERSION)
    return Unreachable(f"delegate runs on {host} exited with status {status}{said}")
