"""The live view of `delegate watch` on a terminal: the progress block of the snapshot at the top, and a feed of the events below.

`LiveView` is a sink of `delegate.run.watch.follow`. At each poll it makes a
frame: the lines of the snapshot (`status.view_lines`, the same snapshot as
`delegate status`), a blank line, and the lines of the feed. The command (in
`delegate.cli.watch`) draws the frame in place on the terminal.

A line of the feed is short: the local time (`HH:MM`), the ticket, and what
happened. It shows no path and no `None`. The view joins the events that belong
together into one line: the result of an agent with its report, the results of
the gates of one round, and a verdict or the gates with the end of the step. A
problem and a stall are lines that start with `PROBLEM`.

The view reads the event streams only through the reader that the command gives
(the `activity` method of the adapter of the run), so this module never imports
an adapter.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import status
from .journal import JournalError

# The mark at the start of a line of the feed for a problem and for a stall, and the mark of a verdict that accepts.
PROBLEM: str = "✗"
ACCEPTED: str = "✓"

# The text between two parts of a line of the feed.
SEPARATOR: str = " · "

# An absolute path in a line of the feed. The view shows only its last part, the name of the file.
ABSOLUTE_PATH = re.compile(r"(?<![\w.~-])/(?:[^\s/'\"]+/)*([^\s/'\"]*)")

# The words at the start of a gate command that only start the tool that does the work. The view names the gate by the
# first word after them that is not an option and not a variable, so `nix develop -c python3 -m pytest -q` is `pytest`.
LAUNCHERS: frozenset[str] = frozenset({"nix", "develop", "uvx", "uv", "run", "npx", "env", "exec", "python", "python3"})

# A word that sets a variable of the environment of a command, as `NAME=value`. A version pin, as `tool==1.0`, is not one.
VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=(?!=)")

# The role of the agent of each result event (the `role` of its `agent-start`), and the result event that each report event belongs to.
AGENT_OF_RESULT: dict[str, str] = {"adapter-result": "specialist", "fixup-result": "fix-up", "verify-result": "verifier"}
RESULT_OF_REPORT: dict[str, str] = {"report-validation": "adapter-result", "fixup-report": "fixup-result", "verify-report": "verify-result"}

# The events that add no line of their own: their facts are in the line of another event, or in the block.
SILENT: frozenset[str] = frozenset({"agent-start", "gate-start", "idle-timeout", "run-branch-advance", "lock-broken"})

# The kinds of a line that waits for the next event of its ticket, to join it.
AGENT, GATES, VERDICT = "agent", "gates", "verdict"


def local_time(when: datetime) -> str:
    """The local time of the clock of this computer, as `HH:MM`."""
    return when.astimezone().strftime("%H:%M")


def duration(seconds: float) -> str:
    """A duration in the short form of the feed: `45s`, `36m`, or `10h42m`."""
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m"
    return f"{whole // 3600}h{whole % 3600 // 60:02d}m"


def gate_name(command: str) -> str:
    """The short name of a gate command: the name of the first word that is not a launcher, with no version.

    For example `nix develop -c python3 -m pytest -q` is `pytest`, and `uvx "zensical==0.0.2" build` is `zensical`.
    """
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    for word in words:
        if word in LAUNCHERS or word.startswith("-") or VARIABLE.match(word):
            continue
        return re.split(r"[=<>@]", Path(word).name)[0] or word
    return command


def without_paths(text: str) -> str:
    """The text with each absolute path cut to its last part."""
    return ABSOLUTE_PATH.sub(lambda match: match.group(1), text)


@dataclass
class _Waiting:
    """A line of the feed that waits for the next event of its ticket: the result of an agent, gates, or a verdict."""

    kind: str
    time: datetime
    subject: str
    text: str
    gates: list[str] = field(default_factory=list)
    red: bool = False


class LiveView:
    """The sink of `follow` for a terminal. `frame` gives the lines to draw; `draw` gets them at each poll and at the end.

    `reader_of` gives the reader of the event streams for the adapter of the run, as for `delegate status`. `now` is the
    clock, in UTC. `size` gives the columns and the lines of the terminal.
    """

    def __init__(
        self, journal: Path, reader_of: status.ReaderOf, now: Callable[[], datetime],
        size: Callable[[], tuple[int, int]], draw: Callable[[list[str]], None],
    ) -> None:
        self._journal, self._reader_of, self._now, self._size, self._draw = journal, reader_of, now, size, draw
        self._events: list[dict[str, object]] = []
        self._feed: list[str] = []
        self._waiting: dict[str, _Waiting] = {}  # by ticket id, in the order the lines started
        self._agents: dict[tuple[str, str], datetime] = {}  # the start of the agent that runs, by ticket and role
        self._gates: dict[tuple[str, str], datetime] = {}  # the start of the gate that runs, by ticket and command
        self._chain: dict[str, str] = {}  # the stack of the chain verification, by the ticket that its events name
        self._reader: status.StreamReader | None = None

    # The sink of `follow`.

    def event(self, event: dict[str, object], reported: bool, problem: str | None) -> None:
        self._events.append(event)
        if event["event"] == "run-start":
            self._reader = self._reader_of(str(event["adapter"]))
        before = len(self._feed)
        self._read(event, problem)
        if not reported:  # an event at or before `--from` adds no line, and no line that waits
            del self._feed[before:]
            self._waiting.clear()

    def verdict(self, event: dict[str, object]) -> None:
        return None  # the verdict is the last line of the feed already

    def stall(self, minutes: float, last_event: dict[str, object] | None) -> None:
        self._flush_all()
        self._add(self._now(), "", f"{PROBLEM} stall{SEPARATOR}no change for {minutes:g}m")

    def time_limit(self, minutes: float) -> None:
        self._flush_all()
        self._add(self._now(), "", f"time limit{SEPARATOR}{minutes:g}m used{SEPARATOR}the run goes on")

    def poll(self) -> None:
        columns, rows = self._size()
        self._draw(self.frame(columns, rows, final=False))

    def end(self) -> None:
        self._flush_all()
        columns, rows = self._size()
        self._draw(self.frame(columns, rows, final=True))

    # The frame.

    def frame(self, columns: int, rows: int, final: bool) -> list[str]:
        """The lines to draw: the block, a blank line, and the feed, each cut to `columns`.

        A frame of a watch that goes on fits in `rows`, with the last line free for the cursor: it keeps the newest
        lines of the feed. The last frame keeps the whole feed, so the terminal scrolls it.
        """
        block = self._block(columns)
        feed = self._feed + [self._line(w.time, w.subject, w.text) for w in self._waiting.values()]
        if not final:
            room = max(0, rows - 1 - len(block) - 1)
            feed = feed[-room:] if room else []
        width = max(columns, 1)
        return block + ([""] if feed else []) + [line[:width] for line in feed]

    def _block(self, columns: int) -> list[str]:
        snap = self._snapshot()
        return [] if snap is None else status.view_lines(snap, columns)

    def _snapshot(self) -> status.Snapshot | None:
        """The snapshot of the events so far, or None when the journal does not start with `run-start`."""
        try:
            return status.snapshot_of(self._events, self._journal, self._reader_of, self._now())
        except JournalError:
            return None

    # The feed.

    def _line(self, when: datetime, subject: str, text: str) -> str:
        return without_paths(f"{local_time(when)}  " + (f"{subject} " if subject else "") + text)

    def _add(self, when: datetime, subject: str, text: str) -> None:
        self._feed.append(self._line(when, subject, text))

    def _flush(self, ticket: str) -> None:
        waiting = self._waiting.pop(ticket, None)
        if waiting is not None:
            self._add(waiting.time, waiting.subject, waiting.text)

    def _flush_all(self) -> None:
        for ticket in list(self._waiting):
            self._flush(ticket)

    def _join(self, ticket: str, when: datetime, subject: str, text: str) -> None:
        """Add the line of an event that ends what waits for its ticket: the waiting text, then `text`."""
        waiting = self._waiting.pop(ticket, None)
        if waiting is None:
            self._add(when, subject, text)
        else:
            self._add(when, waiting.subject, waiting.text + SEPARATOR + text)

    def _wait(self, ticket: str, waiting: _Waiting) -> None:
        self._flush(ticket)
        self._waiting[ticket] = waiting

    def _tokens(self, stream: object) -> str | None:
        """The tokens of an event stream in the short form, or None when the run has no reader or the stream cannot be read."""
        if self._reader is None or stream is None:
            return None
        try:
            return f"{status.format_tokens(self._reader(Path(str(stream))).tokens)} tok"
        except Exception:  # noqa: BLE001 - the view only shows data, as `delegate status` does
            return None

    def _read(self, event: dict[str, object], problem: str | None) -> None:
        """Read one event: add the lines that it ends to the feed, and keep the line that waits for the next event."""
        kind, when = str(event["event"]), datetime.fromisoformat(str(event["time"]))
        ticket = str(event.get("ticket", ""))
        subject = f"#{ticket}" if "ticket" in event else ""
        if kind == "agent-start":
            self._agents[(ticket, str(event["role"]))] = when
        elif kind == "gate-start":
            self._gates[(ticket, str(event["command"]))] = when
        elif kind == "verify-start" and "stack" in event:
            self._chain[ticket] = str(event["stack"])  # the events of the chain verification name its last ticket
        if problem is not None:
            self._flush(ticket)
            self._add(when, "", f"{PROBLEM} " + (f"{subject} " if subject else "") + problem)
            return
        if kind in SILENT:
            return
        if kind in AGENT_OF_RESULT:
            role = AGENT_OF_RESULT[kind]
            started = self._agents.pop((ticket, role), None)
            parts = [f"{role} {event['end_state']}"]
            if started is not None:
                parts.append(duration((when - started).total_seconds()))
            tokens = self._tokens(event.get("event_stream"))
            if tokens is not None:
                parts.append(tokens)
            self._wait(ticket, _Waiting(AGENT, when, self._chain_subject(ticket, subject), SEPARATOR.join(parts)))
            return
        if kind in RESULT_OF_REPORT:
            text = "report ok" if event["valid"] else "report not valid"
            if event["valid"] and event.get("status") not in (None, "committed"):
                text = f"report {event['status']}"
            waiting = self._waiting.get(ticket)
            if waiting is not None and waiting.kind == AGENT:
                self._join(ticket, when, subject, text)
            else:
                self._flush(ticket)
                self._add(when, self._chain_subject(ticket, subject), text)
            return
        if kind in ("gate-result", "gate-skip"):
            command = str(event["command"])
            started = self._gates.pop((ticket, command), None)
            if kind == "gate-skip":
                item = f"{gate_name(command)} skipped"
            else:
                item = gate_name(command) + (f" {duration((when - started).total_seconds())}" if started is not None else "")
                item += "" if event["green"] else " red"
            waiting = self._waiting.get(ticket)
            if waiting is None or waiting.kind != GATES:
                waiting = _Waiting(GATES, when, subject, "")
                self._wait(ticket, waiting)
            waiting.gates.append(item)
            waiting.red = waiting.red or (kind == "gate-result" and not event["green"])
            waiting.time = when
            waiting.text = f"gates {'red' if waiting.red else 'green'} ({', '.join(waiting.gates)})"
            return
        if kind == "verdict":
            findings = len(event.get("findings") or [])  # type: ignore[arg-type]
            text = f"{ACCEPTED} ACCEPT" if event["verdict"] == "ACCEPT" else f"{event['verdict']}{SEPARATOR}{findings} finding{'s' if findings != 1 else ''}"
            chain = f"stack {event['stack']}" if "stack" in event else None
            self._wait(ticket, _Waiting(VERDICT, when, chain or subject, text))
            return
        if kind == "step-end":
            state = str(event["state"])
            text = state if state == status.BUILT or not event.get("reason") else f"{state}{SEPARATOR}{event['reason']}"
            self._join(ticket, when, subject, text)
            return
        if kind == "run-end":
            self._run_end(event, when)
            return
        self._flush(ticket)
        chain = kind in ("verify-start", "fixup-start", "fixup-refused")
        self._add(when, self._chain_subject(ticket, subject) if chain else subject, self._text(kind, event))

    def _chain_subject(self, ticket: str, subject: str) -> str:
        return f"stack {self._chain[ticket]}" if ticket in self._chain else subject

    def _run_end(self, event: dict[str, object], when: datetime) -> None:
        """The line of the end of the run joins the newest line that waits: the verdict of a chain, for example."""
        newest = list(self._waiting)[-1] if self._waiting else None
        for ticket in list(self._waiting):
            if ticket != newest:
                self._flush(ticket)
        start = datetime.fromisoformat(str(self._events[0]["time"])) if self._events and self._events[0]["event"] == "run-start" else when
        parts = [f"run {event['result']}", duration((when - start).total_seconds())]
        snap = self._snapshot()
        if snap is not None and snap.tokens is not None:
            parts.append(f"{status.format_tokens(snap.tokens)} tok")
        text = SEPARATOR.join(parts)
        if newest is None:
            self._add(when, "", text)
        else:
            waiting = self._waiting.pop(newest)
            self._add(when, waiting.subject, waiting.text + SEPARATOR + text)

    def _text(self, kind: str, event: dict[str, object]) -> str:
        """The text of an event that has a line of its own. An event that this version does not know shows its name."""
        if kind == "run-start":
            count = len(event["tickets"])  # type: ignore[arg-type]
            return f"run {event['run_id']} started{SEPARATOR}{event['mode']}{SEPARATOR}{count} ticket{'s' if count != 1 else ''}"
        if kind == "resume":
            return "run resumed"
        if kind == "step-start":
            return f"started{SEPARATOR}{event['agent']}"
        if kind == "retry":
            return f"retry at the {event['phase']}"
        if kind == "continuation":
            return f"continuation {event['count']} of {event['limit']}{SEPARATOR}{event['trigger']}"
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
            return "invariant violation: the verifier changed the worktree"
        if kind == "fixup-start":
            return f"fix-up round {event['round']} started"
        if kind == "fixup-refused":
            return f"fix-up round {event['round']} refused"
        if kind == "skip":
            return f"skipped{SEPARATOR}{event['reason']}" if event.get("reason") else "skipped"
        return kind
