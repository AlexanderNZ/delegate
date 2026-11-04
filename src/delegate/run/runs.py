"""Find the journal of a run in a repository, and the active runs. `status` and `watch` share it.

The engine keeps one directory for each run in the state directory of the
repository. A run that started before #200 is in `<git common dir>/delegate`, and
a newer run is in the default state directory, so the functions here search both.
`journal_of` reads only the names of those directories and the first
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

from .engine import EngineError, StateLocation, StateRootError, locate_state
from .journal import JournalError, newest_run_end, read_new_events
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


def locate(repo: Path, vcs: VersionControl) -> StateLocation:
    """The location of the repository and of its state directories (see `engine.locate_state`), for a command that looks it up one time (#239).

    Raise RunsError when `repo` is not a git repository, or when the state root is not valid.
    """
    try:
        return locate_state(repo, vcs)
    except StateRootError as error:
        raise RunsError(str(error)) from None
    except EngineError as error:
        raise RunsError(f"{repo} is not a git repository: {error}") from None


def _states(repo: Path, vcs: VersionControl, location: StateLocation | None = None) -> list[Path]:
    """Each state directory of the repository that can hold a run, the default one first (#200).

    The second directory is `<git common dir>/delegate`, when it holds runs of the versions before #200. Raise RunsError
    when `repo` is not a git repository. A `location` that the command looked up already gives the directories with
    no lookup.
    """
    return (location or locate(repo, vcs)).run_directories()


def _all_journals(states: list[Path]) -> list[Path]:
    """The journal of each run in the state directories, in the order of the paths. The list can be empty."""
    found: list[Path] = []
    for state in states:
        runs = state / "runs"
        found += sorted(runs.glob(f"*/{JOURNAL_NAME}")) if runs.is_dir() else []
    return found


def _no_run(repo: Path, states: list[Path]) -> RunsError:
    """The error of a repository with no journal; it names `runs` of each state directory."""
    return RunsError(f"no run found in {repo}: no journal under {' or '.join(str(state / 'runs') for state in states)}")


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


def _failed_starts(states: list[Path]) -> list[FailedStart]:
    """Each run of the state directories that failed at start, in the order of the names.

    Raise RunsError when a lock file cannot be read.
    """
    found: list[FailedStart] = []
    logs = [(state, log) for state in states for log in sorted((state / "runs").glob(f"*/{LOG_NAME}"))]
    for state, log in logs:
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


def _full_run_id(states: list[Path], run_id: str) -> str:
    """The full id of the run that `run_id` names: the run with that id, or else the one run whose short id it is.

    The short id is the part of a run id after the last `-`. A run id that names no run comes back as it is, so the
    caller gives its own error for an unknown run. Raise RunsError when more than one run has the short id.
    """
    if any((state / "runs" / run_id).is_dir() for state in states):
        return run_id
    matches = sorted(
        {path.name for state in states if (state / "runs").is_dir() for path in (state / "runs").iterdir()
         if path.is_dir() and path.name.rsplit("-", 1)[-1] == run_id}
    )
    if len(matches) > 1:
        raise RunsError(f"run id {run_id!r} matches more than one run: {', '.join(matches)}; give the full run id")
    return matches[0] if matches else run_id


def run_id_of(repo: Path, run_id: str, vcs: VersionControl, *, location: StateLocation | None = None) -> str:
    """The full id of the run that `run_id` names in the repository: a full run id, or the short id of one run.

    `delegate run --resume` uses it. A run id that names no run comes back as it is. Raise RunsError when `repo` is not
    a git repository, or when more than one run has the short id. `location` is the location that the command looked
    up (see `locate`), or None to look it up here.
    """
    return _full_run_id(_states(repo, vcs, location), run_id)


def journal_of(repo: Path, run_id: str | None, vcs: VersionControl, *, location: StateLocation | None = None) -> Path:
    """The journal file of the run `run_id`, or of the newest run when `run_id` is `None`.

    `run_id` is a full run id, or the short id of one run (`run_id_of`). The run can be in the default state directory
    or in `<git common dir>/delegate` (#200). Raise RunsError when `repo` is not a git repository, when the run is
    unknown, when more than one run has the short id, or when the repository has no run. `location` is the location
    that the command looked up (see `locate`), or None to look it up here.
    """
    states = _states(repo, vcs, location)
    if run_id is not None:
        run_id = _full_run_id(states, run_id)
        for state in states:
            journal = state / "runs" / run_id / JOURNAL_NAME
            if journal.is_file():
                return journal
        raise RunsError(f"run {run_id!r} not found in {repo}: no journal at {states[0] / 'runs' / run_id / JOURNAL_NAME}")
    journals = _all_journals(states)
    if not journals:
        raise _no_run(repo, states)
    return max(journals, key=_start_time)


@dataclass(frozen=True)
class Runs:
    """The runs of a repository: the newest run, and the journals of the active runs, oldest first.

    The newest run is its journal, or a `FailedStart` when the newest run failed at start. `unreadable` holds each
    journal that the listing skipped, with the reason: one bad journal does not stop the listing of the other runs
    (#191). The command that lists the runs names each one on stderr.
    """

    newest: Path | FailedStart
    active: tuple[Path, ...]
    unreadable: tuple[tuple[Path, str], ...] = ()

    def unreadable_lines(self, command: str) -> list[str]:
        """One line for each journal that the listing skipped, for stderr: the command, the journal and the reason."""
        return [f"{command}: skipped the journal {path}: {reason}" for path, reason in self.unreadable]

    @property
    def chosen(self) -> Path | FailedStart | None:
        """The run of a command with no run id: the one active run, or the newest run when no run is active.

        None when more than one run is active: the command cannot choose.
        """
        if len(self.active) > 1:
            return None
        return self.active[0] if self.active else self.newest


def has_live_process(journal: Path) -> bool:
    """True when the lock of the run branch of `journal` names the run and a process that exists.

    Raise RunsError when its lock file cannot be read. Raise JournalError when the first line of the journal is not JSON,
    or when it is not an event.

    The journal is `<state dir>/runs/<run id>/journal.jsonl`, so its path gives the state directory that holds the lock.
    `delegate status` uses the rule for the state of a run with no `run-end`: with no live process, the run is `stopped`.
    """
    state = journal.parents[2]
    with journal.open() as handle:
        line = handle.readline()
    # `_start_time` checked that a complete first line is an event. A journal with no `run-start` names no run branch.
    try:
        first = json.loads(line) if line.endswith("\n") else {}
    except json.JSONDecodeError as error:
        raise JournalError(f"journal {journal}: line 1 is not valid JSON: {error}") from None
    if not isinstance(first, dict):
        raise JournalError(f"journal {journal}: line 1 is not an event")
    branch = first.get("run_branch") if first.get("event") == "run-start" else None
    if branch is None:
        return False
    try:
        holder = live_holder(state, str(branch))
    except LockError as error:
        raise RunsError(str(error)) from None
    return holder is not None and holder.run_id == journal.parent.name


def is_active(journal: Path) -> bool:
    """True when the run of `journal` is active: it has a live process (`has_live_process`) and no `run-end` after its newest `resume`.

    Raise RunsError when its lock file cannot be read.
    Raise JournalError when its first line is not JSON, or when a complete line is not an event.
    `delegate status` uses the rule also for the state of a run with a pause request.
    """
    if not has_live_process(journal):
        return False
    events, _ = read_new_events(journal, 0, 1)
    return newest_run_end(events) is None


def runs_of(repo: Path, vcs: VersionControl, *, location: StateLocation | None = None) -> Runs:
    """The newest run and the active runs of the repository. A run that failed at start can be the newest run.

    A journal that cannot be read is not listed: `Runs.unreadable` names it with the reason, and the other runs are
    listed (#191). Raise RunsError when `repo` is not a git repository, when the repository has no run that can be
    listed, or when a lock file cannot be read. `location` is the location that the command looked up (see `locate`),
    or None to look it up here.
    """
    states = _states(repo, vcs, location)
    journals = _all_journals(states)
    failed = _failed_starts(states)
    if not journals and not failed:
        raise _no_run(repo, states)
    unreadable: list[tuple[Path, str]] = []
    started: list[tuple[datetime, Path]] = []
    for journal in journals:
        try:
            started.append((_start_time(journal), journal))
        except RunsError as error:
            unreadable.append((journal, str(error)))
    started.sort(key=lambda pair: pair[0])
    active: list[Path] = []
    for _, journal in started:
        try:
            if is_active(journal):
                active.append(journal)
        except JournalError as error:
            unreadable.append((journal, str(error)))
    skipped = {path for path, _ in unreadable}
    ranked: list[tuple[datetime, Path | FailedStart]] = [(time, journal) for time, journal in started if journal not in skipped]
    ranked += [(start.changed, start) for start in failed]
    if not ranked:
        raise RunsError(f"no run found in {repo}: " + "; ".join(reason for _, reason in unreadable))
    return Runs(max(ranked, key=lambda pair: pair[0])[1], tuple(active), tuple(unreadable))
