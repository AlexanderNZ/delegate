"""The ssh backend of the host reader port (`delegate.ports.hosts`, #318).

It runs `ssh -o BatchMode=yes -o ConnectTimeout=5 <host> delegate runs --local --json` and reads the document from its standard output. `BatchMode` makes
`ssh` ask for no password and no passphrase, so a host that needs one is unreachable and the read never waits for a person. The process starts in a session of
its own, and the backend ends its group at the time limit (`process.end_group`), so no process of the read stays alive after the limit.
This is the only code of `delegate runs` that starts a process.

The reason of an unreachable host says what failed (#323): `ssh` cannot reach the host (exit status 255, with the last line that `ssh` wrote on
standard error), the host has no `delegate` command (exit status 127), the `delegate` of the host is older than the first version with
`delegate runs` (it does not know the subcommand), or `delegate runs` on the host failed (with its last line of standard error).
"""

from __future__ import annotations

import json
import subprocess
import tempfile

from ..ports.hosts import Unreachable
from ..run.fleet import older_reason
from .environment import process_environment
from .process import end_group

# The seconds that `ssh` waits for the connection of a host (`-o ConnectTimeout`).
CONNECT_TIMEOUT_SECONDS: int = 5

# The exit status of `ssh` when it cannot reach the host, and of a shell that finds no command.
SSH_FAILED: int = 255
NO_COMMAND: int = 127

# The words of standard error of a `delegate` that has no subcommand `runs`: the umbrella command of each earlier version, and argparse.
NO_RUNS_COMMAND: tuple[str, ...] = ("unknown subcommand 'runs'", "invalid choice: 'runs'")

# The characters of standard error that the reason reads at most.
_STDERR_LIMIT: int = 64 * 1024

# The seconds that a process that ignores SIGTERM has before SIGKILL at the time limit.
TERMINATE_GRACE_SECONDS: float = 1.0


class SshHostReader:
    """Reads the runs of a host with `ssh`."""

    def read_runs(self, host: str, timeout_seconds: float) -> dict[str, object] | Unreachable:
        command = [
            "ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}", host, "delegate", "runs", "--local", "--json",
        ]
        with tempfile.TemporaryFile("w+") as out, tempfile.TemporaryFile("w+") as err:
            try:
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=out, stderr=err, text=True, env=process_environment(),
                    start_new_session=True,
                )
            except OSError as error:
                return Unreachable(f"ssh cannot start: {error.strerror or error}")
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                end_group(process, TERMINATE_GRACE_SECONDS)
                return Unreachable(f"no answer from {host} in {timeout_seconds:g} seconds")
            except BaseException:
                end_group(process, TERMINATE_GRACE_SECONDS)  # an interrupt: no process of the read stays alive
                raise
            if process.returncode != 0:
                err.seek(0)
                return Unreachable(_failure(host, process.returncode, err.read(_STDERR_LIMIT)))
            out.seek(0)
            try:
                document = json.loads(out.read())
            except ValueError:
                return Unreachable(f"{host} gave no JSON document")
        return document if isinstance(document, dict) else Unreachable(f"{host} gave a document that is not an object")


def _failure(host: str, status: int, stderr: str) -> str:
    """The reason of a read that exited with `status` and wrote `stderr`."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    said = f": {lines[-1]}" if lines else ""
    if status == SSH_FAILED:
        return f"ssh cannot reach {host}{said}"
    if status == NO_COMMAND:
        return f"{host} has no delegate command on its PATH{said}"
    if any(words in stderr for words in NO_RUNS_COMMAND):
        return older_reason(host)
    return f"delegate runs on {host} exited with status {status}{said}"
