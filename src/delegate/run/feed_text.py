"""The text of the lines of the feed of `delegate watch` (#363).

A part of a line is a `Span`: the role of its paint (see `output.styled`) and its text. Each function here makes the parts
of one kind of line from an event and the few facts that the live view keeps for it: the seconds since its agent started,
its tokens, the id of the run. The functions have no state and know no terminal, so a test reads them with no frame.
`delegate.run.live` keeps the frame and the grouping of the feed: it decides which line joins which, and it paints and
places the parts.
"""

from __future__ import annotations

from pathlib import Path

from . import output, status_model
from .events import JOURNAL_CHANGED
from .journal import crash_where

# The text between two parts of a line of the feed.
SEPARATOR: str = " · "

# A part of a line of the feed: the roles of its paint (see `output.styled`), and its text.
Span = tuple[str, str]
JOIN: Span = ("", SEPARATOR)

# The events whose line names the stack of a chain, when the ticket of the event ends a chain.
CHAIN_KINDS: frozenset[str] = frozenset({"verify-start", "fixup-start", "fixup-refused"})


def duration(seconds: float) -> str:
    """A duration in the short form of the feed: `45s`, `36m`, or `10h42m`."""
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m"
    return f"{whole // 3600}h{whole % 3600 // 60:02d}m"


def agent_result(role: str, ended: str, seconds: float | None, tokens: str | None) -> list[Span]:
    """The result of an agent: its role and how it ended, the time since it started, and its tokens in `dim`."""
    spans: list[Span] = [("", f"{role} {ended}")]
    if seconds is not None:
        spans += [JOIN, ("", duration(seconds))]
    if tokens is not None:
        spans += [JOIN, ("dim", tokens)]
    return spans


def report(event: dict[str, object]) -> str:
    """The text of the line of a report event: `report ok`, `report blocked`, or `report not valid`."""
    if not event["valid"]:
        return "report not valid"
    if event.get("status") not in (None, "committed"):
        return f"report {event['status']}"
    return "report ok"


def gate_item(event: dict[str, object], kind: str, seconds: float | None) -> tuple[str, bool]:
    """The text of one gate of a round, and whether it is red. `seconds` is the time that the gate ran, or None."""
    red = kind == "gate-result" and not event["green"]
    if kind == "gate-skip":
        return f"{output.gate_name_of(event)} skipped", False
    item = output.gate_name_of(event) + (f" {duration(seconds)}" if seconds is not None else "")
    if red:
        item += " timed out" if event.get("timed_out") else " red"
    return item, red


def gates(items: list[tuple[str, bool]]) -> list[Span]:
    """The parts of the line of the gates of a round: `gates green` or `gates red`, and each gate in a list."""
    listed: list[Span] = []
    for index, (text, red) in enumerate(items):
        listed += ([("dim", ", ")] if index else []) + [("bad" if red else "dim", text)]
    verdict: Span = ("bad", "gates red") if any(red for _, red in items) else ("ok", "gates green")
    return [verdict, ("dim", " ("), *listed, ("dim", ")")]


def verdict(event: dict[str, object]) -> list[Span]:
    """The parts of the line of a verdict: the mark of an ACCEPT, or the verdict and its count of findings in `warn bold`."""
    if event["verdict"] == "ACCEPT":
        return [("ok", output.glyphs().built), ("", " "), ("bold", "ACCEPT")]
    findings = len(event.get("findings") or [])  # type: ignore[arg-type]
    return [("warn bold", f"{event['verdict']}{SEPARATOR}{findings} finding{'s' if findings != 1 else ''}")]


def verdict_subject(event: dict[str, object]) -> str | None:
    """The subject of the line of the verdict of a chain (`stack python`), or None for the verdict of a ticket."""
    return f"stack {event['stack']}" if "stack" in event else None


def step_end(event: dict[str, object]) -> str:
    """The text of the end of a step: its state, and for a state other than built, its reason."""
    state = str(event["state"])
    return state if state == status_model.BUILT or not event.get("reason") else f"{state}{SEPARATOR}{event['reason']}"


def run_end(event: dict[str, object], seconds: float, tokens: str | None) -> list[Span]:
    """The parts of the line of the end of a run: its result, how long it ran, its reason, and its tokens in `dim`."""
    result = str(event["result"])
    spans: list[Span] = [("ok bold" if result == status_model.BUILT else "bad", f"run {result}"), JOIN, ("", duration(seconds))]
    if event.get("reason"):
        spans += [JOIN, ("", f"reason: {event['reason']}")]
    if tokens is not None:
        spans += [JOIN, ("dim", tokens)]
    return spans


def stall(minutes: float) -> list[Span]:
    """The parts of the line of a stall of the run."""
    return [("", f"{output.glyphs().failed} stall{SEPARATOR}no change for {minutes:g}m")]


def time_limit(minutes: float) -> list[Span]:
    """The parts of the line of the time limit of the watch."""
    return [("", f"time limit{SEPARATOR}{minutes:g}m used{SEPARATOR}the run goes on")]


def warning(subject: str, problem: str) -> list[Span]:
    """The parts of the line of a problem of the run: the glyph of a failure, the subject when there is one, and the problem."""
    return [("", f"{output.glyphs().failed} " + (f"{subject} " if subject else "") + problem)]


def own_line(kind: str, event: dict[str, object], run_id: str) -> tuple[str, list[Span]]:
    """The paint of the whole line, and the parts, of an event that has a line of its own.

    A pause is `warn`, a halt and a crash are `bad`: these lines are of the run and have no subject. The paint of the other
    lines is by part. `run_id` is the id of the
    run, for the command that goes on with it. An event that this version does not know shows its name.
    """
    if kind == "paused":
        return "warn", [("", paused(event, run_id))]
    if kind == "halted":
        return "bad", [("", halted(event, run_id))]
    if kind == "crashed":
        return "bad", [("", crashed(event, run_id))]
    if kind == "fixup-start":
        return "", [("warn", f"fix-up round {event['round']} started")]
    if kind == "continuation":
        return "", [("warn", f"continuation {event['count']} of {event['limit']}"), JOIN, ("", str(event["trigger"]))]
    return "", [("", text(kind, event))]


def paused(event: dict[str, object], run_id: str) -> str:
    """The line of a pause: where the run stopped, and the command that goes on with it."""
    ticket, before = event.get("ticket"), event.get("before")
    if before == "gate" and event.get("command"):
        where = f"the gate {output.gate_name_of(event)} of ticket {ticket}"
    elif before == "verifier":
        where = "the verifier of " + (f"stack {event['stack']}" if event.get("stack") else f"ticket {ticket}")
    else:
        where = f"ticket {ticket}"
    return f"paused{SEPARATOR}before {where}{SEPARATOR}go on with delegate run --resume {run_id}"


def halted(event: dict[str, object], run_id: str) -> str:
    """The line of a halt: where the harness failed, its message, and the command that goes on with the run."""
    where = f"the {event['role']} of ticket {event['ticket']}"
    return f"halted{SEPARATOR}{where}{SEPARATOR}{event['message']}{SEPARATOR}go on with delegate run --resume {run_id}"


def crashed(event: dict[str, object], run_id: str) -> str:
    """The line of a crash (#281): where it stopped the run, the error, the name of the file of its traceback, and the
    command that goes on with the run. The line cuts each path to its name (`output.without_paths`)."""
    where = crash_where(event.get("ticket"), event.get("phase"))
    parts = ["crashed", where, f"{event['error']}: {output.without_paths(str(event['message']))}"]
    if event.get("traceback"):
        parts.append(f"traceback {Path(str(event['traceback'])).name}")
    again = f" --retry {event.get('ticket')}" if event.get("retry") else ""
    return SEPARATOR.join([*parts, f"go on with delegate run --resume {run_id}{again}"])


def text(kind: str, event: dict[str, object]) -> str:
    """The text of an event that has a line of its own, with no paint."""
    if kind == "run-start":
        count = len(event["tickets"])  # type: ignore[arg-type]
        return f"run {event['run_id']} started{SEPARATOR}{event['mode']}{SEPARATOR}{count} ticket{'s' if count != 1 else ''}"
    if kind == "resume":
        return "run resumed"
    if kind == "step-start":
        return f"started{SEPARATOR}{event['agent']}"
    if kind == "retry":
        return f"retry at the {event['phase']}"
    if kind == "stack-change":
        return output.stack_change_line(event)
    if kind == "continuation-limit":
        return f"continuation limit {event['limit']} reached"
    if kind == "hotspot-finding":
        count = len(event["matches"])  # type: ignore[arg-type]
        return f"hotspot finding{SEPARATOR}{count} path{'s' if count != 1 else ''}"
    if kind == "rebase":
        return f"rebase {event['result']}"
    if kind == "verify-start":
        if "tickets" in event:
            tickets = ", ".join(f"#{t}" for t in event["tickets"])  # type: ignore[attr-defined]
            return f"verifier started ({tickets})"
        return "verifier started" if not event["round"] else f"scoped verifier started{SEPARATOR}round {event['round']}"
    if kind == "invariant-violation":
        if event.get("kind") == JOURNAL_CHANGED:
            return "invariant violation: another writer changed the journal"
        return "invariant violation: the verifier changed the worktree"
    if kind == "fixup-refused":
        return f"fix-up round {event['round']} refused"
    if kind == "skip":
        return f"skipped{SEPARATOR}{event['reason']}" if event.get("reason") else "skipped"
    if kind == "unskip":
        return f"not skipped{SEPARATOR}{event['outlook']}"
    return kind
