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

A run that `delegate run --detach` started and that ended before it wrote its
journal failed at start (`FailedStart`): its directory holds a `run.log` with
text and no journal, and no lock names the run and a process that exists. The
rule of the lock is the rule of an active run: a run process takes its lock
before it writes its journal. The log is empty until the run process writes to
it, so a run whose process starts has not failed. A failed start has no run
branch and no start event, so the time of the last change of its log ranks it
among the other runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .engine import EngineError, StateRootError, state_directory
from .journal import newest_run_end, read_new_events
from .lock import LockError, live_holder, live_holder_of_run
from ..ports.vcs import VersionControl

JOURNAL_NAME: str = "journal.jsonl"

# The name of the log file of a detached run, in the directory of the run.
LOG_NAME: str = "run.log"


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
    journals = _all_journals(state)
    if not journals:
        raise RunsError(f"no run found in {repo}: no journal under {state / 'runs'}")
    return journals


def _all_journals(state: Path) -> list[Path]:
    """The journal of each run in the state directory, in the order of the names. The list can be empty."""
    runs = state / "runs"
    return sorted(runs.glob(f"*/{JOURNAL_NAME}")) if runs.is_dir() else []


@dataclass(frozen=True)
class FailedStart:
    """A run that failed at start: its id, its log, the last line of the log that has text, and the time of that log.

    `delegate watch` and `delegate status` with no run id report it in one line when it is the newest run.
    """

    run_id: str
    log: Path
    last_line: str
    changed: datetime

    def line(self) -> str:
        """The one line of a newest run that failed at start, when no run is active."""
        return f"no run is active; the newest run {self.run_id} failed at start: {self.last_line}; log {self.log}"


def _failed_starts(state: Path) -> list[FailedStart]:
    """Each run of the state directory that failed at start, in the order of the names.

    Raise RunsError when a lock file cannot be read.
    """
    runs = state / "runs"
    found: list[FailedStart] = []
    for log in sorted(runs.glob(f"*/{LOG_NAME}")) if runs.is_dir() else []:
        run_dir = log.parent
        if (run_dir / JOURNAL_NAME).exists():
            continue
        try:
            lines = [line.strip() for line in log.read_text(errors="replace").splitlines() if line.strip()]
            changed = datetime.fromtimestamp(log.stat().st_mtime, timezone.utc)
        except FileNotFoundError:
            continue  # the run directory went while this function read it
        if not lines:
            continue  # the run process has not written to its log yet
        try:
            starting = live_holder_of_run(state, run_dir.name)
        except LockError as error:
            raise RunsError(str(error)) from None
        if starting is None:
            found.append(FailedStart(run_dir.name, log, lines[-1], changed))
    return found


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
    """The runs of a repository: the newest run, and the journals of the active runs, oldest first.

    The newest run is its journal, or a `FailedStart` when the newest run failed at start.
    """

    newest: Path | FailedStart
    active: tuple[Path, ...]

    @property
    def chosen(self) -> Path | FailedStart | None:
        """The run of a command with no run id: the one active run, or the newest run when no run is active.

        None when more than one run is active: the command cannot choose.
        """
        if len(self.active) > 1:
            return None
        return self.active[0] if self.active else self.newest


def is_active(journal: Path) -> bool:
    """True when the run of `journal` is active. Raise RunsError when its lock file cannot be read.

    The journal is `<state dir>/runs/<run id>/journal.jsonl`, so its path gives the state directory that holds the lock.
    `delegate status` uses the rule also for the state of a run with a pause request.
    """
    state = journal.parents[2]
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
    """The newest run and the active runs of the repository. A run that failed at start can be the newest run.

    Raise RunsError when `repo` is not a git repository, when the repository has no run, or when a lock file cannot be
    read. Raise JournalError when the journal of a run with a live lock is not valid.
    """
    state = _state(repo, vcs)
    journals = _all_journals(state)
    failed = _failed_starts(state)
    if not journals and not failed:
        raise RunsError(f"no run found in {repo}: no journal under {state / 'runs'}")
    active = tuple(journal for journal in sorted(journals, key=_start_time) if is_active(journal))
    ranked: list[tuple[datetime, Path | FailedStart]] = [(_start_time(journal), journal) for journal in journals]
    ranked += [(start.changed, start) for start in failed]
    return Runs(max(ranked, key=lambda pair: pair[0])[1], active)
