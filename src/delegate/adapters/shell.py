"""The shell backend of the gate port in `delegate.ports.gates`.

It runs a gate command with `bash -c`, in the worktree of the ticket, through
`subprocess`. The run context never starts a process: this module does it for
the gates, as `delegate.adapters.git` does it for git.

The backend reads the standard output and the standard error of the command
as they come. When the caller gives a log file, the backend appends each part
to that file at once, so the file grows while the command runs. The result
holds the standard output, then the standard error, as before.

The command gets the process environment of `delegate.adapters.environment`,
the same environment that each agent gets.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import IO, BinaryIO

from ..ports.gates import GateResult
from .environment import process_environment

# The most bytes that one read from a pipe of the command takes.
READ_SIZE: int = 65536


def _copy(pipe: IO[bytes], parts: list[bytes], log: BinaryIO | None, lock: threading.Lock) -> None:
    """Read `pipe` to its end. Keep each part in `parts`, and append it to `log` at once when there is a log."""
    while chunk := pipe.read1(READ_SIZE):  # type: ignore[attr-defined]
        parts.append(chunk)
        if log is not None:
            with lock:
                log.write(chunk)
                log.flush()


def _text(parts: list[bytes]) -> str:
    """The bytes of one pipe as text, with the newlines of the `text` mode of `subprocess`."""
    return b"".join(parts).decode(errors="replace").replace("\r\n", "\n").replace("\r", "\n")


class ShellGateRunner:
    """Runs a gate command through `bash -c`."""

    def run(self, command: str, worktree: Path, log: Path | None = None) -> GateResult:
        out: list[bytes] = []
        err: list[bytes] = []
        lock = threading.Lock()
        handle = log.open("ab") if log is not None else None
        try:
            process = subprocess.Popen(
                ["bash", "-c", command], cwd=worktree, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=process_environment(),
            )
            readers = [
                threading.Thread(target=_copy, args=(pipe, parts, handle, lock), daemon=True)
                for pipe, parts in ((process.stdout, out), (process.stderr, err))
            ]
            for reader in readers:
                reader.start()
            for reader in readers:
                reader.join()
            status = process.wait()
        finally:
            if handle is not None:
                handle.close()
        return GateResult(exit_status=status, output=_text(out) + _text(err))
