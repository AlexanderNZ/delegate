"""The shell backend of the gate port in `delegate.ports.gates`.

It runs a gate command with `bash -c`, in the worktree of the ticket, through
`subprocess`. The run context never starts a process: this module does it for
the gates, as `delegate.adapters.git` does it for git.

The backend reads the standard output and the standard error of the command
as they come. When the caller gives a log file, the backend appends each part
to that file at once, so the file grows while the command runs. The result
holds the standard output, then the standard error, as before.

The command gets its standard input from `/dev/null`, so a gate that reads it gets end of file at once and does not wait
for the terminal of the engine (#178). It starts in a session of its own, so it leads a process group that holds each
process it starts. When an interrupt or an error leaves the wait, the backend ends the whole group before it lets the
exception go on (see `process.end_group`).

A caller can give a time limit (#179). When the command runs longer, the backend ends the whole group in the same way, keeps
the output up to that point, and returns a result with `timed_out` set. That result is not green.

The command gets the process environment of `delegate.adapters.environment`,
the same environment that each agent gets.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import IO, BinaryIO

from ..ports.gates import GateResult
from .process import end_group
from .environment import process_environment

# The most bytes that one read from a pipe of the command takes.
READ_SIZE: int = 65536

# The most seconds that the backend waits for the output of a command that it ended at the time limit.
READER_GRACE_SECONDS: float = 5.0


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

    def run(self, command: str, worktree: Path, log: Path | None = None, timeout: float | None = None) -> GateResult:
        out: list[bytes] = []
        err: list[bytes] = []
        lock = threading.Lock()
        handle = log.open("ab") if log is not None else None
        try:
            process = subprocess.Popen(
                ["bash", "-c", command], cwd=worktree, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=process_environment(), start_new_session=True,
            )
            try:
                readers = [
                    threading.Thread(target=_copy, args=(pipe, parts, handle, lock), daemon=True)
                    for pipe, parts in ((process.stdout, out), (process.stderr, err))
                ]
                for reader in readers:
                    reader.start()
                deadline = None if timeout is None else time.monotonic() + timeout

                def remaining() -> float | None:
                    return None if deadline is None else max(deadline - time.monotonic(), 0)

                timed_out = False
                try:
                    for reader in readers:
                        reader.join(remaining())
                    if any(reader.is_alive() for reader in readers):
                        raise subprocess.TimeoutExpired(command, timeout)  # type: ignore[arg-type]
                    status = process.wait(timeout=remaining())
                except subprocess.TimeoutExpired:
                    # The command, or a process that holds its pipes, ran past the limit. Ending the group closes the pipes.
                    end_group(process)
                    for reader in readers:
                        reader.join(READER_GRACE_SECONDS)  # a process that left the group can still hold a pipe
                    status, timed_out = process.returncode, True
            except BaseException:
                end_group(process)  # an interrupt or an error: no process of the gate stays alive after the backend
                raise
        finally:
            if handle is not None:
                handle.close()
        return GateResult(exit_status=status, output=_text(out) + _text(err), timed_out=timed_out)
