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
resume, `stopped` when it has no `run-end`, no pause, no halt and no live process (#193), or the result of its `run-end`. A pause request of a run that is not
active stays until the next resume: the run then keeps the state of its
journal, and the snapshot has the note `_PAUSE_REQUEST_NOTE`.

The values of a snapshot are in `delegate.run.status_model`. The report of `delegate status` and the live view of
`delegate watch` are in `delegate.run.status_table`, and the run picker and the plan links are in `delegate.run.status_picker` (#335).

`liveness` gives the time since the last activity of an agent (#147). A snapshot,
as the run picker, gives `active <time> ago`. The live view on a terminal gives
a spinner while the agent is active, and `quiet <time>` after `_QUIET_SECONDS`.
Past the stall time, both give `⚠ idle <time>`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from ..ports.harness import Activity
from ..ports.vcs import VersionControl
from . import domain, output, run_state
from .events import GATE_PHASE_FIXUP, ROLE_FIXUP, ROLE_SPECIALIST, ROLE_VERIFIER
from .journal import ABANDONED, JournalError, abandon_note, crash_note, event_time, plan_ids, read_new_events, setup_problem_note
from .pause import pause_requested
from .runs import RunsError, has_live_process, journal_of
from .state_location import StateLocation
from .status_model import (
    ACCEPTED, BUILT, FAILED, HALTED, PAUSED, PAUSING, REJECTED, RUNNING, SKIPPED, STOPPED, ChainSnapshot, ReaderOf, Snapshot,
    StreamReader, TicketSnapshot, format_duration,
)

# The states of the run state in which the clock of the snapshot stops at the event that gives the state (and the run has a live process).
_CLOCK_STOPS: tuple[str, ...] = (run_state.ENDED, run_state.PAUSED, run_state.HALTED)

# The note of a run with a pause request and no live process: the run stopped before the engine read the request.
_PAUSE_REQUEST_NOTE: str = "a pause request waits; delegate run --resume removes it"

# The seconds with no activity of an agent after which the live view shows `quiet <time>` and no spinner (#147).
_QUIET_SECONDS: int = 30

# The states of a ticket that ended.
ENDED: tuple[str, ...] = (BUILT, FAILED, SKIPPED)

# The phase that an event of a running ticket starts. `report-validation` starts the gates only when the report is valid.
_PHASE_OF_EVENT: dict[str, str] = {
    "step-start": domain.PHASE_SPECIALIST, "continuation": domain.PHASE_SPECIALIST, "report-validation": domain.PHASE_GATES,
    "rebase": domain.PHASE_GATES, "verify-start": domain.PHASE_VERIFIER, "fixup-start": domain.PHASE_FIXUP,
}

# The phase of the agent that an `agent-start` event names, by its `role`.
_PHASE_OF_ROLE: dict[str, str] = {
    ROLE_SPECIALIST: domain.PHASE_SPECIALIST, ROLE_VERIFIER: domain.PHASE_VERIFIER, ROLE_FIXUP: domain.PHASE_FIXUP,
}

# The phase of the verification of an `economy` chain that an event starts. In `economy` mode only the chain verifies.
# A `gate-start` or a `gate-skip` starts the phase `gates` only when its phase is the phase of the chain gates.
_PHASE_OF_CHAIN_EVENT: dict[str, str] = {
    "verify-start": domain.PHASE_VERIFIER, "fixup-start": domain.PHASE_FIXUP,
    "gate-start": domain.PHASE_GATES, "gate-skip": domain.PHASE_GATES,
}

# The events that end an agent run.
_AGENT_RESULTS: frozenset[str] = frozenset({"adapter-result", "fixup-result", "verify-result"})


def seconds_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds()))


def _ticket(
    ticket: str, own: list[dict[str, object]], settled: run_state.TicketState, activity: StreamReader | None, now: datetime,
    run_ended: bool,
) -> tuple[TicketSnapshot, int | None]:
    """The snapshot of one ticket from its own events, and the seconds of its step when it ended after one.

    The state of the ticket, and the reason of a failed or a skipped ticket, come from the run state (`settled`, ADR 0009). A ticket
    that a retry runs again, or that has an open step, shows as running. The phase, the times, the tokens and the gate come from the
    events of the ticket.

    With no reader (`activity` is None), the ticket has no tokens and no idle time, and its streams are not read. A
    result event that holds the totals of its agent (`journal_totals`) gives them, and its stream is not read (#183).

    An agent runs from its `agent-start` until its result event, a `step-end` (a crash), or the end of the run. The
    verifier of an `economy` chain starts after its ticket ended, so a ticket that ended can have an agent that runs.
    A gate runs from its `gate-start` until its `gate-result`, a `step-end` (a crash), or the end of the run.
    """
    state = RUNNING if settled.retrying or settled.outcome == run_state.OPEN else settled.outcome
    phase = None
    started = ended = None
    streams: list[str] = []
    totals: dict[str, Activity] = {}  # the totals that the result events hold, by event stream (#183)
    live: dict[str, object] | None = None  # the `agent-start` of the agent that runs now
    gate: dict[str, object] | None = None  # the `gate-start` of the gate that runs now
    reason = settled.reason if state in (FAILED, SKIPPED) else None
    trace: str | None = None
    fixup_round, continuation = 0, None
    for event in own:
        kind = str(event["event"])
        if kind in ("step-start", "retry"):
            trace, fixup_round, continuation = None, 0, None
        if kind == "step-start":
            started = started or event_time(event)
        elif kind == "step-end":
            ended, live, gate = ended or event_time(event), None, None
            trace = None if event.get("traceback") is None else str(event["traceback"])
        elif kind == "retry":
            ended = None
        elif kind == "fixup-start":
            fixup_round = int(event["round"])  # type: ignore[call-overload]
        elif kind == "continuation":
            continuation = (int(event["count"]), int(event["limit"]))  # type: ignore[call-overload]
        if kind == "agent-start":
            phase, live = _PHASE_OF_ROLE[str(event["role"])], event
        elif kind in _AGENT_RESULTS:
            live = None
            ended_totals = journal_totals(event)
            if ended_totals is not None:
                totals[str(event["event_stream"])] = ended_totals
        elif kind in _PHASE_OF_EVENT and (kind != "report-validation" or event["valid"]):
            phase = _PHASE_OF_EVENT[kind]
        elif kind in ("gate-start", "gate-result", "gate-skip") and event["phase"] != GATE_PHASE_FIXUP:
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
    elapsed = seconds_between(started, ended or now) if started else 0
    duration = elapsed if started and ended else None
    gate_command = None if gate is None else str(gate["command"])
    gate_idle = None if gate is None else _log_idle_seconds(Path(str(gate["gate_log"])), now)
    gate_name = None if gate is None or not gate.get("name") else str(gate["name"])
    table = {"reason": reason, "traceback": trace, "fixup_round": fixup_round, "continuation": continuation, "gate_name": gate_name}
    if activity is None:
        return TicketSnapshot(ticket, state, phase, elapsed, None, None, None, gate_command, gate_idle, **table), duration
    new, cache, idle = _agent_counts(streams, totals, live, activity, now)
    return TicketSnapshot(ticket, state, phase, elapsed, new, cache, idle, gate_command, gate_idle, **table), duration


def _agent_counts(
    streams: list[str], totals: dict[str, Activity], live: dict[str, object] | None, activity: StreamReader, now: datetime
) -> tuple[int | None, int | None, int | None]:
    """The new tokens, the cache reads and the idle seconds of the agents of `streams`, as a ticket and a chain count them.

    An agent that ended has its totals in `totals`; only an agent that runs (`live` is its `agent-start`), or an earlier
    journal, needs the stream. A stream that cannot be read gives no counts, and no idle time when its agent runs.
    """
    tokens: tuple[int, int] | None = (0, 0)  # the new tokens and the cache reads
    idle = None
    for stream in streams:
        is_live = live is not None and stream == str(live["event_stream"])
        found = totals[stream] if stream in totals and not is_live else read_stream(activity, Path(stream), is_live)
        if found is None:
            tokens = None  # a count without this agent is not the count of the ticket
            continue
        if tokens is not None:
            tokens = (tokens[0] + found.tokens_new, tokens[1] + found.tokens_cache)
        if is_live:
            idle = seconds_between(found.last_event or event_time(live), now)  # type: ignore[arg-type, union-attr]
    return (*tokens, idle) if tokens is not None else (None, None, idle)


# The phases of the gates of one ticket that run every gate of its stack: the build, and the gates after a rebase.
_GATE_RUN_PHASES: frozenset[str] = frozenset({"build", "rebase"})

# The events of a ticket that start a new run of its gates: a valid report starts the build gates, and a rebase the
# gates after it.
_GATE_RUN_STARTS: frozenset[str] = frozenset({"report-validation", "rebase"})


def _gate_runs(own: list[dict[str, object]]) -> list[tuple[frozenset[str], int]]:
    """Each run of the gates of one ticket, in order: the commands of the run, and the number of the newest gate of the run.

    A run starts at a valid `report-validation` or a `rebase`, and holds the gates of the phases `build` and `rebase`
    after it. A run with no gate yet has the number 0. The number of a gate is its place in the run: the gates of the
    run that ended, and the gate that runs now.
    """
    runs: list[tuple[set[str], int]] = []
    for event in own:
        kind = str(event["event"])
        if kind in _GATE_RUN_STARTS and (kind != "report-validation" or event["valid"]):
            runs.append((set(), 0))
        elif kind in ("gate-start", "gate-skip") and event.get("phase") in _GATE_RUN_PHASES:
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


def _chain_phase_of(event: dict[str, object]) -> str | None:
    """The phase of the verification of an `economy` chain that an event starts, or None for an event that starts none.

    The gates of one ticket are not the gates of the chain, so a `gate-start` or a `gate-skip` counts only in its phase
    `chain`. The agent of the verifier or of a fix-up specialist starts its phase too: in `economy` mode only the chain
    runs them. A `retry` at the chain fix-up starts the phase `fix-up`: the stopped fix-up runs again.
    """
    kind = str(event["event"])
    if kind in ("gate-start", "gate-skip") and event.get("phase") != domain.GATES_OF_CHAIN:
        return None
    if kind == "retry":
        # A retry at the chain fix-up goes on with the verification: the engine runs the stopped fix-up again (#297).
        return domain.PHASE_FIXUP if event.get("phase") == domain.RETRY_CHAIN_FIXUP else None
    if kind == "agent-start":
        role = str(event["role"])
        return _PHASE_OF_ROLE[role] if role != "specialist" else None
    return _PHASE_OF_CHAIN_EVENT.get(kind)


def _chain(events: list[dict[str, object]], now: datetime) -> tuple[str | None, int]:
    """The phase of the verification of an `economy` chain, and the seconds since it started. (None, 0) before it starts.

    `events` are the events of the current pass of the chain (`run_state.fold(...).chain.start`). The verification starts with the first
    gate of the chain in the pass, and its
    phase follows the newest `gate-start` or `gate-skip` of the chain gates (`gates`), `verify-start` (`verifier`), or
    `fixup-start` (`fix-up`). After a fix-up round, the gates of the chain run again, so the phase is `gates` again.
    """
    phase, started = None, None
    for event in events:
        found = _chain_phase_of(event)
        if found is not None:
            phase, started = found, started or event_time(event)
    return phase, seconds_between(started, now) if started else 0


# The events of the chain of one stack that the chain row reads, beside the events that start a phase.
_CHAIN_RESULT_EVENTS: frozenset[str] = frozenset({"gate-result", "verdict", "step-end", *_AGENT_RESULTS})


def _chains(
    events: list[dict[str, object]], plan: list[str], stacks: dict[str, object], activity: StreamReader | None, now: datetime,
    stopped: bool, run_ended: bool,
) -> list[ChainSnapshot]:
    """The chain row of each stack that started its verification in an `economy` run (#196).

    The engine names the chain of a stack by the last ticket of the stack: the gates, the verifier and the fix-up rounds
    of the chain carry that ticket. A group is the events of one such ticket from its first event of the chain. A new
    `step-start` ends its group. `events` are the events of the current pass of the chain (`run_state.fold(...).chain.start`, the
    rule of the engine): the chain after the start of the pass is a new one, and the first pass of each stack is gone, also when
    the `retry` names the tip of the chain and not the last ticket of the stack (#304). A `retry` at the chain fix-up starts no
    new pass, so its group keeps the fix-up rounds and the time before it (#297).

    The chain is `accepted` when its newest verdict is ACCEPT. It is `rejected` when its last ticket failed after the
    chain started (red gates, a crash), when its newest verdict is not ACCEPT and no fix-up follows it and a ticket
    failed after it, or when the run ended with no ACCEPT. Else it is `running`. While a run is stopped, paused or
    halted, no gate and no agent works, so the row has neither (as a ticket row has not).
    """
    chain = domain.GATES_OF_CHAIN
    groups: dict[str, list[tuple[int, dict[str, object]]]] = {}
    failures: list[int] = []  # the position of each `step-end` that failed a ticket
    for index, event in enumerate(events):
        kind, ticket = str(event["event"]), event.get("ticket")
        if kind == "step-end" and event.get("state") == FAILED:
            failures.append(index)
        if not isinstance(ticket, str):
            continue
        if kind == "step-start":
            groups.pop(ticket, None)
        elif _chain_phase_of(event) is not None or (ticket in groups and kind in _CHAIN_RESULT_EVENTS and event.get("phase", chain) == chain):
            groups.setdefault(ticket, []).append((index, event))
    found: dict[str, ChainSnapshot] = {}
    newest: dict[str, int] = {}
    for ticket, group in groups.items():
        row = _chain_of(ticket, group, failures, plan, stacks, activity, now, stopped, run_ended)
        if row.stack not in newest or group[-1][0] > newest[row.stack]:
            found[row.stack], newest[row.stack] = row, group[-1][0]
    return list(found.values())


def _chain_of(
    ticket: str, group: list[tuple[int, dict[str, object]]], failures: list[int], plan: list[str], stacks: dict[str, object],
    activity: StreamReader | None, now: datetime, stopped: bool, run_ended: bool,
) -> ChainSnapshot:
    """The chain row of the stack of `ticket` from the `group` of its chain events (see `_chains`)."""
    phase, started = None, None
    streams: list[str] = []
    totals: dict[str, Activity] = {}
    live: dict[str, object] | None = None  # the `agent-start` of the agent that runs now
    gate: dict[str, object] | None = None  # the `gate-start` of the gate that runs now
    verdict: tuple[int, dict[str, object]] | None = None
    fixup_round, failed = 0, None
    for index, event in group:
        kind = str(event["event"])
        found = _chain_phase_of(event)
        if found is not None:
            phase, started, verdict = found, started or event_time(event), None  # a phase after a verdict: the chain goes on
        if kind == "agent-start":
            live = event
        elif kind in _AGENT_RESULTS:
            live = None
            ended_totals = journal_totals(event)
            if ended_totals is not None:
                totals[str(event["event_stream"])] = ended_totals
        elif kind == "gate-start":
            gate = event
        elif kind == "gate-result":
            gate = None
        elif kind == "fixup-start":
            fixup_round = int(event["round"])  # type: ignore[call-overload]
        elif kind == "retry":
            failed = None  # the retry at the chain fix-up revives the tickets that the stop failed
        elif kind == "verdict":
            verdict = (index, event)
        elif kind == "step-end" and event.get("state") == FAILED:
            failed = failed or event
        if "event_stream" in event and str(event["event_stream"]) not in streams:
            streams.append(str(event["event_stream"]))
    assert started is not None  # a group starts with an event of a phase
    last_event = group[-1][1]
    accepted = verdict is not None and domain.is_accepted(str(verdict[1]["verdict"]))
    refused = verdict is not None and not accepted and any(position > verdict[0] for position in failures)
    if accepted:
        state, ended = ACCEPTED, event_time(verdict[1])  # type: ignore[index]
    elif failed is not None or refused or run_ended:
        state = REJECTED
        ended = event_time(failed if failed is not None else verdict[1] if verdict is not None else last_event)
    else:
        state, ended = RUNNING, None
    running = state == RUNNING
    if stopped:
        live = gate = None
    new, cache, idle = (None, None, None) if activity is None else _agent_counts(streams, totals, live, activity, now)
    stack = stacks.get(ticket)
    after = next((t for t in reversed(plan) if stack is not None and stacks.get(t) == stack), ticket)
    return ChainSnapshot(
        stack=str(stack) if stack is not None else "chain", phase=phase if running else None, state=state,
        elapsed_seconds=seconds_between(started, ended or now), gate_command=None if gate is None else str(gate["command"]),
        gate_idle_seconds=None if gate is None else _log_idle_seconds(Path(str(gate["gate_log"])), now), tokens_new=new, tokens_cache=cache,
        verdict=None if running or verdict is None else str(verdict[1]["verdict"]), idle_seconds=idle,
        fixup_round=fixup_round, after=after, gate_name=None if gate is None or not gate.get("name") else str(gate["name"]),
    )


def _log_idle_seconds(log: Path, now: datetime) -> int | None:
    """The seconds from the last change of a gate log to `now`, or None when the log cannot be read.

    The status only shows data, so a log that is missing, or that the process cannot reach, gives no idle time and
    is not an error.
    """
    try:
        changed = log.stat().st_mtime
    except OSError:
        return None
    return seconds_between(datetime.fromtimestamp(changed, now.tzinfo), now)


def journal_totals(event: dict[str, object]) -> Activity | None:
    """The totals that a result event of an agent holds, or None for a journal of an earlier version that has none (#183).

    The engine reads the stream of an agent once, when the session ends, and writes the new tokens (`tokens_new`), the
    cache reads (`tokens_cache`) and the time of the last activity (`last_event`) into the result event. The stream of
    an agent that ended never changes, so the views take these facts from the journal.
    """
    try:
        new, cache = event["tokens_new"], event["tokens_cache"]
        if not isinstance(new, int) or not isinstance(cache, int) or isinstance(new, bool) or isinstance(cache, bool):
            return None
        last = event.get("last_event")
        return Activity(new, cache, None if last is None else datetime.fromisoformat(str(last)))
    except (KeyError, ValueError):
        return None  # a result event with no totals, or with totals that the view cannot use: the stream is the source


class _CachedReader:
    """A reader of event streams that parses a stream again only when the stream changed (#183).

    It keeps the last `Activity` of each path, with the size and the modification time of the file at that time. The live
    view asks for the same stream many times in a second; the stream of an agent that works changes, the others do not.
    A stream that cannot be read is not kept: the error goes up, and the next call tries again. The file is looked at
    before it is parsed, so a write during the parse makes the key old, and the next call parses the stream again.
    """

    def __init__(self, reader: StreamReader) -> None:
        self._reader = reader
        self._kept: dict[Path, tuple[tuple[int, int], Activity]] = {}

    def __call__(self, stream: Path) -> Activity:
        info = stream.stat()
        key = (info.st_size, info.st_mtime_ns)
        kept = self._kept.get(stream)
        if kept is not None and kept[0] == key:
            return kept[1]
        found = self._reader(stream)
        self._kept[stream] = (key, found)
        return found


def caching(reader_of: ReaderOf) -> ReaderOf:
    """`reader_of` with a `_CachedReader` for each adapter name, so that all calls of a live view share one cache."""
    readers: dict[str, StreamReader | None] = {}

    def cached(adapter: str) -> StreamReader | None:
        if adapter not in readers:
            found = reader_of(adapter)
            readers[adapter] = None if found is None else _CachedReader(found)
        return readers[adapter]

    return cached


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


def snapshot_of(
    events: list[dict[str, object]], journal: Path, reader_of: ReaderOf, now: datetime, *, folded: run_state.RunState | None = None,
) -> Snapshot:
    """The snapshot of a run from its journal events. `now` is the time of the clock, in UTC.

    The reader of the streams comes from `reader_of` once. When it gives None, the snapshot has no tokens and no activity.
    The two facts that the journal does not hold come from the disk: a live process that holds the lock of the run, and a
    pause request that waits. `snapshot_at` is the same snapshot with the two facts given. The lock is read only when its
    answer decides the state (`asks`): the run state of the events is active. The check of the lock gets the first event, so it does not open the journal again (#350). The events fold once: the fold answers the
    question of the lock and goes to `snapshot_at`. A caller that has the fold of the events (`folded`) gives it, and the
    events fold no more.
    """
    folded = run_state.fold(events) if folded is None else folded
    asks = folded.status.kind == run_state.ACTIVE
    return snapshot_at(
        events, journal, reader_of, now, live_process=asks and _has_live_process(journal, events[0] if events else None), pause_request=pause_requested(journal),
        folded=folded,
    )


def snapshot_at(
    events: list[dict[str, object]], journal: Path, reader_of: ReaderOf, now: datetime, *, live_process: bool, pause_request: bool,
    folded: run_state.RunState | None = None,
) -> Snapshot:
    """The snapshot of a run, a function of its journal events, the time `now`, and two facts (#311). It reads no lock and no pause request.

    `live_process` is True when a process holds the lock of the run. `pause_request` is True when a pause request waits.
    `journal` only names the run and its files. The snapshot of a run at an earlier time t is the events up to t, the time
    t, a live process, and no pause request. `folded` is the run state of `events`, when the caller has it already: it
    folds the events when it is None.
    """
    if not events or events[0]["event"] != "run-start":
        raise JournalError(f"journal {journal}: the first event is not 'run-start'")
    start = events[0]
    # The state of the run is the state of the run state (ADR 0009). The lock and the pause request are the two facts it lacks.
    folded = run_state.fold(events) if folded is None else folded
    stop = folded.status.event
    # A run that ended, paused or halted keeps its clock at that event. A crash and a stop by the check of the setup end the
    # run process too: the run is stopped, also in the moment before the process releases its lock (#281, #296).
    at_stop = folded.status.kind in _CLOCK_STOPS
    live = at_stop or (folded.status.kind == run_state.ACTIVE and live_process)
    if at_stop:
        now = event_time(stop)  # the clock of a run that ended, paused or halted stops at that event
    elif not live:
        now = event_time(events[-1])  # and the clock of a stopped run at its newest event
    now = now.replace(microsecond=0)
    # No agent and no gate works in a run that ended, paused, halted or stopped.
    stopped = at_stop or not live
    run_ended = folded.run_end is not None
    activity = reader_of(str(start["adapter"]))
    tickets: list[TicketSnapshot] = []
    durations: list[float] = []
    plan = plan_ids(events)
    owns = events_by_ticket(events, plan)
    for ticket, own in owns.items():
        found, duration = _ticket(ticket, own, folded.ticket(ticket), activity, now, stopped)
        tickets.append(found)
        if duration is not None:
            durations.append(duration)
    stacks = {str(e["ticket"]): e.get("stack") for e in events if e["event"] == "step-start"}
    tickets = _gate_counts(tickets, owns, stacks)
    ended = sum(1 for t in tickets if t.state in ENDED)
    running = [t for t in tickets if t.state == RUNNING]
    # The verification of an `economy` chain is one more unit: it ends with the run, and while it runs it counts by its phase.
    chain = domain.chain_units(str(start["mode"]))
    chain_phase, chain_seconds = _chain(events[folded.chain.start :], now) if chain and not stopped else (None, 0)
    units, units_ended = len(tickets) + chain, ended + (chain if run_ended else 0)
    to_end = domain.seconds_to_end(durations, units - units_ended, sum(t.elapsed_seconds for t in running) + chain_seconds)
    # A unit that still runs never ends the estimate: the ETA is `-` and not 0 seconds (#196).
    unit_runs = bool(running) or chain_phase is not None
    chains = _chains(events[folded.chain.start :], plan, stacks, activity, now, stopped, run_ended) if chain else []
    state, note = _state(folded.status, live, pause_request, journal.parent.name, folded.unresumable)
    return Snapshot(
        run_id=str(start["run_id"]), state=state, time=now,
        elapsed_seconds=seconds_between(event_time(start), now), tickets_total=len(tickets), tickets_ended=ended,
        percent=domain.percent_complete(units_ended, [t.phase for t in running if t.phase] + ([chain_phase] if chain_phase else []), units),
        eta=None if to_end is None or (to_end == 0 and unit_runs) or not live or (at_stop and not run_ended) else now + timedelta(seconds=to_end),
        tokens_new=None if activity is None else sum(t.tokens_new or 0 for t in tickets),
        tokens_cache=None if activity is None else sum(t.tokens_cache or 0 for t in tickets), tickets=tuple(tickets),
        phase=chain_phase, note=note, mode=str(start["mode"]),
        paused_before=_paused_before(folded.status), plan=_plan(events),
        chains=tuple(chains),
    )


def events_by_ticket(events: list[dict[str, object]], plan: list[str]) -> dict[str, list[dict[str, object]]]:
    """The events of each ticket of `plan`, in order, from one pass over `events`."""
    owns: dict[str, list[dict[str, object]]] = {ticket: [] for ticket in plan}
    for event in events:
        own = owns.get(event.get("ticket"))  # type: ignore[arg-type]
        if own is not None:
            own.append(event)
    return owns


def _has_live_process(journal: Path, first: dict[str, object] | None = None) -> bool:
    """True when a process works on the run of `journal`. A lock file that cannot be read does not show that the process stopped.

    `first` is the first event of the journal when the caller read it already (#350): the check does not open the journal.
    """
    try:
        return has_live_process(journal, first)
    except RunsError:
        return True


def _paused_before(stop: run_state.RunStatus) -> tuple[str, str] | None:
    """The ticket and the point of a pause (`before` of its `paused` event), or None when the run is not paused."""
    if stop.kind != run_state.PAUSED or stop.event is None:
        return None
    return str(stop.event.get("ticket")), str(stop.event.get("before"))


def _state(
    stop: run_state.RunStatus, live: bool, requested: bool, run_id: str, unresumable: run_state.Unresumable | None = None,
) -> tuple[str, str | None]:
    """The state of a run and its note, from the status of its run state (`stop`, ADR 0009).

    `live` is True when the run has a live process (a run that ended, paused or halted has one for this function), and
    `requested` when a pause request waits. The state is the `result` of its `run-end`, `paused`, `halted`, `pausing` while
    a pause request waits and the run is live, `running` while it is live, or `stopped` when it has no live process. The
    note of a halted run is `_halt_note`. The note of a stopped run is `_stopped_note`, or `journal.crash_note` when a crash
    stopped it (#281), or `journal.setup_problem_note` when the check of the setup stopped it (#296). The note of an
    abandoned run is `journal.abandon_note`: the state and the reason (#201). A run that cannot be resumed (`unresumable`, #358)
    has `_unresumable_note` in each state but abandoned and live, also with no `run-end` (a kill after `invariant-violation`).
    Otherwise the note is `_PAUSE_REQUEST_NOTE` when a pause request waits and no process is live, and None otherwise.
    """
    kind, event = stop.kind, stop.event
    fact = _unresumable_note(unresumable) if unresumable is not None else None
    if kind == run_state.HALTED:
        return HALTED, fact or _halt_note(event)  # type: ignore[arg-type]
    if kind in (run_state.ENDED, run_state.PAUSED):
        note = fact or (_PAUSE_REQUEST_NOTE if requested else None)  # an abandoned run gets the note of the abandon below
        if kind == run_state.PAUSED:
            return PAUSED, note
        if event["result"] == ABANDONED:  # type: ignore[index]
            return ABANDONED, abandon_note(event)  # type: ignore[arg-type]
        return str(event["result"]), note  # type: ignore[index]
    if live:
        return (PAUSING, None) if requested else (RUNNING, None)
    if fact is not None:
        return STOPPED, fact
    removes = "; the resume removes the pause request that waits" if requested else ""
    if kind == run_state.CRASHED:
        return STOPPED, crash_note(event, run_id) + removes  # type: ignore[arg-type]
    if kind == run_state.SETUP_PROBLEM:
        return STOPPED, setup_problem_note(event, run_id)  # type: ignore[arg-type]
    return STOPPED, _stopped_note(run_id, requested)


def _stopped_note(run_id: str, requested: bool = False) -> str:
    """The note of a stopped run: no process works on it, and the command that goes on with it."""
    note = f"no process works on the run; go on with delegate run --resume {run_id}"
    return note + ("; the resume removes the pause request that waits" if requested else "")


def _unresumable_note(found: run_state.Unresumable) -> str:
    """The note of a run that cannot be resumed (#358): why, and that the person starts a new run."""
    return f"cannot be resumed: {found.reason}; start a new run"


def _halt_note(halt: dict[str, object]) -> str:
    """The note of a halted run: where the harness failed, and the message of the failure."""
    return f"halted at the {halt['role']} of ticket {halt['ticket']}: {halt['message']}"


def snapshot(
    repo: Path, run_id: str | None, vcs: VersionControl, reader_of: ReaderOf, now: datetime, *, location: StateLocation | None = None,
) -> Snapshot:
    """The snapshot of a run of the repository: the run with that id, or the newest run when `run_id` is None.

    A run in progress appends to its journal, so a last line with no newline is
    left for the next snapshot. Raise RunsError when there is no such run, and
    JournalError when its journal is not valid. A stream that cannot be read is
    not an error: the ticket of that stream has no tokens, and no activity when
    its agent runs now. The stream of an agent that runs now can be missing: the
    harness has not opened it yet, so the agent has 0 tokens. `location` is the location that the command looked up
    (see `runs.locate`), or None to look it up here.
    """
    return snapshot_of_journal(journal_of(repo, run_id, vcs, location=location), reader_of, now)


def snapshot_of_journal(journal: Path, reader_of: ReaderOf, now: datetime) -> Snapshot:
    """The snapshot of the run of the journal file `journal`, as `snapshot` gives it, with no lookup of the repository.

    `delegate run` knows the journal of its run, so its end lines need no git process (#239).
    """
    events, _ = read_new_events(journal, 0, 1)
    return snapshot_of(events, journal, reader_of, now)


def liveness(seconds: int, idle_limit: int | None, spin: int | str | None) -> tuple[str, str]:
    """The paint role and the text of the time since the last activity of an agent (#147).

    At `idle_limit` seconds or more, it is the warning `⚠ idle <time>` in `warn`. Else, with no `spin`, it is the text
    of a snapshot: `active <time> ago` in `now`. A live view that animates gives the frame of its spinner in `spin`:
    under `_QUIET_SECONDS`, the text is that frame of the spinner, in `now`, and after it `quiet <time>` in `dim`.
    `spin` can also be the text of the cell of the spinner, which the view puts in the place of each frame (#351).
    """
    if idle_limit is not None and seconds >= idle_limit:
        return "warn", f"{output.glyphs().idle} idle {format_duration(seconds)}"
    if spin is None:
        return "now", f"active {format_duration(seconds)} ago"
    if seconds < _QUIET_SECONDS:
        frames = output.spinner()
        return "now", spin if isinstance(spin, str) else frames[spin % len(frames)]
    return "dim", f"quiet {format_duration(seconds)}"



def newest_blockers(events: list[dict[str, object]]) -> dict[str, list[str]] | None:
    """The blockers of each ticket in the `plan` field of the newest `plan-change`, or else of `run-start` (#107).

    None when that event records no plan: an earlier version of the engine wrote only the ticket ids.
    """
    records = [event.get("plan") for event in events if event["event"] == "plan-change"] or [events[0].get("plan")]
    if records[-1] is None:
        return None
    return {str(r["id"]): [str(b) for b in r["blocked_by"]] for r in records[-1]}  # type: ignore[union-attr, index]


def _plan(events: list[dict[str, object]]) -> tuple[tuple[str, tuple[str, ...]], ...] | None:
    """The newest plan of a run for `status --json`: each ticket id in plan order, with its blockers as recorded."""
    blockers = newest_blockers(events)
    if blockers is None:
        return None
    return tuple((ticket, tuple(blockers.get(ticket, ()))) for ticket in plan_ids(events))
