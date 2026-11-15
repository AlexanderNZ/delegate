"""The values of a snapshot of a run: its states, the snapshot, the snapshot of a ticket and of a chain, the readers of an event stream, and the short form of a duration and of a token count (#335).

`delegate.run.status` makes the snapshot from the journal (`snapshot`), and `status_table` and `status_picker` draw it. This module
holds only the values that the three share, so none of them imports another to reach a value. It reads no file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..ports.harness import Activity

# The states that a ticket shows. A ticket with no event is pending, a ticket
# with `step-start` and no `step-end` is running, and the rest follow `step-end`
# and `skip`.
PENDING: str = "pending"
RUNNING: str = "running"
BUILT: str = "built"
FAILED: str = "failed"
SKIPPED: str = "skipped"

# The states of the chain row of an `economy` stack, beside `running` (#196).
ACCEPTED: str = "accepted"
REJECTED: str = "rejected"

# The states of a run that has no `run-end`, beside `running`. A run is `pausing` from a pause request until the engine
# reads it at a step boundary, and `paused` from its `paused` event until a resume (see `delegate.run.pause`).
PAUSING: str = "pausing"
PAUSED: str = "paused"
# A failure of the harness stopped the run, and the run wrote no `run-end`: a resume goes on (#177).
HALTED: str = "halted"
# A run with no `run-end`, no pause, no halt and no live process: a crash, a kill, Ctrl-C, or a stop of the machine. A resume goes on (#193).
# A run whose newest `crashed` comes after its newest `resume` is stopped also while its process ends (#281).
STOPPED: str = "stopped"


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
    `gate_name` is the `name` of that `gate-start` (#223), or None: the table shows it in place of the short name of the
    command, and `to_json` leaves it out.
    `gate_idle_seconds` is the seconds since the log file of that gate last changed. It is None when no gate runs, or
    when the log file cannot be read. It needs no reader of the streams.

    The last five fields are for the ticket table, and `to_json` leaves them out. `reason` is the reason of the
    `step-end` or the `skip` of the ticket. `traceback` is the `traceback` of that `step-end`, the file that holds the
    traceback of a crash (#194), or None. `fixup_round` is the round of the newest `fixup-start` of the step, or 0.
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
    traceback: str | None = None
    fixup_round: int = 0
    continuation: tuple[int, int] | None = None
    gate_count: tuple[int, int] | None = None
    gate_name: str | None = None


@dataclass(frozen=True)
class ChainSnapshot:
    """The verification of one stack of an `economy` chain in a snapshot: one chain row of the table (#196).

    The row exists from the first gate, verifier or fix-up of the chain of the stack. `state` is `running`, `accepted` or
    `rejected`. `phase` is `gates`, `verifier` or `fix-up` while the chain of the stack runs, and None after it ended.
    `elapsed_seconds` is the time from the start of the verification of the stack to now, or to its end.

    `gate_command` and `gate_idle_seconds` are as `TicketSnapshot.gate` and `gate_idle_seconds`, for the gate of the
    chain. `tokens_new` and `tokens_cache` count the agents of the chain (the verifier and the fix-up specialist), and
    are None as for a ticket. `verdict` is the verdict that ended the chain, or None while it runs and when red gates
    ended it.

    The last three fields are for the table, and `to_json` leaves them out. `idle_seconds` is the seconds since the
    activity of the agent of the chain that runs now. `fixup_round` is the round of the newest `fixup-start`. `after` is
    the id of the last ticket of the stack in plan order: the row goes under the rows of that ticket. `gate_name` is the
    name that the workflow gives to the gate that runs (#223), and `to_json` leaves it out too.
    """

    stack: str
    phase: str | None
    state: str
    elapsed_seconds: int
    gate_command: str | None
    gate_idle_seconds: int | None
    tokens_new: int | None
    tokens_cache: int | None
    verdict: str | None
    idle_seconds: int | None = None
    fixup_round: int = 0
    after: str = ""
    gate_name: str | None = None


@dataclass(frozen=True)
class Snapshot:
    """One snapshot of a run. `time` is the time of the snapshot: now, the time of `run-end` for a run that ended, or the
    time of `paused` for a run that is paused. `state` is the result of a run that ended, `paused`, `halted`, `pausing`, or `running`.

    `phase` is the phase of the verification of the chain while it runs in `economy` mode, and None otherwise.

    `note` is the text that `status` gives to a pause request that waits when the run is not active, and None otherwise.

    `mode` is the mode of the run. `paused_before` is the ticket and the `before` field of the `paused` event of a run
    that is paused, and None otherwise. `to_json` leaves the two out.

    `plan` is each ticket id of the newest plan, in plan order, with the blockers that the plan records for it. It is
    None when the journal records no plan (an earlier version of the engine).

    `chains` is the chain row of each stack that started its verification in `economy` mode (#196), and empty in any
    other mode. It is the last field.

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
    chains: tuple[ChainSnapshot, ...] = ()

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
            "chains": [
                {
                    "stack": c.stack, "phase": c.phase, "state": c.state, "elapsed_seconds": c.elapsed_seconds,
                    "gate_command": c.gate_command, "gate_idle_seconds": c.gate_idle_seconds, "tokens_new": c.tokens_new,
                    "tokens_cache": c.tokens_cache, "verdict": c.verdict, "fixup_round": c.fixup_round,
                }
                for c in self.chains
            ],
        }


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
