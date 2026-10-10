"""The run lock: one run at a time on one run branch.

A run takes the lock before it changes anything, and releases it when it ends.
The lock is a file in `<state dir>/locks/`, one for each run branch. It holds the
run id and the process id of the holder. The file is put in place with a hard link
that fails when the lock exists, so two runs that start at the same time cannot both get it.

A process that is killed cannot release its lock. A later run finds a lock whose
process no longer exists, reports it, and removes it only when the caller asks
for that with `break_stale`. A lock whose process still exists is never removed.
"""

from __future__ import annotations

import json
import os
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


def _lock_path(state_dir: Path, run_branch: str) -> Path:
    return state_dir / "locks" / (quote(run_branch, safe="") + ".lock")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # the process exists, and another user owns it
    return True


def _read_holder(path: Path) -> LockHolder:
    try:
        data = json.loads(path.read_text())
        return LockHolder(run_id=str(data["run_id"]), pid=int(data["pid"]))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise LockError(f"lock file {path} cannot be read ({error!r}); remove it by hand when no run uses it") from None


def _refuse(path: Path, run_branch: str, holder: LockHolder, break_stale: bool) -> None:
    """Raise LockError for a lock that the caller may not take. Return when the lock is stale and `break_stale` is set."""
    if _alive(holder.pid):
        raise LockError(
            f"run branch {run_branch} is in use by run {holder.run_id} (process {holder.pid}); lock file {path}"
        )
    if not break_stale:
        raise LockError(
            f"run branch {run_branch} is locked by run {holder.run_id}, process {holder.pid}, which no longer exists; "
            f"pass --break-lock to remove the lock (lock file {path})"
        )


def check_free(state_dir: Path, run_branch: str, *, break_stale: bool = False) -> None:
    """Raise LockError, as `run_lock` does, when the lock of the run branch is held. Create nothing.

    A caller uses it to report a run branch in use before any other check, and
    before `run_lock` makes the state directory. `run_lock` still decides: it
    holds the lock with an atomic link.
    """
    path = _lock_path(state_dir, run_branch)
    if path.exists():
        _refuse(path, run_branch, _read_holder(path), break_stale)


@contextmanager
def run_lock(
    state_dir: Path, run_branch: str, run_id: str, *, break_stale: bool = False
) -> Iterator[LockHolder | None]:
    """Hold the lock of a run branch while the block runs.

    Yield None, or the holder of a stale lock that `break_stale` removed. Raise
    LockError when a live process holds the lock, or when a stale lock is there
    and `break_stale` is false. The message names the run id and the process id of the holder.
    """
    path = _lock_path(state_dir, run_branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The holder is written to a private file first, and a hard link puts it in place.
    # The link fails if the lock exists, and the lock never shows as a half-written file.
    private = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    private.write_text(json.dumps({"run_id": run_id, "pid": os.getpid()}))
    broken: LockHolder | None = None
    try:
        while True:
            try:
                os.link(private, path)
            except FileExistsError:
                holder = _read_holder(path)
                _refuse(path, run_branch, holder, break_stale)
                path.unlink()
                broken = holder
                continue
            break
    finally:
        private.unlink()
    try:
        yield broken
    finally:
        path.unlink(missing_ok=True)
