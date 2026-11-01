"""Remove what ended runs left behind. The `delegate clean` command is in `delegate.cli.clean` (#168).

A run that ended `built` before the clean-up of the engine existed (#167), or
with `keep-worktrees = true`, keeps its ticket worktrees, its ticket branches,
and the file watchers of its worktrees. A run process that a kill ended during
a verifier also leaves the temporary copy of the verifier and its proof copy.
`clean` removes them for each run that ended `built`, with the rules of
`cleanup.clean_tickets`: it stops the file watchers, removes the ticket
worktrees and their hooks, deletes each ticket branch that the run branch
holds, and removes the leftover verifier copies and proof copies, and the
scratch directories of the agent sessions of the run (#227). With
`merged`, it then deletes the run branch when the base branch holds it. When the
run branch is gone already, the base branch is the holder of the ticket
branches with `merged`, and without it every ticket branch stays.

A run is cleaned only when the newest `run-end` of its journal after its newest
`resume` has the result `built` (`journal.newest_run_end`), and no live process
holds the lock of its run branch. Each other run keeps everything, and its line
gives the reason: it is active, it failed, it is paused, or it stopped. The
clean takes the lock of the run branch while it works, so a resume cannot start
in the middle. When the clean removes a stale lock of the run, the journal gets `lock-broken` with the run id and the
process id of the old holder, as for `delegate run --break-lock` (#207), before the clean changes anything else.

The journal of each cleaned run gets one `cleanup` event after its `run-end`,
with what went, what stayed and why, the copies removed, the scratch directory removed, and the bytes freed.
A run where nothing went gets no event: a second clean adds no line. `watch` and `status` read a
`cleanup` and a `lock-broken` after `run-end` as no continuation of the run (`journal.AFTER_END`).

With `dry_run` nothing changes: no file, no branch, no journal, and no lock.
The lines then say what the command would remove and keep.

`abandon` is the other use of the module (#201): it ends a run that is not built for good. It writes `run-end` with the
result `abandoned` and the reason, and then removes the ticket worktrees, the ticket branches and the hooks directory
whatever they hold, and the scratch directories of the run. It keeps the run branch, unless the caller asks to delete it. A stale lock that it removes is
recorded with `lock-broken` before the `run-end` (#207).

The module starts no process and imports no adapter: the port does the work.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..ports.vcs import VcsError, VersionControl
from .cleanup import Cleanup, TicketTree, clean_tickets, delete_held_branch, remove_empty_directories
from .engine import CRASH_FILE_PREFIX, PAUSE_REQUEST_NAME, SCRATCH_DIRECTORY, EngineError, restore_progress, state_directories
from .events import RUN_END
from .journal import ABANDONED, Journal, JournalError, abandon_note, halt_of, is_paused, newest_run_end, read_events
from .lock import LockError, LockHolder, live_holder, run_lock
from .runs import JOURNAL_NAME, RunsError, journal_of

BUILT: str = "built"

# The prefix of the name of the scratch directory of a verifier, and the name of the copy in it. See `engine._verify_round`.
VERIFY_SCRATCH_PREFIX: str = "delegate-verify-"
VERIFY_COPY_NAME: str = "copy"
PROOF_COPY_NAME: str = "proof"


class CleanError(Exception):
    """The command cannot start: the repository is not a git repository. The message names the cause."""


@dataclass
class RunClean:
    """The outcome for one run: the clean-up, or the reason that the run keeps everything.

    `kept` is None for a run that the command cleaned. `cleanup` is None for a
    run that keeps everything. `errors` holds each failure of the port or of a
    file, which makes the exit code 1.
    """

    run_id: str
    kept: str | None = None
    cleanup: Cleanup | None = None
    copies_removed: list[str] = field(default_factory=list)
    tracebacks_removed: list[str] = field(default_factory=list)
    scratch_removed: list[str] = field(default_factory=list)
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)

    def line(self, dry_run: bool) -> str:
        """The one line of the run: what went, what stayed and why, and the space freed."""
        if self.kept is not None:
            return f"run {self.run_id}: not cleaned: {self.kept}"
        c = self.cleanup or Cleanup()
        verbs = ("would remove", "would delete", "would keep", "would free") if dry_run else ("removed", "deleted", "kept", "freed")
        parts: list[str] = []
        if c.worktrees_removed:
            parts.append(f"{verbs[0]} {_count(c.worktrees_removed, 'worktree')} ({', '.join(c.worktrees_removed)})")
        if c.branches_deleted:
            parts.append(f"{verbs[1]} {_count(c.branches_deleted, 'branch', 'branches')} ({', '.join(c.branches_deleted)})")
        if self.copies_removed:
            parts.append(f"{verbs[0]} {_count(self.copies_removed, 'verifier copy', 'verifier copies')} ({', '.join(self.copies_removed)})")
        if self.tracebacks_removed:
            parts.append(f"{verbs[0]} {_count(self.tracebacks_removed, 'crash traceback')} ({', '.join(self.tracebacks_removed)})")
        kept = [f"worktree {k['worktree']}: {k['reason']}" for k in c.worktrees_kept]
        kept += [f"branch {k['branch']}: {k['reason']}" for k in c.branches_kept]
        if kept:
            parts.append(f"{verbs[2]} " + "; ".join(kept))
        if self.scratch_removed:
            parts.append(f"{verbs[0]} the scratch directory ({', '.join(self.scratch_removed)})")
        if not parts:
            return f"run {self.run_id}: nothing to remove"
        return f"run {self.run_id}: " + "; ".join(parts) + f"; {verbs[3]} {format_size(self.bytes_freed)}"


@dataclass
class CleanResult:
    """The outcome of each run, in the order of the run ids, and the errors that belong to no run."""

    runs: list[RunClean]
    errors: list[str]

    @property
    def ok(self) -> bool:
        """True when the command removed or kept everything as the rules say."""
        return not self.errors and not any(run.errors for run in self.runs)


def clean(repo: Path, run_ids: Iterable[str], ended: bool, merged: bool, dry_run: bool, vcs: VersionControl) -> CleanResult:
    """Clean each run that `run_ids` names, and with `ended` each run of the repository. Return the outcome of each run.

    A run id is a full run id or the short id of one run. A run is in the default state directory or in
    `<git common dir>/delegate` (#200), and `ended` covers both. A run id that names
    no run is an error of the result, and the other runs are cleaned. Raise
    CleanError when `repo` is not a git repository. Before the runs and at the
    end, the port prunes the records of the worktrees whose directory is gone,
    except with `dry_run`.
    """
    try:
        top, states = state_directories(repo, vcs)
    except EngineError as error:
        raise CleanError(f"{repo} is not a git repository: {error}") from None
    errors: list[str] = []
    journals: set[Path] = set()
    for run_id in run_ids:
        try:
            journals.add(journal_of(top, run_id, vcs))
        except RunsError as error:
            errors.append(str(error))
    if ended:
        for state in states:
            runs_dir = state / "runs"
            journals.update(runs_dir.glob(f"*/{JOURNAL_NAME}") if runs_dir.is_dir() else ())
    # Git refuses to delete the branch of a worktree whose directory is gone while it still records that worktree, so
    # the records go first. The prune at the end drops the records of the worktrees that the clean removed.
    _prune(vcs, top, dry_run, errors)
    # The journal of a run is `<state dir>/runs/<run id>/journal.jsonl`, so its path gives the state directory of the run.
    outcomes = [_run(top, journal.parents[2], journal, merged, dry_run, vcs) for journal in sorted(journals)]
    _prune(vcs, top, dry_run, errors)
    return CleanResult(outcomes, errors)


def _prune(vcs: VersionControl, repo: Path, dry_run: bool, errors: list[str]) -> None:
    """Remove the records of the worktrees whose directory is gone, except with `dry_run`. Add a failure to `errors`."""
    if dry_run:
        return
    try:
        vcs.prune_worktrees(repo)
    except VcsError as error:
        errors.append(f"git worktree prune: {error}")


def _run(repo: Path, state: Path, journal: Path, merged: bool, dry_run: bool, vcs: VersionControl) -> RunClean:
    """Clean one run, or give the reason that it keeps everything."""
    run_id = journal.parent.name
    try:
        events = read_events(journal, torn_tail=True)
        if not events or events[0]["event"] != "run-start":
            raise JournalError(f"journal {journal}: the first event is not 'run-start'")
        run_branch = str(events[0]["run_branch"])
        holder = live_holder(state, run_branch)
    except (JournalError, LockError) as error:
        return RunClean(run_id, kept=str(error), errors=[str(error)])
    if holder is not None:
        if holder.run_id == run_id:
            return RunClean(run_id, kept=f"the run is active: process {holder.pid} works on it")
        return RunClean(run_id, kept=f"its run branch {run_branch} is in use by the active run {holder.run_id} (process {holder.pid})")
    if dry_run:
        return _clean_run(repo, state, journal, events, merged, dry_run, vcs)
    try:
        # The lock keeps a resume out while the clean works. A stale lock names no live process, so it goes.
        with run_lock(state, run_branch, run_id, break_stale=True) as broken:
            if broken is not None:
                _record_lock_broken(Journal.reopen(journal)[0], broken)
            events = read_events(journal, torn_tail=True)  # the journal can change before the lock
            return _clean_run(repo, state, journal, events, merged, dry_run, vcs)
    except LockError as error:
        return RunClean(run_id, kept=f"the run is active: {error}")
    except JournalError as error:
        return RunClean(run_id, kept=str(error), errors=[str(error)])


def _clean_run(
    repo: Path, state: Path, journal: Path, events: list[dict[str, object]], merged: bool, dry_run: bool, vcs: VersionControl
) -> RunClean:
    run_id = journal.parent.name
    reason = _not_built(events, run_id)
    if reason is not None:
        return RunClean(run_id, kept=reason)
    start = events[0]
    run_branch, base_branch = str(start["run_branch"]), str(start["base_branch"])
    outcome = RunClean(run_id)
    try:
        holder, holder_name = run_branch, "the run branch"
        if merged and not vcs.branch_exists(repo, run_branch):
            holder, holder_name = base_branch, "the base branch"
        trees = _ticket_trees(events, state, run_id)
        sizes = {str(tree.worktree): _size(tree.worktree) for tree in trees if tree.worktree.is_dir()}
        cleanup = clean_tickets(vcs, repo, trees, holder, holder_name, dry_run=dry_run, discard_failed=True)
        if merged and vcs.branch_exists(repo, run_branch):
            try:
                if delete_held_branch(vcs, repo, run_branch, base_branch, dry_run=dry_run):
                    cleanup.branches_deleted.append(run_branch)
                else:
                    cleanup.keep_branch(run_branch, f"the base branch {base_branch!r} does not hold its commits")
            except VcsError as error:
                cleanup.keep_branch(run_branch, str(error), error=True)
    except VcsError as error:
        outcome.errors.append(str(error))
        outcome.kept = str(error)
        return outcome
    outcome.cleanup = cleanup
    outcome.errors += cleanup.errors
    outcome.bytes_freed = sum(sizes.get(path, 0) for path in cleanup.worktrees_removed)
    _remove_copies(outcome, events, dry_run, vcs)
    for trace in _crash_files(events, journal.parent):
        size = _size(trace)
        if not dry_run:
            try:
                trace.unlink()
            except OSError as error:
                outcome.errors.append(f"crash traceback {trace}: it could not be removed: {error}")
                continue
        outcome.tracebacks_removed.append(str(trace))
        outcome.bytes_freed += size
    _remove_scratch(outcome, journal.parent, dry_run)
    if dry_run:
        return outcome
    remove_empty_directories(state / "worktrees" / run_id, state / "hooks" / run_id)
    # A run where nothing went gets no event, so a second clean of the same run adds no line to its journal.
    if outcome.copies_removed or outcome.tracebacks_removed or outcome.scratch_removed or cleanup.worktrees_removed or cleanup.branches_deleted:
        handle, _ = Journal.reopen(journal)
        handle.append(
            "cleanup", **cleanup.fields(), copies_removed=outcome.copies_removed,
            tracebacks_removed=outcome.tracebacks_removed, scratch_removed=outcome.scratch_removed, bytes_freed=outcome.bytes_freed,
        )
    return outcome


def _remove_copies(outcome: RunClean, events: list[dict[str, object]], dry_run: bool, vcs: VersionControl) -> None:
    """Remove the leftover verifier copies and proof copies of the run (`_leftover_copies`), and add each to `outcome`. With `dry_run`, remove none."""
    for copy in _leftover_copies(events):
        size = _size(copy)
        if not dry_run:
            vcs.stop_file_watcher(copy)
            shutil.rmtree(copy, ignore_errors=True)
            if copy.exists():
                outcome.errors.append(f"copy {copy}: it could not be removed")
                continue
            remove_empty_directories(copy.parent)
        outcome.copies_removed.append(str(copy))
        outcome.bytes_freed += size


def _remove_scratch(outcome: RunClean, run_directory: Path, dry_run: bool) -> None:
    """Remove the scratch directory of the run, `<run dir>/scratch`, which holds the scratch directory of each agent session (#227).

    Add it to `outcome` with its size. The leftover copies go first (`_remove_copies`), so the file watcher of a copy in
    it is stopped. With `dry_run`, remove nothing. A run with no scratch directory adds nothing.
    """
    scratch = run_directory / SCRATCH_DIRECTORY
    if not scratch.is_dir():
        return
    size = _size(scratch)
    if not dry_run:
        shutil.rmtree(scratch, ignore_errors=True)
        if scratch.exists():
            outcome.errors.append(f"scratch directory {scratch}: it could not be removed")
            return
    outcome.scratch_removed.append(str(scratch))
    outcome.bytes_freed += size


def _not_built(events: list[dict[str, object]], run_id: str) -> str | None:
    """The reason that a run with no live process is not cleaned, or None for a run that ended `built`."""
    end = newest_run_end(events)
    if end is None:
        if is_paused(events):
            return f"the run is paused; go on with delegate run --resume {run_id}"
        if halt_of(events) is not None:
            return f"the run is halted; go on with delegate run --resume {run_id}"
        return f"the run stopped with no run-end, and no process works on it; go on with delegate run --resume {run_id}"
    if end["result"] == ABANDONED:
        return f"the run was {abandon_note(end)}; delegate clean --abandon removed what it left"
    if end["result"] != BUILT:
        return f"the run ended {end['result']}; its worktrees and branches stay for a retry"
    return None


def _ticket_trees(events: list[dict[str, object]], state: Path, run_id: str) -> list[TicketTree]:
    """The worktree, the branch and the hooks directory of each ticket that started a step, in the order of the first start.

    A branch that a retry with a new text kept (`failed_branch` of a `retry` event, #204) follows them: it has no worktree.
    """
    trees: dict[str, TicketTree] = {}
    kept: list[TicketTree] = []
    for event in events:
        if event["event"] == "step-start":
            ticket = str(event["ticket"])
            trees[ticket] = TicketTree(Path(str(event["worktree"])), str(event["branch"]), state / "hooks" / run_id / ticket)
        elif event["event"] == "retry" and event.get("failed_branch"):
            ticket = str(event["ticket"])
            # No directory has this path: the engine removed the worktree of the failed step before it kept the branch.
            gone = state / "worktrees" / run_id / f"{ticket}-failed-{len(kept) + 1}"
            kept.append(TicketTree(gone, str(event["failed_branch"]), state / "hooks" / run_id / ticket, failed=True))
    return [*trees.values(), *kept]


def _leftover_copies(events: list[dict[str, object]]) -> list[Path]:
    """The verifier copies and proof copies of the run that are still on the disk.

    Only a path of the shape that the engine makes counts: `copy` or `proof` in a
    directory whose name starts with `delegate-verify-`.
    """
    found: list[Path] = []
    for event in events:
        if event["event"] != "verify-start" or "copy" not in event:
            continue
        copy = Path(str(event["copy"]))
        if copy.name != VERIFY_COPY_NAME or not copy.parent.name.startswith(VERIFY_SCRATCH_PREFIX):
            continue
        for path in (copy, copy.parent / PROOF_COPY_NAME):
            if path.is_dir() and path not in found:
                found.append(path)
    return found


def _crash_files(events: list[dict[str, object]], run_directory: Path) -> list[Path]:
    """The files of a crash traceback (#194) that the `step-end` events name and that are still on the disk.

    Only a file of the shape that the engine makes counts: `crash-<number>.txt` in the directory of the run.
    """
    found: list[Path] = []
    for event in events:
        if event["event"] != "step-end" or "traceback" not in event:
            continue
        trace = Path(str(event["traceback"]))
        shaped = trace.name.startswith(CRASH_FILE_PREFIX) and trace.suffix == ".txt"
        if shaped and trace.parent == run_directory and trace.is_file() and trace not in found:
            found.append(trace)
    return found


def _size(path: Path) -> int:
    """The bytes on the disk of the files under `path`. A link counts as itself. A file that goes while the walk reads counts as 0."""
    total = 0
    for root, dirs, files in os.walk(path):
        for name in [*dirs, *files]:
            try:
                info = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            total += getattr(info, "st_blocks", 0) * 512 or info.st_size
    return total


def _count(items: list[str], one: str, many: str | None = None) -> str:
    return f"1 {one}" if len(items) == 1 else f"{len(items)} {many or one + 's'}"


def format_size(size: int) -> str:
    """A number of bytes for a person to read: `0 B`, `512 B`, `12.3 KB`, `886.0 MB`, in units of 1000."""
    if size < 1000:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1000
        if value < 1000 or unit == "TB":
            break
    return f"{value:.1f} {unit}"


class AbandonError(Exception):
    """The run cannot be abandoned: it is unknown, it has a live process, or it ended built. The message names the cause and the command to use."""


@dataclass
class Abandoned:
    """The outcome of an abandon: the run branch, the reason, and what the command removed.

    `already` is true when the run was abandoned before, so the command wrote no `run-end`. `run.errors` holds each
    failure of the port or of a file, which makes the exit code 1.
    """

    run: RunClean
    run_branch: str
    reason: str
    already: bool
    run_branch_deleted: bool = False

    def lines(self) -> list[str]:
        """The lines of the command: the decision, what went and what stayed, and the run branch with what happened to it."""
        said = f"run {self.run.run_id}: abandoned already" if self.already else f"run {self.run.run_id}: abandoned: {self.reason}"
        if self.run_branch_deleted:
            branch = f"run branch {self.run_branch}: deleted"
        else:
            branch = f"run branch {self.run_branch}: kept; --delete-run-branch deletes it"
        return [said, self.run.line(False), branch]


def abandon(repo: Path, run_id: str, reason: str, delete_run_branch: bool, vcs: VersionControl) -> Abandoned:
    """End the run `run_id` for good (#201): write `run-end` with the result `abandoned` and the reason, then remove what the run left.

    The run is paused, halted, failed, or stopped with no `run-end`, and no live process works on it. The command
    removes the worktree, the branch and the hooks directory of each ticket that started a step, whatever the worktree
    holds and whether the run branch holds the branch, the leftover verifier copies, and the scratch directories of the run. It keeps the run branch, or
    with `delete_run_branch` deletes it. The journal then gets a `cleanup` event, as for `clean`. A run that was
    abandoned before gets no new `run-end`: the command only removes what is left, so a failed removal can be tried again.

    Raise AbandonError, and change nothing, when the run is unknown, its journal cannot be read, a live process works on
    it (the message names `delegate stop`), or it ended `built` (the message names `delegate clean`). A failure of the
    port while it removes is an error of the result, and the other items are still removed.
    """
    try:
        top, _ = state_directories(repo, vcs)
        journal = journal_of(top, run_id, vcs)
    except EngineError as error:
        raise AbandonError(f"{repo} is not a git repository: {error}") from None
    except RunsError as error:
        raise AbandonError(str(error)) from None
    state, run_id = journal.parents[2], journal.parent.name  # `run_id` can be a short id; the lock and the messages name the full id
    try:
        events = read_events(journal, torn_tail=True)
        if not events or events[0]["event"] != "run-start":
            raise JournalError(f"journal {journal}: the first event is not 'run-start'")
        run_branch = str(events[0]["run_branch"])
        holder = live_holder(state, run_branch)
    except (JournalError, LockError) as error:
        raise AbandonError(str(error)) from None
    if holder is not None:
        if holder.run_id == run_id:
            raise AbandonError(f"run {run_id} has a live process, {holder.pid}; stop it with delegate stop {run_id}, then abandon it")
        raise AbandonError(
            f"the run branch {run_branch} of run {run_id} is in use by the active run {holder.run_id} (process {holder.pid}); "
            f"stop that run with delegate stop {holder.run_id}, then abandon this one"
        )
    try:
        # The lock keeps a resume out while the command works. A stale lock names no live process, so it goes.
        with run_lock(state, run_branch, run_id, break_stale=True) as broken:
            return _abandon_run(top, state, journal, reason, delete_run_branch, vcs, broken)
    except LockError as error:
        raise AbandonError(f"run {run_id} has a live process: {error}; stop it with delegate stop {run_id}, then abandon it") from None
    except JournalError as error:
        raise AbandonError(str(error)) from None


def _record_lock_broken(handle: Journal, broken: LockHolder) -> None:
    """Append `lock-broken` with the run id and the process id of the old holder, as `delegate run --break-lock` does (#207)."""
    handle.append("lock-broken", run_id=broken.run_id, pid=broken.pid)


def _abandon_run(
    repo: Path, state: Path, journal: Path, reason: str, delete_run_branch: bool, vcs: VersionControl, broken: LockHolder | None = None
) -> Abandoned:
    """Do the abandon of one run, with its lock held. The journal can have changed since the first read, so it is read again.

    `broken` is the holder of the stale lock that the command removed. The journal records it before anything else, so before `run-end`.
    """
    run_id = journal.parent.name
    handle, events = Journal.reopen(journal)
    if broken is not None:
        _record_lock_broken(handle, broken)
    start = events[0]
    run_branch = str(start["run_branch"])
    end = newest_run_end(events)
    if end is not None and end["result"] == BUILT:
        raise AbandonError(f"run {run_id} ended built; it has nothing to abandon, and delegate clean {run_id} removes what it left")
    already = end is not None and end["result"] == ABANDONED
    if not already:
        progress = restore_progress(events, run_id, retrying=True)
        handle.append(RUN_END, result=ABANDONED, built=progress.built, failed=progress.failed, skipped=list(progress.skipped), reason=reason)
    # A request that nothing reads goes: the run has ended.
    (journal.parent / PAUSE_REQUEST_NAME).unlink(missing_ok=True)
    outcome = RunClean(run_id, cleanup=Cleanup())
    errors = outcome.errors
    _prune(vcs, repo, False, errors)
    trees = _ticket_trees(events, state, run_id)
    sizes = {str(tree.worktree): _size(tree.worktree) for tree in trees if tree.worktree.is_dir()}
    cleanup = _discard_tickets(vcs, repo, trees)
    outcome.cleanup = cleanup
    errors += cleanup.errors
    outcome.bytes_freed = sum(sizes.get(path, 0) for path in cleanup.worktrees_removed)
    _remove_copies(outcome, events, False, vcs)
    _remove_scratch(outcome, journal.parent, False)
    result = Abandoned(outcome, run_branch, reason, already)
    if delete_run_branch:
        try:
            if vcs.branch_exists(repo, run_branch):
                vcs.discard_branch(repo, run_branch)
                cleanup.branches_deleted.append(run_branch)
            result.run_branch_deleted = True
        except VcsError as error:
            errors.append(f"branch {run_branch}: {error}")
    remove_empty_directories(state / "worktrees" / run_id, state / "hooks" / run_id)
    _prune(vcs, repo, False, errors)
    if outcome.copies_removed or outcome.scratch_removed or cleanup.worktrees_removed or cleanup.branches_deleted:
        handle.append(
            "cleanup", **cleanup.fields(), copies_removed=outcome.copies_removed, tracebacks_removed=[],
            scratch_removed=outcome.scratch_removed, bytes_freed=outcome.bytes_freed,
        )
    return result


def _discard_tickets(vcs: VersionControl, repo: Path, trees: list[TicketTree]) -> Cleanup:
    """Remove the worktree, the hooks directory and the branch of each ticket, whatever they hold. A failure keeps that item only.

    A worktree whose directory is gone is gone already, and its branch still goes. A worktree that cannot go keeps its branch.
    """
    result = Cleanup()
    for tree in trees:
        if tree.worktree.is_dir():
            try:
                vcs.remove_worktree(repo, tree.worktree)
            except VcsError as error:
                result.worktrees_kept.append({"worktree": str(tree.worktree), "reason": str(error)})
                result.errors.append(f"worktree {tree.worktree}: {error}")
                result.keep_branch(tree.branch, "its worktree is kept")
                continue
            result.worktrees_removed.append(str(tree.worktree))
        shutil.rmtree(tree.hooks, ignore_errors=True)
        try:
            if vcs.branch_exists(repo, tree.branch):
                vcs.discard_branch(repo, tree.branch)
                result.branches_deleted.append(tree.branch)
        except VcsError as error:
            result.keep_branch(tree.branch, str(error), error=True)
    return result
