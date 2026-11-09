"""The run lock: one run at a time on one run branch.

A run takes the lock before it changes anything, and releases it when it ends.
The lock is a kernel lock (`fcntl.flock`, exclusive) on a file in
`<state dir>/locks/`, one file for each run branch (#288). The process that runs
the engine holds it for its life. The kernel releases it when the process ends,
also after a kill, so a process id that the system uses again is never a holder.

A lock is held when a non-blocking attempt at `flock` fails. A lock file that no
process holds is free, whatever process id it names. A run takes the lock with
`flock`, then writes its run id and its process id in the file. The file names
the holder only for the messages and for `delegate stop`. No step removes a
file and links another in its place, so two runs cannot both get the lock.

The release removes the file while it holds the `flock`, then drops the `flock`.
A taker that opened the old file before the removal finds that the path names
another file, or no file, and opens the path again. So a removed file is never
a lock.

A process that is killed leaves its lock file, with the run id and the process id
of the killed run. The next run that takes the lock gets that old holder, and the
journal records it (`lock-broken`).
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import quote


class LockError(Exception):
    """The run branch is locked, or the lock cannot be read. The message names the holder or the file."""


@dataclass(frozen=True)
class LockHolder:
    run_id: str
    pid: int


# A look at a lock (`live_holder`, `check_free`) holds a shared `flock` for a moment, so an attempt to take the lock at
# the same time can fail. A take tries again for this many seconds before it reports the lock as held.
TAKE_SECONDS: float = 0.5

# The seconds that a look waits for the holder to write its run id and process id after it took the lock.
READ_SECONDS: float = 1.0

_POLL_SECONDS: float = 0.01


def _lock_path(state_dir: Path, run_branch: str) -> Path:
    return state_dir / "locks" / (quote(run_branch, safe="") + ".lock")


def _parse(text: str, path: Path) -> LockHolder:
    try:
        data = json.loads(text)
        return LockHolder(run_id=str(data["run_id"]), pid=int(data["pid"]))
    except (ValueError, KeyError, TypeError) as error:
        raise LockError(f"lock file {path} cannot be read ({error!r}); remove it by hand when no run uses it") from None


def _read_fd(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while chunk := os.read(fd, 65536):
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _try_flock(fd: int, operation: int) -> bool:
    """True when the non-blocking `flock` gets the lock, False when another open file holds it."""
    try:
        fcntl.flock(fd, operation | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            return False
        raise
    return True


def _held_by(path: Path) -> LockHolder | None:
    """The holder of the lock file `path` when a process holds its `flock`, or None when the file is free or absent.

    A shared `flock` for a moment is the look: it fails only while a process holds the exclusive lock. Raise LockError
    when a process holds the lock and the file does not name a holder within `READ_SECONDS`.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise LockError(f"lock file {path} cannot be read ({error!r}); remove it by hand when no run uses it") from None
    try:
        if _try_flock(fd, fcntl.LOCK_SH):
            return None
        # The holder takes the lock, then writes its run id and process id, so the file can be empty for a moment.
        deadline = time.monotonic() + READ_SECONDS
        while True:
            try:
                return _parse(_read_fd(fd), path)
            except LockError:
                if time.monotonic() >= deadline:
                    raise
            time.sleep(_POLL_SECONDS)
    finally:
        os.close(fd)  # this also drops a shared lock that the look got


def live_holder(state_dir: Path, run_branch: str) -> LockHolder | None:
    """The holder of the lock of the run branch while a process holds it, or None when no process holds it.

    Raise LockError when the lock file cannot be read. `delegate pause` and `delegate stop` use it to find a run that works now.
    """
    return _held_by(_lock_path(state_dir, run_branch))


def live_holder_of_run(state_dir: Path, run_id: str) -> LockHolder | None:
    """The holder of a lock of any run branch that names `run_id` while a process holds it, or None when there is none.

    A run takes its lock before it writes its journal, so a run with no journal yet has no run branch to look up.
    Raise LockError when a lock file cannot be read.
    """
    locks = state_dir / "locks"
    for path in sorted(locks.glob("*.lock")) if locks.is_dir() else []:
        holder = _held_by(path)
        if holder is not None and holder.run_id == run_id:
            return holder
    return None


def _in_use(path: Path, run_branch: str, holder: LockHolder) -> LockError:
    return LockError(f"run branch {run_branch} is in use by run {holder.run_id} (process {holder.pid}); lock file {path}")


def check_free(state_dir: Path, run_branch: str) -> None:
    """Raise LockError, as `run_lock` does, when a process holds the lock of the run branch. Create nothing.

    A caller uses it to report a run branch in use before any other check, and before `run_lock` makes the state
    directory. `run_lock` still decides: a lock that no process holds is free.
    """
    path = _lock_path(state_dir, run_branch)
    holder = _held_by(path)
    if holder is not None:
        raise _in_use(path, run_branch, holder)


def _take(path: Path, run_branch: str, run_id: str) -> tuple[int, LockHolder | None]:
    """Take the lock file `path`. Return the open file that holds the `flock`, and the old holder that the file named.

    Raise LockError when another process holds the lock for `TAKE_SECONDS`.
    """
    deadline = time.monotonic() + TAKE_SECONDS
    while True:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            got = _try_flock(fd, fcntl.LOCK_EX)
            current = got and _names(fd, path)
            old = _write_holder(fd, path, run_id) if current else None
        except BaseException:
            os.close(fd)
            raise
        if current:
            return fd, old
        os.close(fd)
        if got:
            continue  # the holder released the file and removed it after this open
        if time.monotonic() < deadline:
            time.sleep(_POLL_SECONDS)
            continue
        holder = _held_by(path)
        if holder is not None:
            raise _in_use(path, run_branch, holder)
        deadline = time.monotonic() + TAKE_SECONDS  # the holder released the lock in the meantime


def _names(fd: int, path: Path) -> bool:
    """True when `path` still names the open file `fd`. A release removes the file before it drops the `flock`."""
    try:
        return os.path.samestat(os.fstat(fd), os.stat(path))
    except FileNotFoundError:
        return False


def _write_holder(fd: int, path: Path, run_id: str) -> LockHolder | None:
    """Write the run id and the process id of this process in the lock file `fd`. Return the old holder that it named, or None."""
    text = _read_fd(fd)
    try:
        old = _parse(text, path) if text.strip() else None
    except LockError:
        old = None  # a free file that cannot be read names no holder; the new holder writes over it
    os.ftruncate(fd, 0)
    os.pwrite(fd, json.dumps({"run_id": run_id, "pid": os.getpid()}).encode(), 0)
    return old


@contextmanager
def run_lock(state_dir: Path, run_branch: str, run_id: str) -> Iterator[LockHolder | None]:
    """Hold the lock of a run branch for the run `run_id` while the block runs.

    Yield None, or the old holder that the free lock file named: a run that ended with no release, for example after
    a kill. Raise LockError when another process holds the lock. The message names the run id and the process id of
    the holder.
    """
    path = _lock_path(state_dir, run_branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, old = _take(path, run_branch, run_id)
    try:
        yield old
    finally:
        try:
            path.unlink(missing_ok=True)  # this process holds the `flock`, so the file is its own
        finally:
            os.close(fd)  # this drops the `flock`
