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

The report (`report`) gives the lines of `delegate status`: the header lines of
the run, and the ticket table of frame 1i of the design (#117), with the verdict,
the branch and the last event of each ticket from the journal. `view_lines` draws
the snapshot for the live view of `delegate watch`: the run line and the ticket
table of frame 3a of the design (#143). The two tables share their tracks: the rows
are flat and in plan order, and `plan_links` gives the rail and the `after` cell of
each row, so the depth of the plan never makes the table wider.

When more than one run is active (see `delegate.run.runs`), `delegate status`
and `delegate watch` with no run id show the run picker of frame 2b (#118):
`active_runs` reads each active run, newest first, `picker_lines` draws the
table, and `choices_line` gives the one line that names each run.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from ..ports.harness import Activity
from ..ports.vcs import VersionControl
from . import domain, output
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

# What a line shows for a value that it does not have: the ETA before the first ticket ends.
NONE: str = "-"


def render(events: list[dict[str, object]], journal: Path, snap: Snapshot) -> list[str]:
    """The lines of the report for a run, from its journal events and its snapshot.

    The header lines give the run, the note when there is one, the progress, and the journal. Under them and a blank
    line, the ticket table of frame 1i of the design (#117, #143): the table of the live view (`view_lines`), with
    `state` 8 cells wide and the columns `verdict`, `branch` and `last event`. Status is a snapshot, so it has no live
    tail: the last column has only the reason of a row that failed, was skipped, or that a pause stopped before. A
    cell that the journal has no value for is empty. The table is not cut to the width of the terminal, and no column
    drops.
    """
    start = events[0]
    if start["event"] != "run-start":
        raise JournalError(f"journal {journal}: the first event is {start['event']!r}, not 'run-start'")
    lines = [
        f"run {start['run_id']}: {snap.state} (mode {start['mode']}, adapter {start['adapter']})",
        *([f"note: {snap.note}"] if snap.note else []),
        f"progress: {_run_progress(snap)}", f"journal {journal}",
    ]
    states = {t.id: t.state for t in snap.tickets}
    facts: dict[str, dict[str, str]] = {}
    for ticket in plan_ids(events):
        own = [e for e in events if e.get("ticket") == ticket]
        # The verdict of a chain (`economy` mode) covers each ticket in its `tickets` field, not only the ticket it names.
        verdicts = [
            str(e["verdict"]) for e in events
            if e["event"] == "verdict" and (e.get("ticket") == ticket or ticket in e.get("tickets", []))  # type: ignore[operator]
        ]
        branch = next((str(e["branch"]) for e in own if e["event"] == "step-start"), "")
        facts[ticket] = {
            "verdict": output.styled("bold", verdicts[-1]) if verdicts else "",
            "branch": branch if states[ticket] == RUNNING else output.styled("dim", branch),
            "last event": output.styled("dim", str(own[-1]["time"])) if own else "",
        }
    rows = _rows(snap, plan_links(events), None, output.glyphs(), live=False, facts=facts)
    table, _ = _table(snap, rows, STATUS_COLUMNS, STATUS_TRACKS, frozenset(), False, output.glyphs())
    return [*lines, "", *table]


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

    The last four fields are for the ticket table, and `to_json` leaves them out. `reason` is the reason of the
    `step-end` or the `skip` of the ticket. `fixup_round` is the round of the newest `fixup-start` of the step, or 0.
    `continuation` is the count and the limit of the newest `continuation` of the step, or None. `gate_count` is set
    only in the phase `gates`: the number of the gate of the round (the gates that ended, and the gate that runs), and
    the gates of the stack of the ticket (see `_gate_counts`). It is None when no gate of the round started yet, or
    when the journal does not show how many gates the stack has.
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
    reason: str | None = None
    fixup_round: int = 0
    continuation: tuple[int, int] | None = None
    gate_count: tuple[int, int] | None = None


@dataclass(frozen=True)
class Snapshot:
    """One snapshot of a run. `time` is the time of the snapshot: now, the time of `run-end` for a run that ended, or the
    time of `paused` for a run that is paused. `state` is the result of a run that ended, `paused`, `pausing`, or `running`.

    `phase` is the phase of the verification of the chain while it runs in `economy` mode, and None otherwise.

    `note` is `PAUSE_REQUEST_NOTE` when a pause request waits and the run is not active, and None otherwise.

    `mode` is the mode of the run. `paused_before` is the ticket and the `before` field of the `paused` event of a run
    that is paused, and None otherwise. `to_json` leaves the two out.

    `plan` is each ticket id of the newest plan, in plan order, with the blockers that the plan records for it. It is
    None when the journal records no plan (an earlier version of the engine).

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
    mode: str = ""
    paused_before: tuple[str, str] | None = None
    plan: tuple[tuple[str, tuple[str, ...]], ...] | None = None

    def to_json(self) -> dict[str, object]:
        """The snapshot as the JSON object that `delegate status --json` prints. The reference page of `status` documents it.

        `plan` is the last field (#120): a new field comes after the fields of earlier versions, which keep their bytes.
        """
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
            "plan": None if self.plan is None else [{"id": ticket, "blocked_by": list(blockers)} for ticket, blockers in self.plan],
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
    reason: str | None = None
    fixup_round, continuation = 0, None
    for event in own:
        kind = str(event["event"])
        if kind in ("step-start", "retry"):
            reason, fixup_round, continuation = None, 0, None
        if kind == "step-start":
            state, started = RUNNING, started or _time(event)
        elif kind == "step-end":
            state, ended, live, gate = str(event["state"]), ended or _time(event), None, None
            reason = None if event.get("reason") is None else str(event["reason"])
        elif kind == "retry":
            state, ended = RUNNING, None
        elif kind == "skip":
            state = SKIPPED
            reason = None if event.get("reason") is None else str(event["reason"])
        elif kind == "fixup-start":
            fixup_round = int(event["round"])  # type: ignore[call-overload]
        elif kind == "continuation":
            continuation = (int(event["count"]), int(event["limit"]))  # type: ignore[call-overload]
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
    table = {"reason": reason, "fixup_round": fixup_round, "continuation": continuation}
    if activity is None:
        return TicketSnapshot(ticket, state, phase, elapsed, None, None, None, gate_command, gate_idle, **table), duration
    tokens: tuple[int, int] | None = (0, 0)  # the new tokens and the cache reads
    idle = None
    for stream in streams:
        is_live = live is not None and stream == str(live["event_stream"])
        found = read_stream(activity, Path(stream), is_live)
        if found is None:
            tokens = None  # a count without this agent is not the count of the ticket
            continue
        if tokens is not None:
            tokens = (tokens[0] + found.tokens_new, tokens[1] + found.tokens_cache)
        if is_live:
            idle = _seconds(found.last_event or _time(live), now)  # type: ignore[arg-type]
    new, cache = tokens if tokens is not None else (None, None)
    return TicketSnapshot(ticket, state, phase, elapsed, new, cache, idle, gate_command, gate_idle, **table), duration


# The phases of the gates of one ticket that run every gate of its stack: the build, and the gates after a rebase.
GATE_RUN_PHASES: frozenset[str] = frozenset({"build", "rebase"})

# The events of a ticket that start a new run of its gates: a valid report starts the build gates, and a rebase the
# gates after it.
GATE_RUN_STARTS: frozenset[str] = frozenset({"report-validation", "rebase"})


def _gate_runs(own: list[dict[str, object]]) -> list[tuple[frozenset[str], int]]:
    """Each run of the gates of one ticket, in order: the commands of the run, and the number of the newest gate of the run.

    A run starts at a valid `report-validation` or a `rebase`, and holds the gates of the phases `build` and `rebase`
    after it. A run with no gate yet has the number 0. The number of a gate is its place in the run: the gates of the
    run that ended, and the gate that runs now.
    """
    runs: list[tuple[set[str], int]] = []
    for event in own:
        kind = str(event["event"])
        if kind in GATE_RUN_STARTS and (kind != "report-validation" or event["valid"]):
            runs.append((set(), 0))
        elif kind in ("gate-start", "gate-skip") and event.get("phase") in GATE_RUN_PHASES:
            if not runs:
                runs.append((set(), 0))  # a journal with a gate and no report before it
            commands, number = runs[-1]
            commands.add(str(event["command"]))
            runs[-1] = (commands, number + 1)
    return [(frozenset(commands), number) for commands, number in runs]


def _gate_counts(
    tickets: list[TicketSnapshot], owns: dict[str, list[dict[str, object]]], stacks: dict[str, object]
) -> list[TicketSnapshot]:
    """The tickets, with the `gate_count` of each ticket that runs in the phase `gates`.

    The number of gates of a stack comes from the runs of the gates that ended, of each ticket with the `stack` of its
    `step-start`: the engine runs each gate of the stack in each run, so a run that ended has each gate of the stack
    once. The newest run of a ticket in the phase `gates` has not ended, so it does not count. A stack with no run that
    ended has no number, and its tickets have no `gate_count`. A journal of an earlier version of the engine records no
    stack: each ticket is then in the stack None.
    """
    in_gates = {t.id for t in tickets if t.state == RUNNING and t.phase == domain.PHASE_GATES}
    if not in_gates:
        return tickets
    runs = {ticket: _gate_runs(own) for ticket, own in owns.items() if ticket in stacks}
    totals: dict[object, int] = {}
    for ticket, found in runs.items():
        for commands, _ in found[:-1] if ticket in in_gates else found:
            totals[stacks[ticket]] = max(totals.get(stacks[ticket], 0), len(commands))
    counted = []
    for t in tickets:
        number = runs[t.id][-1][1] if t.id in in_gates and runs.get(t.id) else 0
        total = totals.get(stacks.get(t.id)) if t.id in stacks else None
        counted.append(replace(t, gate_count=(number, max(number, total))) if number and total is not None else t)
    return counted


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


def read_stream(activity: StreamReader, stream: Path, is_live: bool) -> Activity | None:
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
    owns = {ticket: [e for e in events if e.get("ticket") == ticket] for ticket in plan_ids(events)}
    for ticket, own in owns.items():
        found, duration = _ticket(ticket, own, activity, now, stopped)
        tickets.append(found)
        if duration is not None:
            durations.append(duration)
    stacks = {str(e["ticket"]): e.get("stack") for e in events if e["event"] == "step-start"}
    tickets = _gate_counts(tickets, owns, stacks)
    ended = sum(1 for t in tickets if t.state in ENDED)
    running = [t for t in tickets if t.state == RUNNING]
    # The verification of an `economy` chain is one more unit: it ends with the run, and while it runs it counts 0.
    chain = domain.chain_units(str(start["mode"]))
    chain_phase, chain_seconds = _chain(events, now) if chain and not stopped else (None, 0)
    units, units_ended = len(tickets) + chain, ended + (chain if end is not None else 0)
    to_end = domain.seconds_to_end(durations, units - units_ended, sum(t.elapsed_seconds for t in running) + chain_seconds)
    state, note = _state(end, paused, journal)
    pause = next((e for e in reversed(events) if e["event"] == "paused"), None) if paused else None
    return Snapshot(
        run_id=str(start["run_id"]), state=state, time=now,
        elapsed_seconds=_seconds(_time(start), now), tickets_total=len(tickets), tickets_ended=ended,
        percent=domain.percent_complete(units_ended, [t.phase for t in running if t.phase], units),
        eta=None if to_end is None or paused else now + timedelta(seconds=to_end),
        tokens_new=None if activity is None else sum(t.tokens_new or 0 for t in tickets),
        tokens_cache=None if activity is None else sum(t.tokens_cache or 0 for t in tickets), tickets=tuple(tickets),
        phase=chain_phase, note=note, mode=str(start["mode"]),
        paused_before=None if pause is None else (str(pause.get("ticket")), str(pause.get("before"))), plan=_plan(events),
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


def format_duration(seconds: int) -> str:
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
    eta = format_duration(_seconds(snap.time, snap.eta)) if snap.eta else "-"
    parts = [snap.phase] if snap.phase else []
    parts += [f"{snap.percent}%", f"{snap.tickets_ended} of {snap.tickets_total} ended", f"{format_duration(snap.elapsed_seconds)} used"]
    if snap.state in (RUNNING, PAUSING):
        parts.append(f"ETA {eta}")
    if snap.tokens_new is not None and snap.tokens_cache is not None:
        parts.append(format_token_counts(snap.tokens_new, snap.tokens_cache))
    return ", ".join(parts)


# The run line of the live view draws the bar only on a terminal of this many columns or more, and the bar has 16 cells.
BAR_COLUMNS: int = 100
BAR_CELLS: int = 16

# The columns of the ticket table of the live view, in order (components.md § Ticket table (watch), #143). The rail
# and the live tail have no header. The columns of numbers align right.
RAIL: str = "rail"
TAIL: str = "tail"
TABLE_COLUMNS: tuple[str, ...] = ("#", RAIL, "plan", "state", "phase", "after", "used", "new", "cache", TAIL)
TABLE_NUMBERS: frozenset[str] = frozenset({"#", "used", "new", "cache"})

# The columns of the ticket table of `delegate status` (frame 1i, components.md § Ticket table (status)): the columns of
# the live view, with `verdict` and `branch` after `after`, and `last event` last. Status is a snapshot, so it has no
# live tail: its last column is only the reason of a row that failed, was skipped, or that a pause stopped before.
STATUS_COLUMNS: tuple[str, ...] = (
    "#", RAIL, "plan", "state", "phase", "after", "verdict", "branch", "used", "new", "cache", "last event", TAIL,
)

# The design width of each column of the ticket table of the live view, in cells. A column is as wide as its track, or
# as its widest value when that is wider, so the depth of the plan never makes the table wider. The live tail has no
# track. The table of `delegate status` has the same tracks, with `state` 8, `verdict` 7 and `branch` 15. `last event`
# has no track.
TRACKS: dict[str, int] = {"#": 3, RAIL: 2, "plan": 6, "state": 10, "phase": 13, "after": 8, "used": 7, "new": 5, "cache": 6}
STATUS_TRACKS: dict[str, int] = {**TRACKS, "state": 8, "verdict": 7, "branch": 15}

# The steps of the fit to the width, in the order of components.md § 80 columns: the columns that drop at each step.
# The bar of the run line goes first, by `BAR_COLUMNS`. Then `new` and `cache` drop, then `used`. The phases stack, so
# their labels stay.
FIT_STEPS: tuple[frozenset[str], ...] = (frozenset(), frozenset({"new", "cache"}), frozenset({"new", "cache", "used"}))

# The phases of a running ticket. In `economy` mode only the chain verifies, so a ticket has two phases.
TRACK: tuple[str, ...] = (domain.PHASE_SPECIALIST, domain.PHASE_GATES, domain.PHASE_VERIFIER, domain.PHASE_FIXUP)
ECONOMY_TRACK: tuple[str, ...] = (domain.PHASE_SPECIALIST, domain.PHASE_GATES)

# The reason of a ticket that a pause stopped before, by the `before` field of the `paused` event.
PAUSED_CELL: dict[str, str] = {"step": "paused before this step", "gate": "paused before the gate", "verifier": "paused before the verifier"}


@dataclass(frozen=True)
class _Row:
    """One row of the ticket table: the cells by the name of the column, the ticket and its state, and whether the
    live tail counts in the fit to the width. A reason in the tail does not count: the cut of the line ends it."""

    ticket: str
    state: str
    cells: dict[str, str]
    measured: bool = False


def view_lines(
    snap: Snapshot, links: Sequence[PlanLink], width: int, height: int | None = None, idle_limit: int | None = None
) -> list[str]:
    """The lines of the live view of a snapshot: the run line, a blank line, and the ticket table, each cut to `width`.

    The spec is `design/design_handoff_delegate_terminal_output/docs/components.md`. `links` gives the rail and the
    `after` cell of each ticket (`plan_links`). The rows are flat and in plan order, and the running ticket has a
    sub-row for each phase after its first. When the lines would be more than `height`, the built rows fold to one
    line. When a line of the table is wider than `width`, the columns drop in the order of `FIT_STEPS`. An agent or a
    gate log that has not changed for `idle_limit` seconds or more shows a warning in the live tail. The view names
    only the run id, the ticket ids, the states, the phases, the reasons with no path, and the numbers. It shows no
    tool call.
    """
    glyph = output.glyphs()
    rows = _rows(snap, links, idle_limit, glyph, live=True)
    fold = height is not None and 3 + len(rows) > height and any(t.state == BUILT for t in snap.tickets)
    for dropped in FIT_STEPS:
        table, measured = _table(snap, rows, TABLE_COLUMNS, TRACKS, dropped, fold, glyph)
        if measured <= width:
            break
    return [output.cut(line, max(width, 1)) for line in [_run_line(snap, width, glyph), "", *table]]


def _run_line(snap: Snapshot, width: int, glyph: output.Glyphs) -> str:
    """The run line: the short run id, the state, the bar, the percentage, the tickets that ended, the time used, the
    ETA while the run runs, and the two token counts when the snapshot has them."""
    dot = f" {glyph.pending} "  # the separator of tokens.md, which has an ASCII form
    state = snap.state + (f"{dot}{snap.phase}" if snap.phase else "")
    role = {PAUSED: "warn", PAUSING: "warn", BUILT: "ok bold", FAILED: "bad bold"}.get(snap.state, "")
    progress = f"{snap.percent}%"
    if width >= BAR_COLUMNS:
        filled = snap.percent * BAR_CELLS // 100
        progress = output.styled("ok", glyph.fill * filled) + output.styled("dim", glyph.empty * (BAR_CELLS - filled)) + " " + progress
    parts = [
        "run " + output.styled("bold", output.short_run_id(snap.run_id)), output.styled(role, state), progress,
        f"{snap.tickets_ended}/{snap.tickets_total} ended", output.styled("dim", f"{format_duration(snap.elapsed_seconds)} used"),
    ]
    if snap.state in (RUNNING, PAUSING):
        parts.append("ETA " + (format_duration(_seconds(snap.time, snap.eta)) if snap.eta else NONE))
    if snap.tokens_new is not None and snap.tokens_cache is not None:
        parts.append(output.styled("dim", f"{format_tokens(snap.tokens_new)} new{dot}{format_tokens(snap.tokens_cache)} cache"))
    return output.GAP.join(parts)


def _table(
    snap: Snapshot, rows: Sequence[_Row], columns: tuple[str, ...], tracks: dict[str, int], dropped: frozenset[str], fold: bool,
    glyph: output.Glyphs,
) -> tuple[list[str], int]:
    """The lines of the ticket table, and the width that the fit to the width measures.

    The lines are the header and the rows, with the built rows folded to one line when `fold` is true. Each column is
    as wide as its track, or as its widest value when that is wider. The columns of `dropped` are not in the table, and
    neither are `new` and `cache` when the snapshot has no tokens. The width is the widest line, without a reason in
    the tail (`_Row.measured`).
    """
    names = [name for name in columns if name not in dropped and (snap.tokens_new is not None or name not in ("new", "cache"))]
    header = [output.styled("dim", "" if name in (RAIL, TAIL) else name) for name in names]
    kept = [row for row in rows if not (fold and row.state == BUILT)]
    folded = [row.ticket for row in rows if fold and row.state == BUILT]
    fold_at = next((index for index, row in enumerate(rows) if fold and row.state == BUILT), None)
    body = [[row.cells.get(name, "") for name in names] for row in kept]
    widths = [max(tracks.get(name, 0), *(output.width(cells[index]) for cells in [header, *body])) for index, name in enumerate(names)]
    right = {index for index, name in enumerate(names) if name in TABLE_NUMBERS}
    lines = [line.rstrip(" ") for line in output.columns([header, *body], right=right, tracks=widths)]
    quiet = [[cells[index] if name != TAIL or row.measured else "" for index, name in enumerate(names)] for row, cells in zip(kept, body)]
    measured = max(output.width(line.rstrip(" ")) for line in output.columns([header, *quiet], right=right, tracks=widths))
    if fold_at is not None:
        start = {name: sum(widths[:index]) + len(output.GAP) * index for index, name in enumerate(names)}
        line = " " * start["plan"] + output.styled("ok", glyph.built) + output.styled("dim", f" {len(folded)} built")
        if TAIL in start:
            line = line + " " * max(1, start[TAIL] - output.width(line)) + output.styled("dim", " ".join(folded))
        lines.insert(1 + fold_at, line)
    return lines, measured


def _rows(
    snap: Snapshot, links: Sequence[PlanLink], idle_limit: int | None, glyph: output.Glyphs, live: bool,
    facts: dict[str, dict[str, str]] | None = None,
) -> list[_Row]:
    """The rows of the ticket table in plan order: one row for each ticket, and under the row of a running ticket a
    sub-row for each phase after its first.

    The rail of a row joins it to the row above it: its one blocker is that row. A sub-row has the rail when the next
    row has it, so the rail runs through the sub-rows. `live` gives the live tail of the live view: the activity of the
    running ticket on its row, and the gate that runs on the sub-row of its phase. Without it, as in `delegate status`,
    the tail has only the reason of a row. `facts` gives, for each ticket id, the cells of the columns that the live
    view does not have: `verdict`, `branch` and `last event` of `delegate status`.
    """
    by_id = {link.id: link for link in links}
    rows = []
    for position, ticket in enumerate(snap.tickets, start=1):
        link = by_id.get(ticket.id)
        below = by_id.get(snap.tickets[position].id) if position < len(snap.tickets) else None
        phases, active = _phases(snap, ticket, glyph)
        details = [""] * max(1, len(phases))
        if live and ticket.state == RUNNING:
            details[0] = _activity_tail(ticket, idle_limit, glyph)
            if ticket.gate is not None:
                at = active if 0 < active < len(details) else 0
                details[at] = f" {glyph.pending} ".join(part for part in (details[at], _gate_tail(ticket, idle_limit, glyph)) if part)
        reason = _reason(snap, ticket)
        cells = _row(ticket, position, link, glyph)
        cells.update(phase=phases[0] if phases else "", **{TAIL: reason or details[0]})
        cells.update((facts or {}).get(ticket.id, {}))
        rows.append(_Row(ticket.id, ticket.state, cells, measured=not reason))
        rail = output.styled("plan", glyph.rail) if below is not None and below.rail else ""
        for phase, detail in zip(phases[1:], details[1:]):
            rows.append(_Row(ticket.id, ticket.state, {RAIL: rail, "phase": phase, TAIL: detail}, measured=True))
    return rows


def _row(ticket: TicketSnapshot, position: int, link: PlanLink | None, glyph: output.Glyphs) -> dict[str, str]:
    """The cells of the row of one ticket that the live view and `delegate status` share, by the name of its column."""
    node, id_role, state = {
        BUILT: (output.styled("ok", glyph.node), "dim", output.styled("ok", glyph.built) + output.styled("dim", " built")),
        RUNNING: (output.styled("now", glyph.running), "now bold", output.styled("now bold", RUNNING)),
        FAILED: (output.styled("bad", glyph.failed), "bad bold", output.styled("bad bold", FAILED)),
        SKIPPED: (output.styled("dim", glyph.skipped), "dim", output.styled("dim", SKIPPED)),
    }.get(ticket.state, (output.styled("dim", glyph.pending), "dim", output.styled("dim", ticket.state)))
    has_step = ticket.state not in (PENDING, SKIPPED)
    tokens = has_step and ticket.tokens_new is not None and ticket.tokens_cache is not None
    return {
        "#": output.styled("dim", str(position)),
        RAIL: output.styled("plan", glyph.rail) if link is not None and link.rail else "",
        "plan": node + " " + output.styled(id_role, ticket.id),
        "state": state,
        "after": output.styled("plan", " ".join(link.after)) if link is not None else "",
        "used": output.styled("dim", format_duration(ticket.elapsed_seconds)) if has_step else "",
        "new": output.styled("dim", format_tokens(ticket.tokens_new)) if tokens else "",  # type: ignore[arg-type]
        "cache": output.styled("dim", format_tokens(ticket.tokens_cache)) if tokens else "",  # type: ignore[arg-type]
    }


def _reason(snap: Snapshot, ticket: TicketSnapshot) -> str:
    """The reason of a ticket that the pause of the run stopped before, that failed, or that was skipped, or empty."""
    if snap.state == PAUSED and snap.paused_before is not None and snap.paused_before[0] == ticket.id:
        return output.styled("warn", PAUSED_CELL.get(snap.paused_before[1], f"paused before the {snap.paused_before[1]}"))
    if ticket.state == FAILED and ticket.reason:
        return output.styled("bad", output.without_paths(ticket.reason))
    if ticket.state == SKIPPED and ticket.reason:
        return output.styled("dim", output.without_paths(ticket.reason))
    return ""


def _phases(snap: Snapshot, ticket: TicketSnapshot, glyph: output.Glyphs) -> tuple[list[str], int]:
    """The phase cells of a running ticket, one for each phase in order, and the index of the active phase.

    A phase that is done has `✓` in `ok`, the active phase has `●` in `now`, and a phase to come has `○` in `dim`. The
    active phase `gates` gives its gate count (`gates ● 2/3`), and a fix-up round is in `warn` with its round and the
    limit of the mode (`fix-up ● 1/2`). A ticket that does not run has no phase.
    """
    if ticket.state != RUNNING or ticket.phase is None:
        return [], 0
    track = ECONOMY_TRACK if snap.mode == domain.ECONOMY else TRACK
    active = track.index(ticket.phase) if ticket.phase in track else len(track)
    rounds = f" {ticket.fixup_round}/{domain.fixup_round_limit(snap.mode)}" if ticket.fixup_round and snap.mode in domain.FIXUP_ROUND_LIMIT else ""
    cells = []
    for index, phase in enumerate(track):
        if phase == domain.PHASE_FIXUP and index == active:
            cells.append(output.styled("warn", f"{phase} {glyph.node}{rounds}"))
        elif index == active:
            count = f" {ticket.gate_count[0]}/{ticket.gate_count[1]}" if phase == domain.PHASE_GATES and ticket.gate_count else ""
            cells.append(output.styled("now", f"{phase} {glyph.node}{count}"))
        elif index < active or (phase == domain.PHASE_FIXUP and rounds):
            cells.append(output.styled("dim", phase + " ") + output.styled("ok", glyph.built) + (output.styled("dim", rounds) if phase == domain.PHASE_FIXUP else ""))
        else:
            cells.append(output.styled("dim", f"{phase} {glyph.open}"))
    return cells, active


def _idle(seconds: int, quiet: str, idle_limit: int | None, glyph: output.Glyphs) -> str:
    """The warning of an activity that is idle for `idle_limit` seconds or more, or else `quiet`."""
    if idle_limit is not None and seconds >= idle_limit:
        return output.styled("warn", f"{glyph.idle} idle {format_duration(seconds)}")
    return quiet


def _activity_tail(ticket: TicketSnapshot, idle_limit: int | None, glyph: output.Glyphs) -> str:
    """The live tail of the row of a running ticket: its continuations, and the activity of its agent."""
    parts = []
    if ticket.continuation is not None:
        parts.append(output.styled("warn", f"cont {ticket.continuation[0]}/{ticket.continuation[1]}"))
    if ticket.idle_seconds is not None:
        parts.append(_idle(ticket.idle_seconds, output.styled("now", f"active {format_duration(ticket.idle_seconds)}"), idle_limit, glyph))
    return f" {glyph.pending} ".join(parts)


def _gate_tail(ticket: TicketSnapshot, idle_limit: int | None, glyph: output.Glyphs) -> str:
    """The detail of the gate that runs: its short name and the age of its log, on the sub-row of its phase."""
    gate = f"gate {output.gate_name(str(ticket.gate))}"
    if ticket.gate_idle_seconds is not None:
        gate += f" {glyph.pending} " + _idle(ticket.gate_idle_seconds, f"log {format_duration(ticket.gate_idle_seconds)} ago", idle_limit, glyph)
    return gate


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
    blockers = _newest_blockers(events) or {}
    ids = plan_ids(events)
    known = set(ids)
    links = []
    for index, ticket in enumerate(ids):
        own = tuple(blocker for blocker in blockers.get(ticket, []) if blocker in known)
        links.append(PlanLink(ticket, own, index > 0 and own == (ids[index - 1],)))
    return links


def _newest_blockers(events: list[dict[str, object]]) -> dict[str, list[str]] | None:
    """The blockers of each ticket in the `plan` field of the newest `plan-change`, or else of `run-start` (#107).

    None when that event records no plan: an earlier version of the engine wrote only the ticket ids.
    """
    records = [event.get("plan") for event in events if event["event"] == "plan-change"] or [events[0].get("plan")]
    if records[-1] is None:
        return None
    return {str(r["id"]): [str(b) for b in r["blocked_by"]] for r in records[-1]}  # type: ignore[union-attr, index]


def _plan(events: list[dict[str, object]]) -> tuple[tuple[str, tuple[str, ...]], ...] | None:
    """The newest plan of a run for `status --json`: each ticket id in plan order, with its blockers as recorded."""
    blockers = _newest_blockers(events)
    if blockers is None:
        return None
    return tuple((ticket, tuple(blockers.get(ticket, ()))) for ticket in plan_ids(events))


@dataclass(frozen=True)
class ActiveRun:
    """One active run, for the run picker (frame 2b, #118): its journal, its snapshot with no tokens, its run branch,
    the time of its `run-start` event, and the seconds since its last activity.

    The last activity is the newest change of the journal, of the event streams, and of the gate logs, as for the stall
    check of `delegate watch`.
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
        found.append(ActiveRun(journal, snap, str(events[0]["run_branch"]), _time(events[0]), _seconds(changed, now)))
    return sorted(found, key=lambda run: run.started, reverse=True)


# The columns of the run picker, in order, and the columns of numbers, which align right.
PICKER_HEADER: tuple[str, ...] = ("#", "run", "run branch", "mode", "since", "progress", "now")
PICKER_NUMBERS: frozenset[int] = frozenset({0})


def picker_lines(runs: Sequence[ActiveRun], now: datetime) -> list[str]:
    """The lines of the run picker: `<n> runs are active in this repository`, and a table with one row for each run.

    The spec is `design/design_handoff_delegate_terminal_output/docs/components.md` (Run picker). The rows keep the
    order of `runs`. A row gives the number to choose, the full run id with its short id bold, the run branch, the mode,
    the local start time (with the day of the week when the run did not start today), the progress, and what runs now
    with the time since the last activity. `now` is the time of the clock, in UTC.
    """
    glyph = output.glyphs()
    dot = f" {glyph.pending} "
    today = now.astimezone().date()
    rows = [[output.styled("dim", name) for name in PICKER_HEADER]]
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
            doing + dot + output.styled("now", f"active {format_duration(run.idle_seconds)}"),
        ])
    table = [line.rstrip(" ") for line in output.columns(rows, right=PICKER_NUMBERS)]
    return [f"{len(runs)} runs are active in this repository", *table]


def choices_line(runs: Sequence[ActiveRun]) -> str:
    """The one line that names each active run, in the order of `runs`, for a reader that cannot answer a prompt.

    The copy deck gives it: `<n> runs are active in this repository; give a run id: <id> (<branch>, <state>), …`.
    """
    named = ", ".join(f"{run.snap.run_id} ({run.run_branch}, {run.snap.state})" for run in runs)
    return f"{len(runs)} runs are active in this repository; give a run id: {named}"
