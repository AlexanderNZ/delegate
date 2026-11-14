"""The ssh backend of the host reader port (`delegate.ports.hosts`, #318).

It runs `ssh -o BatchMode=yes -o ConnectTimeout=5 <host> delegate runs --local --json` and reads the document from its standard output. `BatchMode` makes
`ssh` ask for no password and no passphrase, so a host that needs one is unreachable and the read never waits for a person. The process starts in a session of
its own, and the backend ends its group at the time limit (`process.end_group`), so no process of the read stays alive after the limit.
This is the only code of `delegate runs` that starts a process.
"""

from __future__ import annotations

import json
import subprocess
import tempfile

from ..ports.hosts import Unreachable
from .environment import process_environment
from .process import end_group

# The seconds that `ssh` waits for the connection of a host (`-o ConnectTimeout`).
CONNECT_TIMEOUT_SECONDS: int = 5

# The seconds that a process that ignores SIGTERM has before SIGKILL at the time limit.
TERMINATE_GRACE_SECONDS: float = 1.0


class SshHostReader:
    """Reads the runs of a host with `ssh`."""

    def read_runs(self, host: str, timeout_seconds: float) -> dict[str, object] | Unreachable:
        command = [
            "ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}", host, "delegate", "runs", "--local", "--json",
        ]
        with tempfile.TemporaryFile("w+") as out:
            try:
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL, text=True, env=process_environment(),
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
                return Unreachable(f"ssh {host} exited with status {process.returncode}")
            out.seek(0)
            try:
                document = json.loads(out.read())
            except ValueError:
                return Unreachable(f"{host} gave no JSON document")
        return document if isinstance(document, dict) else Unreachable(f"{host} gave a document that is not an object")
