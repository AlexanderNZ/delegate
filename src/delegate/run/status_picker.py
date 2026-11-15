"""The links of the plan of a run, and the picker of an active run, for `delegate status` and `delegate watch` (#335).

`plan_links` gives the rail and the `after` cell of each ticket of the newest plan (#143). When more than one run is active (see
`delegate.run.runs`), `delegate status` and `delegate watch` with no run id show the run picker of frame 2b (#118): `active_runs`
reads each active run, newest first, `picker_lines` draws the table, and `choices_line` gives the one line that names each run.
The snapshot is in `delegate.run.status`, and the table of the live view, which uses the links, is in `delegate.run.status_table`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import output
from .journal import activity_files, last_change, plan_ids, read_new_events
from .status import liveness, newest_blockers, seconds_between, snapshot_of, time_of
from .status_model import RUNNING, Snapshot

@dataclass(frozen=True)
class PlanLink:
    """The link of one ticket of the newest plan to the rows above it in the ticket table (#143).

    `blockers` are the blockers of the ticket that are in the plan, in the order of the plan record. `rail` is true when
    the one blocker of the ticket is the ticket directly above it in plan order: the rail joins the two rows. `after`
    gives the blockers that the `after` column names: none when the rail joins the rows, and else each blocker, so a
    join (two blockers or more) names each one.
    """

    id: str
    blockers: tuple[str, ...]
    rail: bool

    @property
    def after(self) -> tuple[str, ...]:
        return () if self.rail else self.blockers


def plan_links(events: list[dict[str, object]]) -> list[PlanLink]:
    """The link of each ticket of the newest plan of a run, in plan order: its blockers, and whether the rail joins it to
    the row above.

    The blockers come from the `plan` field of the newest `plan-change`, or else of `run-start` (#107). A blocker that is
    not in the plan does not count. A journal of an earlier version of the engine records no plan: no ticket has a
    blocker, so the table has no rail and no `after` cell.
    """
    blockers = newest_blockers(events) or {}
    ids = plan_ids(events)
    known = set(ids)
    links = []
    for index, ticket in enumerate(ids):
        own = tuple(blocker for blocker in blockers.get(ticket, []) if blocker in known)
        links.append(PlanLink(ticket, own, index > 0 and own == (ids[index - 1],)))
    return links


@dataclass(frozen=True)
class ActiveRun:
    """One active run, for the run picker (frame 2b, #118): its journal, its snapshot with no tokens, its run branch,
    the time of its `run-start` event, and the seconds since its last activity.

    The last activity is the newest change of the journal, of the event streams, of the gate logs and of the prepare
    logs, as for the stall check of `delegate watch`.
    """

    journal: Path
    snap: Snapshot
    run_branch: str
    started: datetime
    idle_seconds: int


def active_runs(journals: Sequence[Path], now: datetime) -> list[ActiveRun]:
    """Each active run of `journals`, the newest run first. `now` is the time of the clock, in UTC.

    The picker numbers the runs in this order, so 1 is the newest run. Raise JournalError when a journal is not valid.
    """
    found = []
    for journal in journals:
        events, _ = read_new_events(journal, 0, 1)
        # The picker needs no tokens, so the snapshot reads no event stream.
        snap = snapshot_of(events, journal, lambda adapter: None, now)
        changed = datetime.fromtimestamp(last_change(journal, activity_files(events)), now.tzinfo)
        found.append(ActiveRun(journal, snap, str(events[0]["run_branch"]), time_of(events[0]), seconds_between(changed, now)))
    return sorted(found, key=lambda run: run.started, reverse=True)


# The columns of the run picker, in order, and the columns of numbers, which align right.
_PICKER_HEADER: tuple[str, ...] = ("#", "run", "run branch", "mode", "since", "progress", "now")
_PICKER_NUMBERS: frozenset[int] = frozenset({0})


def picker_lines(runs: Sequence[ActiveRun], now: datetime) -> list[str]:
    """The lines of the run picker: `<n> runs are active in this repository`, and a table with one row for each run.

    The spec is `design/docs/components.md` (Run picker). The rows keep the
    order of `runs`. A row gives the number to choose, the full run id with its short id bold, the run branch, the mode,
    the local start time (with the day of the week when the run did not start today), the progress, and what runs now
    with the time since the last activity, as `active 2s ago` (`liveness`). `now` is the time of the clock, in UTC.
    """
    glyph = output.glyphs()
    dot = f" {glyph.pending} "
    today = now.astimezone().date()
    rows = [[output.styled("dim", name) for name in _PICKER_HEADER]]
    for number, run in enumerate(runs, start=1):
        snap = run.snap
        prefix, _, short = snap.run_id.rpartition("-")
        started = run.started.astimezone()
        running = [f"{t.id} {t.phase}" if t.phase else t.id for t in snap.tickets if t.state == RUNNING]
        if snap.phase:
            running.append(f"chain {snap.phase}")
        doing = output.styled("now", f"{glyph.running} {', '.join(running)}") if running else output.styled("dim", "no ticket running")
        if snap.state != RUNNING:
            doing = output.styled("warn", snap.state) + dot + doing
        rows.append([
            output.styled("dim", str(number)),
            prefix + ("-" if prefix else "") + output.styled("bold", short),
            run.run_branch,
            snap.mode,
            output.styled("dim", started.strftime("%H:%M" if started.date() == today else "%a %H:%M")),
            f"{snap.tickets_ended}/{snap.tickets_total} ended{dot}{snap.percent}%",
            doing + dot + output.styled(*liveness(run.idle_seconds, None, None)),
        ])
    table = [line.rstrip(" ") for line in output.columns(rows, right=_PICKER_NUMBERS)]
    return [f"{len(runs)} runs are active in this repository", *table]


def choices_line(runs: Sequence[ActiveRun]) -> str:
    """The one line that names each active run, in the order of `runs`, for a reader that cannot answer a prompt.

    The copy deck gives it: `<n> runs are active in this repository; give a run id: <id> (<branch>, <state>), …`.
    """
    named = ", ".join(f"{run.snap.run_id} ({run.run_branch}, {run.snap.state})" for run in runs)
    return f"{len(runs)} runs are active in this repository; give a run id: {named}"
