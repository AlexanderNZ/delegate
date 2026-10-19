"""Find the journal of a run in a repository, and the active runs. `status` and `watch` share it.

The engine keeps one directory for each run in the state directory of the
repository. `journal_of` reads only the names of those directories and the first
line of each journal. It needs no adapter and no workflow file.

`runs_of` also finds the active runs. A run is active when its journal has no
`run-end` after its newest `resume` (`journal.newest_run_end`, the rule of
`delegate status`), and the lock of its run branch names the run and a process
that exists (`lock.live_holder`, the lock check of `delegate run` and `delegate
pause`). A run with no `run-end` and no live process stopped: a crash, a kill,
Ctrl-C, or a pause. It is not active.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .engine import EngineError, StateRootError, state_directory
from .journal import newest_run_end, read_new_events
from .lock import LockError, live_holder
from ..ports.vcs import VersionControl

JOURNAL_NAME: str = "journal.jsonl"


class RunsError(Exception):
    """No journal can be found. The message names the repository or the run id."""


def _start_time(journal: Path) -> datetime:
    """The time of the first event of a journal. A journal with no complete first line ranks oldest."""
    with journal.open() as handle:
        line = handle.readline()
    if not line.endswith("\n"):
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(json.loads(line)["time"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise RunsError(f"journal {journal}: the first line is not an event with a time") from None


def _state(repo: Path, vcs: VersionControl) -> Path:
    """The state directory of the repository. Raise RunsError when `repo` is not a git repository."""
    try:
        return state_directory(repo, vcs)[1]
    except StateRootError as error:
        raise RunsError(str(error)) from None
    except EngineError as error:
        raise RunsError(f"{repo} is not a git repository: {error}") from None


def _journals(repo: Path, state: Path) -> list[Path]:
    """The journal of each run in the state directory, in the order of the names. Raise RunsError when there is none."""
    runs = state / "runs"
    journals = sorted(runs.glob(f"*/{JOURNAL_NAME}")) if runs.is_dir() else []
    if not journals:
        raise RunsError(f"no run found in {repo}: no journal under {runs}")
    return journals


def journal_of(repo: Path, run_id: str | None, vcs: VersionControl) -> Path:
    """The journal file of the run `run_id`, or of the newest run when `run_id` is `None`.

    Raise RunsError when `repo` is not a git repository, when the run is
    unknown, or when the repository has no run.
    """
    state = _state(repo, vcs)
    if run_id is not None:
        journal = state / "runs" / run_id / JOURNAL_NAME
        if not journal.is_file():
            raise RunsError(f"run {run_id!r} not found in {repo}: no journal at {journal}")
        return journal
    return max(_journals(repo, state), key=_start_time)


@dataclass(frozen=True)
class Runs:
    """The runs of a repository: the journal of the newest run, and the journals of the active runs, oldest first."""

    newest: Path
    active: tuple[Path, ...]

    @property
    def chosen(self) -> Path | None:
        """The run of a command with no run id: the one active run, or the newest run when no run is active.

        None when more than one run is active: the command cannot choose.
        """
        if len(self.active) > 1:
            return None
        return self.active[0] if self.active else self.newest


def _active(state: Path, journal: Path) -> bool:
    """True when the run of `journal` is active. Raise RunsError when its lock file cannot be read."""
    with journal.open() as handle:
        line = handle.readline()
    # `_start_time` checked that a complete first line is an event. A journal with no `run-start` names no run branch.
    first = json.loads(line) if line.endswith("\n") else {}
    branch = first.get("run_branch") if first.get("event") == "run-start" else None
    if branch is None:
        return False
    try:
        holder = live_holder(state, str(branch))
    except LockError as error:
        raise RunsError(str(error)) from None
    if holder is None or holder.run_id != journal.parent.name:
        return False
    events, _ = read_new_events(journal, 0, 1)
    return newest_run_end(events) is None


def runs_of(repo: Path, vcs: VersionControl) -> Runs:
    """The newest run and the active runs of the repository.

    Raise RunsError when `repo` is not a git repository, when the repository has no run, or when the lock file of a
    run branch cannot be read. Raise JournalError when the journal of a run with a live lock is not valid.
    """
    state = _state(repo, vcs)
    journals = _journals(repo, state)
    active = tuple(journal for journal in sorted(journals, key=_start_time) if _active(state, journal))
    return Runs(max(journals, key=_start_time), active)
