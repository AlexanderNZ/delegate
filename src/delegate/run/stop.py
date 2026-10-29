"""Stop a run now. The `delegate stop` command is in `delegate.cli.stop`.

`delegate pause` stops a run at its next step boundary, and that can be many minutes away. A stop sends SIGTERM to the
process that holds the lock of the run. The engine handles the signal as it handles Ctrl-C (#178): it ends the process
group of its child, writes `interrupted` to the journal, and releases the lock. The run writes no `run-end`, so
`delegate run --resume` goes on from a stopped run.

The stop waits a fixed time for the lock to be free. A run with no live process holder is refused and gets no signal: a
run that ended, a run that is paused, and a run whose process stopped.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from ..ports.vcs import VersionControl
from .engine import EngineError, state_directory
from .journal import JournalError, is_paused, newest_run_end, read_new_events
from .lock import LockError, LockHolder, live_holder
from .runs import RunsError, journal_of

# The seconds between two looks at the lock while the stop waits.
POLL_SECONDS: float = 0.1


class StopError(Exception):
    """The run cannot be stopped. The message names the run and the reason."""


@dataclass(frozen=True)
class StopOutcome:
    """How a stop ended: the full id of the run, the process that got the signal, and whether the lock is free."""

    run_id: str
    pid: int
    released: bool


def stop_run(repo: Path, run_id: str, vcs: VersionControl, wait_seconds: float) -> StopOutcome:
    """Send SIGTERM to the process that holds the lock of the run `run_id` of the repository, and wait up to `wait_seconds` for the lock to be free.

    `run_id` is a full run id, or the short id of one run (`runs.run_id_of`). Raise StopError, and send no signal,
    when the run is unknown, its journal is not valid, it ended, it is paused, or no live process works on it.
    The outcome has `released` false when the process still holds the lock at the end of the wait.
    """
    try:
        journal = journal_of(repo, run_id, vcs)
        events, _ = read_new_events(journal, 0, 1)
    except (RunsError, JournalError) as error:
        raise StopError(str(error)) from None
    run_id = journal.parent.name  # `run_id` can be a short id; the lock and the messages name the full id
    if not events or events[0]["event"] != "run-start":
        raise StopError(f"journal {journal}: the first event is not 'run-start'")
    resume = f"delegate run --resume {run_id}"
    end = newest_run_end(events)
    if end is not None:
        raise StopError(f"run {run_id!r} has ended with the result {end['result']!r}; there is nothing to stop")
    if is_paused(events):
        raise StopError(f"run {run_id!r} is paused already, and no process works on it; go on with {resume}")
    run_branch = str(events[0]["run_branch"])
    try:
        _, state_dir = state_directory(repo, vcs)
        holder = live_holder(state_dir, run_branch)
    except (EngineError, LockError) as error:
        raise StopError(str(error)) from None
    if holder is None or holder.run_id != run_id:
        raise StopError(f"run {run_id!r} has no live process: it stopped already; go on with {resume}")
    try:
        os.kill(holder.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # the process ended between the check and the signal; the wait finds the lock free
    except PermissionError:
        raise StopError(f"run {run_id!r}: process {holder.pid} belongs to another user, so it cannot get a signal") from None
    return StopOutcome(run_id, holder.pid, _wait_for_release(state_dir, run_branch, holder, wait_seconds))


def _wait_for_release(state_dir: Path, run_branch: str, holder: LockHolder, wait_seconds: float) -> bool:
    """True when the lock of the run branch no longer names a live process of the run `holder.run_id`, within `wait_seconds`."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            now = live_holder(state_dir, run_branch)
        except LockError:
            return False
        if now is None or now.run_id != holder.run_id:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)
