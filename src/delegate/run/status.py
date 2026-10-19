"""The state of each ticket of a run, for `delegate status` (the command is in `delegate.cli.status`).

The snapshot (`snapshot`) reads the journal of the run and the event streams
that the journal names, through a reader that the command gives. It also reads
the modification time of the log file of a gate that runs, with no reader. The reader is
the `activity` method of the harness adapter of the run, so this module never
imports an adapter. The snapshot gives the progress, the ETA, the tokens (two
counts: the new tokens and the cache reads), and the activity of each agent. In `economy` mode it also gives the phase of the
verification of the chain (its gates, its verifier, or a fix-up), which counts
in the progress and the ETA as one more unit of the run. When the command has no reader for the adapter of
the run, the snapshot has the progress and the ETA, and no tokens and no
activity. A stream that the reader cannot read gives no tokens and no activity
for its agent, and is not an error. It needs no workflow file, and no tool
beyond git, which finds the state directory. The tickets are the tickets of the
newest plan of the run (see `journal.plan_ids`), so the progress and the ETA
count a ticket that a plan change added. The state of the run is `running`,
`pausing` while a pause request waits in the directory of the run and the run
is active (see `delegate.run.runs`), `paused` from its `paused` event until a
resume, or the result of its `run-end`. A pause request of a run that is not
active stays until the next resume: the run then keeps the state of its
journal, and the snapshot has the note `PAUSE_REQUEST_NOTE`.

The report (`report`) gives the lines of `delegate status`: the state, the
verdict and the branch of each ticket from the journal, with the progress of
the snapshot. `view_lines` draws the snapshot as one line for the run and one
for each ticket.

`active_line` gives the one line of an active run (see `delegate.run.runs`):
`delegate status` and `delegate watch` with no run id list the active runs in
this form when more than one is active.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..ports.harness import Activity
from ..ports.vcs import VersionControl
from . import domain
from .journal import JournalError, activity_files, is_paused, last_change, newest_run_end, plan_ids, read_new_events
from .pause import pause_requested
from .runs import RunsError, is_active, journal_of

# The states that a ticket shows. A ticket with no event is pending, a ticket
# with `step-start` and no `step-end` is running, and the rest follow `step-end`
# and `skip`.
PENDING: str = "pending"
RUNNING: str = "running"
BUILT: str = "built"
FAILED: str = "failed"
SKIPPED: str = "skipped"

# The states of a run that has no `run-end`, beside `running`. A run is `pausing` from a pause request until the engine
# reads it at a step boundary, and `paused` from its `paused` event until a resume (see `delegate.run.pause`).
PAUSING: str = "pausing"
PAUSED: str = "paused"

# The note of a run with a pause request and no live process: the run stopped before the engine read the request.
PAUSE_REQUEST_NOTE: str = "a pause request waits; delegate run --resume removes it"

# What the line of a ticket shows for a value that the journal does not hold.
NONE: str = "-"


def render(events: list[dict[str, object]], journal: Path, snap: Snapshot) -> list[str]:
    """The lines of the report for a run, from its journal events and its snapshot."""
    start = events[0]
    if start["event"] != "run-start":
        raise JournalError(f"journal {journal}: the first event is {start['event']!r}, not 'run-start'")
    lines = [
        f"run {start['run_id']}: {snap.state} (mode {start['mode']}, adapter {start['adapter']})",
        *([f"note: {snap.note}"] if snap.note else []),
        f"progress: {_run_progress(snap)}", f"journal {journal}",
    ]
    progress = {t.id: t for t in snap.tickets}
    for ticket in plan_ids(events):
        own = [e for e in events if e.get("ticket") == ticket]
        state, reason = PENDING, None
        for event in own:
            kind = event["event"]
            if kind == "step-start":
                state = RUNNING
            elif kind == "step-end":
                state, reason = str(event["state"]), event["reason"]
            elif kind == "retry":
                state, reason = RUNNING, None
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

# The phase of the verification of an `economy` chain that an event starts. In `economy` mode only the chain verifies.
# A `gate-start` or a `gate-skip` starts the phase `gates` only when its phase is the phase of the chain gates.
PHASE_OF_CHAIN_EVENT: dict[str, str] = {
    "verify-start": domain.PHASE_VERIFIER, "fixup-start": domain.PHASE_FIXUP,
    "gate-start": domain.PHASE_GATES, "gate-skip": domain.PHASE_GATES,
}

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

    `tokens_new` counts the input, the cache writes, and the output of the agents of the ticket. `tokens_cache` counts
    their cache reads. The two counts and `idle_seconds` are None when the snapshot has no reader of the streams. The two
    counts are None also when a stream of the ticket cannot be read, and `idle_seconds` when the stream of the agent that
    runs now cannot be read.

    `gate` is the command of the gate that runs now, from its `gate-start`, and None when no gate of the ticket runs.
    `gate_idle_seconds` is the seconds since the log file of that gate last changed. It is None when no gate runs, or
    when the log file cannot be read. It needs no reader of the streams.
    """

    id: str
    state: str
    phase: str | None
    elapsed_seconds: int
    tokens_new: int | None
    tokens_cache: int | None
    idle_seconds: int | None
    gate: str | None = None
    gate_idle_seconds: int | None = None


@dataclass(frozen=True)
class Snapshot:
    """One snapshot of a run. `time` is the time of the snapshot: now, the time of `run-end` for a run that ended, or the
    time of `paused` for a run that is paused. `state` is the result of a run that ended, `paused`, `pausing`, or `running`.

    `phase` is the phase of the verification of the chain while it runs in `economy` mode, and None otherwise.

    `note` is `PAUSE_REQUEST_NOTE` when a pause request waits and the run is not active, and None otherwise.

    `tokens_new` and `tokens_cache` are None when the snapshot has no reader of the streams. Otherwise each is the sum of
    the tickets that have tokens: a ticket with a stream that cannot be read adds nothing.
    """

    run_id: str
    state: str
    time: datetime
    elapsed_seconds: int
    tickets_total: int
    tickets_ended: int
    percent: int
    eta: datetime | None
    tokens_new: int | None
    tokens_cache: int | None
    tickets: tuple[TicketSnapshot, ...]
    phase: str | None = None
    note: str | None = None

    def to_json(self) -> dict[str, object]:
        """The snapshot as the JSON object that `delegate status --json` prints. The reference page of `status` documents it."""
        return {
            "run_id": self.run_id, "state": self.state, "time": self.time.isoformat(), "elapsed_seconds": self.elapsed_seconds,
            "tickets_total": self.tickets_total, "tickets_ended": self.tickets_ended, "percent": self.percent,
            "eta": self.eta.isoformat() if self.eta else None, "tokens_new": self.tokens_new, "tokens_cache": self.tokens_cache,
            "phase": self.phase, "note": self.note,
            "tickets": [
                {
                    "id": t.id, "state": t.state, "phase": t.phase, "elapsed_seconds": t.elapsed_seconds, "tokens_new": t.tokens_new,
                    "tokens_cache": t.tokens_cache, "idle_seconds": t.idle_seconds, "gate": t.gate, "gate_idle_seconds": t.gate_idle_seconds,
                }
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
    A gate runs from its `gate-start` until its `gate-result`, a `step-end` (a crash), or the end of the run.
    """
    state, phase = PENDING, None
    started = ended = None
    streams: list[str] = []
    live: dict[str, object] | None = None  # the `agent-start` of the agent that runs now
    gate: dict[str, object] | None = None  # the `gate-start` of the gate that runs now
    for event in own:
        kind = str(event["event"])
        if kind == "step-start":
            state, started = RUNNING, started or _time(event)
        elif kind == "step-end":
            state, ended, live, gate = str(event["state"]), ended or _time(event), None, None
        elif kind == "retry":
            state, ended = RUNNING, None
        elif kind == "skip":
            state = SKIPPED
        if kind == "agent-start":
            phase, live = PHASE_OF_ROLE[str(event["role"])], event
        elif kind in AGENT_RESULTS:
            live = None
        elif kind in PHASE_OF_EVENT and (kind != "report-validation" or event["valid"]):
            phase = PHASE_OF_EVENT[kind]
        elif kind in ("gate-start", "gate-result", "gate-skip") and event["phase"] != "fixup":
            phase = domain.PHASE_GATES
        if kind == "gate-start":
            gate = event
        elif kind == "gate-result":
            gate = None
        if "event_stream" in event and str(event["event_stream"]) not in streams:
            streams.append(str(event["event_stream"]))
    if state != RUNNING:
        phase = None
    if run_ended:
        live = gate = None
    elapsed = _seconds(started, ended or now) if started else 0
    duration = elapsed if started and ended else None
    gate_command = None if gate is None else str(gate["command"])
    gate_idle = None if gate is None else _log_idle_seconds(Path(str(gate["gate_log"])), now)
    if activity is None:
        return TicketSnapshot(ticket, state, phase, elapsed, None, None, None, gate_command, gate_idle), duration
    tokens: tuple[int, int] | None = (0, 0)  # the new tokens and the cache reads
    idle = None
    for stream in streams:
        is_live = live is not None and stream == str(live["event_stream"])
        found = _read(activity, Path(stream), is_live)
        if found is None:
            tokens = None  # a count without this agent is not the count of the ticket
            continue
        if tokens is not None:
            tokens = (tokens[0] + found.tokens_new, tokens[1] + found.tokens_cache)
        if is_live:
            idle = _seconds(found.last_event or _time(live), now)  # type: ignore[arg-type]
    new, cache = tokens if tokens is not None else (None, None)
    return TicketSnapshot(ticket, state, phase, elapsed, new, cache, idle, gate_command, gate_idle), duration


def _chain(events: list[dict[str, object]], now: datetime) -> tuple[str | None, int]:
    """The phase of the verification of an `economy` chain, and the seconds since it started. (None, 0) before it starts.

    The verification starts with the first gate of the chain, and its phase follows the newest `gate-start` or `gate-skip`
    of the chain gates (`gates`), `verify-start` (`verifier`), or `fixup-start` (`fix-up`). After a fix-up round, the
    gates of the chain run again, so the phase is `gates` again.
    """
    phase, started = None, None
    for event in events:
        kind = str(event["event"])
        if kind in ("gate-start", "gate-skip") and event.get("phase") != domain.GATES_OF_CHAIN:
            continue  # the gates of one ticket are not the gates of the chain
        if kind in PHASE_OF_CHAIN_EVENT:
            phase, started = PHASE_OF_CHAIN_EVENT[kind], started or _time(event)
    return phase, _seconds(started, now) if started else 0


def _log_idle_seconds(log: Path, now: datetime) -> int | None:
    """The seconds from the last change of a gate log to `now`, or None when the log cannot be read.

    The status only shows data, so a log that is missing, or that the process cannot reach, gives no idle time and
    is not an error.
    """
    try:
        changed = log.stat().st_mtime
    except OSError:
        return None
    return _seconds(datetime.fromtimestamp(changed, now.tzinfo), now)


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
                return Activity(0, 0, None)  # the harness has not opened the stream yet
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
    end = newest_run_end(events)
    paused = end is None and is_paused(events)
    if end is not None:
        now = _time(end)  # the clock of a run that ended stops at its end
    elif paused:
        now = _time(next(e for e in reversed(events) if e["event"] == "paused"))  # and the clock of a paused run at its pause
    now = now.replace(microsecond=0)
    # No agent and no gate works in a run that ended or paused.
    stopped = end is not None or paused
    activity = reader_of(str(start["adapter"]))
    tickets: list[TicketSnapshot] = []
    durations: list[float] = []
    for ticket in plan_ids(events):
        found, duration = _ticket(ticket, [e for e in events if e.get("ticket") == ticket], activity, now, stopped)
        tickets.append(found)
        if duration is not None:
            durations.append(duration)
    ended = sum(1 for t in tickets if t.state in ENDED)
    running = [t for t in tickets if t.state == RUNNING]
    # The verification of an `economy` chain is one more unit: it ends with the run, and while it runs it counts 0.
    chain = domain.chain_units(str(start["mode"]))
    chain_phase, chain_seconds = _chain(events, now) if chain and not stopped else (None, 0)
    units, units_ended = len(tickets) + chain, ended + (chain if end is not None else 0)
    to_end = domain.seconds_to_end(durations, units - units_ended, sum(t.elapsed_seconds for t in running) + chain_seconds)
    state, note = _state(end, paused, journal)
    return Snapshot(
        run_id=str(start["run_id"]), state=state, time=now,
        elapsed_seconds=_seconds(_time(start), now), tickets_total=len(tickets), tickets_ended=ended,
        percent=domain.percent_complete(units_ended, [t.phase for t in running if t.phase], units),
        eta=None if to_end is None or paused else now + timedelta(seconds=to_end),
        tokens_new=None if activity is None else sum(t.tokens_new or 0 for t in tickets),
        tokens_cache=None if activity is None else sum(t.tokens_cache or 0 for t in tickets), tickets=tuple(tickets),
        phase=chain_phase, note=note,
    )


def _state(end: dict[str, object] | None, paused: bool, journal: Path) -> tuple[str, str | None]:
    """The state of a run and its note.

    The state is the result of its `run-end`, `paused`, `pausing` while a pause request waits and the run is active, or
    `running`. The note is `PAUSE_REQUEST_NOTE` when a pause request waits and the run is not active, and None otherwise.
    A lock file that cannot be read does not show that the process stopped, so the run is then `pausing`.
    """
    requested = pause_requested(journal)
    if end is None and not paused and requested:
        try:
            active = is_active(journal)
        except RunsError:
            active = True
        if active:
            return PAUSING, None
    note = PAUSE_REQUEST_NOTE if requested else None
    if end is not None:
        return str(end["result"]), note
    return (PAUSED if paused else RUNNING), note


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


def format_tokens(count: int) -> str:
    """A token count in a short form: `950`, `26k`, or `1.2M`."""
    if count < 1000:
        return str(count)
    if count < 1_000_000:
        return f"{count / 1000:.0f}k"
    return f"{count / 1_000_000:.1f}M"


def format_token_counts(new: int, cache: int) -> str:
    """The two token counts in the short form of each line: `tokens 302k new, 9.7M cache`."""
    return f"tokens {format_tokens(new)} new, {format_tokens(cache)} cache"


def _run_progress(snap: Snapshot) -> str:
    """The progress of the run: the phase of an `economy` chain, the percentage, the tickets that ended, the time used,
    the ETA, and the tokens.

    The ETA shows only while the run runs: in the time from now, or `-` before the first ticket ends. The tokens show
    only when the snapshot has them.
    """
    eta = _duration(_seconds(snap.time, snap.eta)) if snap.eta else "-"
    parts = [snap.phase] if snap.phase else []
    parts += [f"{snap.percent}%", f"{snap.tickets_ended} of {snap.tickets_total} ended", f"{_duration(snap.elapsed_seconds)} used"]
    if snap.state in (RUNNING, PAUSING):
        parts.append(f"ETA {eta}")
    if snap.tokens_new is not None and snap.tokens_cache is not None:
        parts.append(format_token_counts(snap.tokens_new, snap.tokens_cache))
    return ", ".join(parts)


def _ticket_progress(ticket: TicketSnapshot) -> str:
    """The progress of a ticket: the time used, the tokens when the snapshot has them, and the time since the last activity.

    While a gate of the ticket runs, it also gives the gate command and the time since the log of the gate changed.
    """
    parts = [f"{_duration(ticket.elapsed_seconds)} used"]
    if ticket.tokens_new is not None and ticket.tokens_cache is not None:
        parts.append(format_token_counts(ticket.tokens_new, ticket.tokens_cache))
    if ticket.idle_seconds is not None:
        parts.append(f"last activity {_duration(ticket.idle_seconds)} ago")
    if ticket.gate is not None:
        parts.append(f"gate {ticket.gate}")
    if ticket.gate_idle_seconds is not None:
        parts.append(f"gate log changed {_duration(ticket.gate_idle_seconds)} ago")
    return ", ".join(parts)


def view_lines(snap: Snapshot, width: int) -> list[str]:
    """The lines of the terminal view of a snapshot: one for the run, then one for each ticket, each cut to `width`.

    The view names only the run id, the ticket ids, the states, the phases, and the numbers. It shows no path and no tool call.
    """
    lines = [f"run {snap.run_id} {snap.state} {_run_progress(snap)}"]
    for ticket in snap.tickets:
        lines.append(f"  {ticket.id} {ticket.state}" + (f" {ticket.phase}" if ticket.phase else "") + f", {_ticket_progress(ticket)}")
    return [line[: max(width, 1)] for line in lines]


# The text between two parts of the line of an active run.
ACTIVE_SEPARATOR: str = " · "


def active_line(journal: Path, now: datetime) -> str:
    """The one line of an active run. `now` is the time of the clock, in UTC.

    The line gives the run id, the local start time, the mode, the progress, each running ticket and its phase, the
    time since the last activity, the workflow file (or the run branch of a journal that names no workflow file), and
    the command that follows the run. The last activity is the newest change of the journal, of the event streams, and
    of the gate logs, as for the stall check of `delegate watch`. Raise JournalError when the journal is not valid.
    """
    events, _ = read_new_events(journal, 0, 1)
    # The line needs no tokens, so the snapshot reads no event stream.
    snap = snapshot_of(events, journal, lambda adapter: None, now)
    start = events[0]
    running = [f"ticket {t.id}" + (f" {t.phase}" if t.phase else "") for t in snap.tickets if t.state == RUNNING]
    if not running:
        running = [f"chain {snap.phase}" if snap.phase else "no ticket running"]
    idle = _seconds(datetime.fromtimestamp(last_change(journal, activity_files(events)), now.tzinfo), now)
    where = f"workflow {start['workflow']}" if start.get("workflow") else f"run branch {start['run_branch']}"
    return ACTIVE_SEPARATOR.join([
        snap.run_id, f"started {_time(start).astimezone().strftime('%Y-%m-%d %H:%M')}", str(start["mode"]),
        f"{snap.tickets_ended} of {snap.tickets_total} ended, {snap.percent}%", ", ".join(running),
        f"last activity {_duration(idle)} ago", where, f"delegate watch {snap.run_id}",
    ])
