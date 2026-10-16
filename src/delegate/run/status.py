"""The state of each ticket of a run, for `delegate status` (the command is in `delegate.cli.status`).

The snapshot (`snapshot`) reads the journal of the run and the event streams
that the journal names, through a reader that the command gives. The reader is
the `activity` method of the harness adapter of the run, so this module never
imports an adapter. The snapshot gives the progress, the ETA, the tokens, and
the activity of each agent. When the command has no reader for the adapter of
the run, the snapshot has the progress and the ETA, and no tokens and no
activity. A stream that the reader cannot read gives no tokens and no activity
for its agent, and is not an error. It needs no workflow file, and no tool
beyond git, which finds the state directory.

The report (`report`) gives the lines of `delegate status`: the state, the
verdict and the branch of each ticket from the journal, with the progress of
the snapshot. `view_lines` draws the snapshot as one line for the run and one
for each ticket.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..ports.harness import Activity
from ..ports.vcs import VersionControl
from . import domain
from .journal import JournalError, read_new_events
from .runs import journal_of

# The states that a ticket shows. A ticket with no event is pending, a ticket
# with `step-start` and no `step-end` is running, and the rest follow `step-end`
# and `skip`.
PENDING: str = "pending"
RUNNING: str = "running"
BUILT: str = "built"
FAILED: str = "failed"
SKIPPED: str = "skipped"

# What the line of a ticket shows for a value that the journal does not hold.
NONE: str = "-"


def render(events: list[dict[str, object]], journal: Path, snap: Snapshot) -> list[str]:
    """The lines of the report for a run, from its journal events and its snapshot."""
    start = events[0]
    if start["event"] != "run-start":
        raise JournalError(f"journal {journal}: the first event is {start['event']!r}, not 'run-start'")
    end = next((e for e in reversed(events) if e["event"] == "run-end"), None)
    lines = [
        f"run {start['run_id']}: {end['result'] if end else RUNNING} (mode {start['mode']}, adapter {start['adapter']})",
        f"progress: {_run_progress(snap)}", f"journal {journal}",
    ]
    progress = {t.id: t for t in snap.tickets}
    tickets = [str(t) for t in start["tickets"]]  # type: ignore[union-attr]
    for ticket in tickets:
        own = [e for e in events if e.get("ticket") == ticket]
        state, reason = PENDING, None
        for event in own:
            kind = event["event"]
            if kind == "step-start":
                state = RUNNING
            elif kind == "step-end":
                state, reason = str(event["state"]), event["reason"]
            elif kind == "skip":
                state, reason = SKIPPED, event["reason"]
        # The verdict of a chain (`economy` mode) covers each ticket in its `tickets` field, not only the ticket it names.
        verdicts = [
            str(e["verdict"]) for e in events
            if e["event"] == "verdict" and (e.get("ticket") == ticket or ticket in e.get("tickets", []))  # type: ignore[operator]
        ]
        branch = next((str(e["branch"]) for e in own if e["event"] == "step-start"), NONE)
        last = str(own[-1]["time"]) if own else NONE
        lines.append(f"ticket {ticket}: state {state}, verdict {verdicts[-1] if verdicts else NONE}, branch {branch}, last event {last}")
        if branch != NONE:  # a ticket with a step has a time used
            found = progress[ticket]
            lines.append("  progress: " + (f"{found.phase}, " if found.phase else "") + _ticket_progress(found))
        if reason:
            lines.append(f"  reason: {reason}")
    return lines


def report(repo: Path, run_id: str | None, vcs: VersionControl, reader_of: ReaderOf, now: datetime) -> list[str]:
    """The lines of the report for a run of the repository: the run with that id, or the newest run when `run_id` is None.

    The errors are the errors of `snapshot`.
    """
    journal = journal_of(repo, run_id, vcs)
    events, _ = read_new_events(journal, 0, 1)
    if not events:
        raise JournalError(f"journal {journal} holds no event")
    return render(events, journal, snapshot_of(events, journal, reader_of, now))


# The states of a ticket that ended.
ENDED: tuple[str, ...] = (BUILT, FAILED, SKIPPED)

# The phase that an event of a running ticket starts. `report-validation` starts the gates only when the report is valid.
PHASE_OF_EVENT: dict[str, str] = {
    "step-start": domain.PHASE_SPECIALIST, "continuation": domain.PHASE_SPECIALIST, "report-validation": domain.PHASE_GATES,
    "rebase": domain.PHASE_GATES, "verify-start": domain.PHASE_VERIFIER, "fixup-start": domain.PHASE_FIXUP,
}

# The phase of the agent that an `agent-start` event names, by its `role`.
PHASE_OF_ROLE: dict[str, str] = {"specialist": domain.PHASE_SPECIALIST, "verifier": domain.PHASE_VERIFIER, "fix-up": domain.PHASE_FIXUP}

# The events that end an agent run.
AGENT_RESULTS: frozenset[str] = frozenset({"adapter-result", "fixup-result", "verify-result"})

# A reader of an event stream: the path of the stream gives the activity of the agent.
StreamReader = Callable[[Path], Activity]

# The reader of the streams of a run, from the adapter name of the run. It gives None when the command knows no such
# adapter, or the adapter has no reader: the snapshot then has no tokens and no activity.
ReaderOf = Callable[[str], StreamReader | None]


@dataclass(frozen=True)
class TicketSnapshot:
    """One ticket in a snapshot. `phase` is set only for a running ticket, and `idle_seconds` only while its agent runs.

    `tokens` and `idle_seconds` are None when the snapshot has no reader of the streams. `tokens` is None also when a
    stream of the ticket cannot be read, and `idle_seconds` when the stream of the agent that runs now cannot be read.
    """

    id: str
    state: str
    phase: str | None
    elapsed_seconds: int
    tokens: int | None
    idle_seconds: int | None


@dataclass(frozen=True)
class Snapshot:
    """One snapshot of a run. `time` is the time of the snapshot: now, or the time of `run-end` for a run that ended.

    `tokens` is None when the snapshot has no reader of the streams. Otherwise it is the sum of the tickets that have
    tokens: a ticket with a stream that cannot be read adds nothing.
    """

    run_id: str
    state: str
    time: datetime
    elapsed_seconds: int
    tickets_total: int
    tickets_ended: int
    percent: int
    eta: datetime | None
    tokens: int | None
    tickets: tuple[TicketSnapshot, ...]

    def to_json(self) -> dict[str, object]:
        """The snapshot as the JSON object that `delegate status --json` prints. The reference page of `status` documents it."""
        return {
            "run_id": self.run_id, "state": self.state, "time": self.time.isoformat(), "elapsed_seconds": self.elapsed_seconds,
            "tickets_total": self.tickets_total, "tickets_ended": self.tickets_ended, "percent": self.percent,
            "eta": self.eta.isoformat() if self.eta else None, "tokens": self.tokens,
            "tickets": [
                {"id": t.id, "state": t.state, "phase": t.phase, "elapsed_seconds": t.elapsed_seconds, "tokens": t.tokens, "idle_seconds": t.idle_seconds}
                for t in self.tickets
            ],
        }


def _time(event: dict[str, object]) -> datetime:
    return datetime.fromisoformat(str(event["time"]))


def _seconds(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds()))


def _ticket(
    ticket: str, own: list[dict[str, object]], activity: StreamReader | None, now: datetime, run_ended: bool
) -> tuple[TicketSnapshot, int | None]:
    """The snapshot of one ticket from its own events, and the seconds of its step when it ended after one.

    With no reader (`activity` is None), the ticket has no tokens and no idle time, and its streams are not read.

    An agent runs from its `agent-start` until its result event, a `step-end` (a crash), or the end of the run. The
    verifier of an `economy` chain starts after its ticket ended, so a ticket that ended can have an agent that runs.
    """
    state, phase = PENDING, None
    started = ended = None
    streams: list[str] = []
    live: dict[str, object] | None = None  # the `agent-start` of the agent that runs now
    for event in own:
        kind = str(event["event"])
        if kind == "step-start":
            state, started = RUNNING, started or _time(event)
        elif kind == "step-end":
            state, ended, live = str(event["state"]), ended or _time(event), None
        elif kind == "skip":
            state = SKIPPED
        if kind == "agent-start":
            phase, live = PHASE_OF_ROLE[str(event["role"])], event
        elif kind in AGENT_RESULTS:
            live = None
        elif kind in PHASE_OF_EVENT and (kind != "report-validation" or event["valid"]):
            phase = PHASE_OF_EVENT[kind]
        elif kind in ("gate-result", "gate-skip") and event["phase"] != "fixup":
            phase = domain.PHASE_GATES
        if "event_stream" in event and str(event["event_stream"]) not in streams:
            streams.append(str(event["event_stream"]))
    if state != RUNNING:
        phase = None
    if run_ended:
        live = None
    elapsed = _seconds(started, ended or now) if started else 0
    duration = elapsed if started and ended else None
    if activity is None:
        return TicketSnapshot(ticket, state, phase, elapsed, None, None), duration
    tokens: int | None = 0
    idle = None
    for stream in streams:
        is_live = live is not None and stream == str(live["event_stream"])
        found = _read(activity, Path(stream), is_live)
        if found is None:
            tokens = None  # a count without this agent is not the count of the ticket
            continue
        if tokens is not None:
            tokens += found.tokens
        if is_live:
            idle = _seconds(found.last_event or _time(live), now)  # type: ignore[arg-type]
    return TicketSnapshot(ticket, state, phase, elapsed, tokens, idle), duration


def _read(activity: StreamReader, stream: Path, is_live: bool) -> Activity | None:
    """The activity in a stream, or None when the stream cannot be read.

    The stream of the agent that runs now (`is_live`) can be missing before the harness opens it: it then has no
    activity yet. Any other failure is a stream that cannot be read: a missing stream of an agent that ended, a parent
    directory that the process cannot search, a file that it cannot read, a directory, not text, not an event stream of
    the harness, or an event that the reader cannot use. The status only shows data, so it then shows no tokens and no
    activity for that agent, and does not fail. `delegate run` keeps the errors of the reader.
    """
    try:
        if is_live:
            try:
                stream.stat()
            except (FileNotFoundError, NotADirectoryError):
                return Activity(0, None)  # the harness has not opened the stream yet
        return activity(stream)
    except Exception:  # noqa: BLE001 - any failure to read one stream is no data for that agent
        return None


def snapshot_of(events: list[dict[str, object]], journal: Path, reader_of: ReaderOf, now: datetime) -> Snapshot:
    """The snapshot of a run from its journal events. `now` is the time of the clock, in UTC.

    The reader of the streams comes from `reader_of` once. When it gives None, the snapshot has no tokens and no activity.
    """
    if not events or events[0]["event"] != "run-start":
        raise JournalError(f"journal {journal}: the first event is not 'run-start'")
    start = events[0]
    end = next((e for e in reversed(events) if e["event"] == "run-end"), None)
    if end is not None:
        now = _time(end)  # the clock of a run that ended stops at its end
    now = now.replace(microsecond=0)
    activity = reader_of(str(start["adapter"]))
    tickets: list[TicketSnapshot] = []
    durations: list[float] = []
    for ticket in [str(t) for t in start["tickets"]]:  # type: ignore[union-attr]
        found, duration = _ticket(ticket, [e for e in events if e.get("ticket") == ticket], activity, now, end is not None)
        tickets.append(found)
        if duration is not None:
            durations.append(duration)
    ended = sum(1 for t in tickets if t.state in ENDED)
    running = [t for t in tickets if t.state == RUNNING]
    to_end = domain.seconds_to_end(durations, len(tickets) - ended, sum(t.elapsed_seconds for t in running))
    return Snapshot(
        run_id=str(start["run_id"]), state=str(end["result"]) if end else RUNNING, time=now,
        elapsed_seconds=_seconds(_time(start), now), tickets_total=len(tickets), tickets_ended=ended,
        percent=domain.percent_complete(ended, [t.phase for t in running if t.phase], len(tickets)),
        eta=None if to_end is None else now + timedelta(seconds=to_end), tokens=None if activity is None else sum(t.tokens or 0 for t in tickets), tickets=tuple(tickets),
    )


def snapshot(repo: Path, run_id: str | None, vcs: VersionControl, reader_of: ReaderOf, now: datetime) -> Snapshot:
    """The snapshot of a run of the repository: the run with that id, or the newest run when `run_id` is None.

    A run in progress appends to its journal, so a last line with no newline is
    left for the next snapshot. Raise RunsError when there is no such run, and
    JournalError when its journal is not valid. A stream that cannot be read is
    not an error: the ticket of that stream has no tokens, and no activity when
    its agent runs now. The stream of an agent that runs now can be missing: the
    harness has not opened it yet, so the agent has 0 tokens.
    """
    journal = journal_of(repo, run_id, vcs)
    events, _ = read_new_events(journal, 0, 1)
    return snapshot_of(events, journal, reader_of, now)


def _duration(seconds: int) -> str:
    """A duration in a short form: `45s`, `12m05s`, `2h03m`, or `3d04h`."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    return f"{seconds // 86400}d{seconds % 86400 // 3600:02d}h"


def _tokens(count: int) -> str:
    """A token count in a short form: `950`, `26k`, or `1.2M`."""
    if count < 1000:
        return str(count)
    if count < 1_000_000:
        return f"{count / 1000:.0f}k"
    return f"{count / 1_000_000:.1f}M"


def _run_progress(snap: Snapshot) -> str:
    """The progress of the run: the percentage, the tickets that ended, the time used, the ETA, and the tokens.

    The ETA shows only while the run runs: in the time from now, or `-` before the first ticket ends. The tokens show
    only when the snapshot has them.
    """
    eta = _duration(_seconds(snap.time, snap.eta)) if snap.eta else "-"
    parts = [f"{snap.percent}%", f"{snap.tickets_ended} of {snap.tickets_total} ended", f"{_duration(snap.elapsed_seconds)} used"]
    if snap.state == RUNNING:
        parts.append(f"ETA {eta}")
    if snap.tokens is not None:
        parts.append(f"{_tokens(snap.tokens)} tokens")
    return ", ".join(parts)


def _ticket_progress(ticket: TicketSnapshot) -> str:
    """The progress of a ticket: the time used, the tokens when the snapshot has them, and the time since the last activity."""
    parts = [f"{_duration(ticket.elapsed_seconds)} used"]
    if ticket.tokens is not None:
        parts.append(f"{_tokens(ticket.tokens)} tokens")
    if ticket.idle_seconds is not None:
        parts.append(f"last activity {_duration(ticket.idle_seconds)} ago")
    return ", ".join(parts)


def view_lines(snap: Snapshot, width: int) -> list[str]:
    """The lines of the terminal view of a snapshot: one for the run, then one for each ticket, each cut to `width`.

    The view names only the run id, the ticket ids, the states, the phases, and the numbers. It shows no path and no tool call.
    """
    lines = [f"run {snap.run_id} {snap.state} {_run_progress(snap)}"]
    for ticket in snap.tickets:
        lines.append(f"  {ticket.id} {ticket.state}" + (f" {ticket.phase}" if ticket.phase else "") + f", {_ticket_progress(ticket)}")
    return [line[: max(width, 1)] for line in lines]
