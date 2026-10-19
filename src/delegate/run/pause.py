"""Ask a run to stop at its next step boundary. The `delegate pause` command is in `delegate.cli.pause`.

The request is a file, `pause-request`, in the directory of the run. So a pause
needs no signal, and it works from any shell on the host. The file holds the
time of the request and the process id of the command that wrote it.

The engine reads the file at each step boundary: before a step starts, before a
gate runs, and before a verifier starts (see `engine._pause_point`). It stops
there, so it never cuts off an agent. It writes `pause-requested` and `paused`,
removes the file, and the run process ends with no `run-end`. `delegate run
--resume` goes on from a paused run as from a stopped one.

A run that works now has a live process that holds the lock of its run branch.
A request for any other run is refused and writes no file: a run that ended, a
run that is paused, and a run whose process stopped. A stopped run needs no
pause, and a request that no process reads would pause its next resume.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from ..ports.vcs import VersionControl
from .engine import PAUSE_REQUEST_NAME, EngineError, state_directory
from .journal import JournalError, is_paused, newest_run_end, read_new_events
from .lock import LockError, live_holder
from .runs import RunsError, journal_of


class PauseError(Exception):
    """The run cannot be paused. The message names the run and the reason."""


def pause_requested(journal: Path) -> bool:
    """True when the directory of the journal holds a pause request that the engine did not read yet."""
    return (journal.parent / PAUSE_REQUEST_NAME).is_file()


def request_pause(repo: Path, run_id: str, vcs: VersionControl, now: datetime) -> Path:
    """Write the pause request of the run `run_id` of the repository, and return its path. `now` is the time of the request.

    A second request before the engine reads the first one writes the file again. Raise PauseError, and write
    nothing, when the run is unknown, its journal is not valid, it ended, it is paused, or no live process works on it.
    """
    try:
        journal = journal_of(repo, run_id, vcs)
        events, _ = read_new_events(journal, 0, 1)
    except (RunsError, JournalError) as error:
        raise PauseError(str(error)) from None
    if not events or events[0]["event"] != "run-start":
        raise PauseError(f"journal {journal}: the first event is not 'run-start'")
    resume = f"delegate run --resume {run_id}"
    end = newest_run_end(events)
    if end is not None:
        raise PauseError(f"run {run_id!r} has ended with the result {end['result']!r}; there is no step boundary to pause at")
    if is_paused(events):
        raise PauseError(f"run {run_id!r} is paused already; go on with {resume}")
    try:
        _, state_dir = state_directory(repo, vcs)
        holder = live_holder(state_dir, str(events[0]["run_branch"]))
    except (EngineError, LockError) as error:
        raise PauseError(str(error)) from None
    if holder is None or holder.run_id != run_id:
        raise PauseError(f"run {run_id!r} has no live process: it stopped, so it needs no pause; go on with {resume}")
    path = journal.parent / PAUSE_REQUEST_NAME
    # The request goes to a private file first, and a rename puts it in place, so the engine never reads half a file.
    private = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    private.write_text(json.dumps({"time": now.isoformat(), "pid": os.getpid()}) + "\n")
    os.replace(private, path)
    return path
