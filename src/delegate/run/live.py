"""The live view of `delegate watch` on a terminal: the run line and the ticket table at the top, and below a divider
the feed of the ticket that runs now (#116).

`LiveView` is a sink of `delegate.run.watch.follow`. At each poll it makes a frame: the run line and the ticket table
(`status.view_lines`, of the same snapshot as `delegate status`, with the tree of `status.plan_tree`), a divider, and
the lines of the feed. The command (in `delegate.cli.watch`) draws the frame in place on the terminal. The spec is
`design/design_handoff_delegate_terminal_output/docs/components.md`.

A line of the feed is short: the local time (`HH:MM`), the ticket, and what happened. It shows no path and no `None`.
The view joins the events that belong together into one line: the result of an agent with its report, the results of
the gates of one round, and a verdict or the gates with the end of the step. A problem and a stall are lines that start
with the glyph of a failure (`✗`).

The feed keeps only the lines of the ticket that runs now. Its `step-start` (or a `retry`) starts a new group, so the
lines of a ticket that ended stay until the next ticket starts. In `economy` mode the verification of the chain is one
group, and its lines name the stack. While an agent runs, the last line of a frame tells it, with the time since its
last activity. Each part of a line has the paint of its role (`output.paint`), which is plain text when `NO_COLOR` is
set or standard output is not a terminal.

The view reads the event streams only through the reader that the command gives (the `activity` method of the adapter
of the run), so this module never imports an adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import domain, output, status
from .journal import JournalError

# The text between two parts of a line of the feed.
SEPARATOR: str = " · "

# The role of the agent of each result event (the `role` of its `agent-start`), and the result event that each report event belongs to.
AGENT_OF_RESULT: dict[str, str] = {"adapter-result": "specialist", "fixup-result": "fix-up", "verify-result": "verifier"}
RESULT_OF_REPORT: dict[str, str] = {"report-validation": "adapter-result", "fixup-report": "fixup-result", "verify-report": "verify-result"}

# The events that add no line of their own: their facts are in the line of another event, or in the table.
SILENT: frozenset[str] = frozenset({"agent-start", "gate-start", "idle-timeout", "run-branch-advance", "lock-broken", "pause-requested"})

# The kinds of a line that waits for the next event of its ticket, to join it.
AGENT, GATES, VERDICT = "agent", "gates", "verdict"

# The minutes with no activity after which the live tail of a running ticket warns, when `watch` has no `--stall-minutes`.
# The pane of the Claude Code plugin uses the same default.
IDLE_WARNING_MINUTES: float = 5

# A part of a line of the feed: the roles of its paint (see `output.styled`), and its text.
Span = tuple[str, str]
JOIN: Span = ("", SEPARATOR)


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


def _plain(spans: list[Span]) -> str:
    return "".join(text for _, text in spans)


@dataclass
class _Line:
    """A line of the feed. `role` paints the whole line, as for a problem. The subject of a `started` line is `now` and bold."""

    time: datetime
    subject: str
    spans: list[Span]
    role: str = ""
    started: bool = False

    def text(self) -> str:
        return f"{local_time(self.time)}  " + (f"{self.subject} " if self.subject else "") + _plain(self.spans)

    def painted(self) -> str:
        if self.role:
            return output.styled(self.role, self.text())
        subject = output.styled("now bold" if self.started else "dim", self.subject) + " " if self.subject else ""
        merged: list[Span] = []
        for role, text in self.spans:  # the parts of one role next to each other get one paint
            if merged and merged[-1][0] == role:
                merged[-1] = (role, merged[-1][1] + text)
            else:
                merged.append((role, text))
        return output.styled("dim", local_time(self.time)) + "  " + subject + "".join(output.styled(role, text) for role, text in merged)


@dataclass
class _Waiting:
    """A line of the feed that waits for the next event of its ticket: the result of an agent, gates, or a verdict."""

    kind: str
    time: datetime
    subject: str
    spans: list[Span]
    gates: list[tuple[str, bool]] = field(default_factory=list)  # the text of each gate, and whether it is red
    red: bool = False


class LiveView:
    """The sink of `follow` for a terminal. `frame` gives the lines to draw; `draw` gets them at each poll and at the end.

    `reader_of` gives the reader of the event streams for the adapter of the run, as for `delegate status`. `now` is the
    clock, in UTC. `size` gives the columns and the lines of the terminal. `stall_minutes` is the `--stall-minutes` of
    the watch: an agent with no activity for that time shows a warning in the table. With None, the time is
    `IDLE_WARNING_MINUTES`.
    """

    def __init__(
        self, journal: Path, reader_of: status.ReaderOf, now: Callable[[], datetime],
        size: Callable[[], tuple[int, int]], draw: Callable[[list[str]], None], stall_minutes: float | None = None,
    ) -> None:
        self._journal, self._reader_of, self._now, self._size, self._draw = journal, reader_of, now, size, draw
        self._idle_limit = int((IDLE_WARNING_MINUTES if stall_minutes is None else stall_minutes) * 60)
        self._events: list[dict[str, object]] = []
        self._feed: list[_Line] = []  # the lines of the group of the ticket that runs now
        self._groups = 0  # the count of the groups so far
        self._in_chain = False  # the group is the verification of an `economy` chain
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
        before, groups = len(self._feed), self._groups
        self._read(event, problem)
        if not reported:  # an event at or before `--from` adds no line, and no line that waits
            del self._feed[(0 if self._groups != groups else before):]
            self._waiting.clear()

    def verdict(self, event: dict[str, object]) -> None:
        return None  # the verdict is the last line of the feed already

    def stall(self, minutes: float, last_event: dict[str, object] | None) -> None:
        self._flush_all()
        self._add(self._now(), "", [("", f"{output.glyphs().failed} stall{SEPARATOR}no change for {minutes:g}m")], role="bad")

    def time_limit(self, minutes: float) -> None:
        self._flush_all()
        self._add(self._now(), "", [("", f"time limit{SEPARATOR}{minutes:g}m used{SEPARATOR}the run goes on")])

    def poll(self) -> None:
        columns, rows = self._size()
        self._draw(self.frame(columns, rows, final=False))

    def interrupted(self) -> None:
        return None  # `end` draws the last frame; the command restores the terminal and prints the line

    def end(self) -> None:
        self._flush_all()
        columns, rows = self._size()
        self._draw(self.frame(columns, rows, final=True))

    # The frame.

    def frame(self, columns: int, rows: int, final: bool) -> list[str]:
        """The lines to draw: the run line, a blank line, the table, the divider, and the feed, each cut to `columns`.

        A frame fits in `rows`, with the last line free for the cursor: the built rows of the table fold first, and the
        feed keeps its newest lines. The last frame keeps the whole feed, so the terminal scrolls it. A frame of a
        watch that goes on also tells each agent that runs now; the last frame does not.
        """
        width = max(columns, 1)
        room = max(0, rows - 1)
        snap = self._snapshot()
        feed = [*self._feed, *(_Line(w.time, w.subject, w.spans) for w in self._waiting.values())]
        if not final and snap is not None:
            feed += self._running(snap)
        block: list[str] = []
        if snap is not None:
            height = room - 1 - len(feed) if feed else room
            block = status.view_lines(snap, status.plan_tree(self._events), width, height, self._idle_limit)
        lines = [line.painted() for line in feed]
        if not final:
            block = block[:room]
            space = max(0, room - len(block) - 1)
            lines = lines[-space:] if space else []
        divider = [output.styled("dim", output.glyphs().rule * width)] if lines else []
        return [output.cut(line, width) for line in [*block, *divider, *lines]]

    def _running(self, snap: status.Snapshot) -> list[_Line]:
        """A line for each agent that runs now: its role, the time since it started, and the time since its last activity."""
        now = self._now()
        idle = {t.id: t.idle_seconds for t in snap.tickets}
        lines = []
        for (ticket, role), started in self._agents.items():
            text, paint = f"{role} running{SEPARATOR}{duration((now - started).total_seconds())}", "now"
            seconds = idle.get(ticket)
            if seconds is not None and seconds >= self._idle_limit:
                text, paint = text + f"{SEPARATOR}{output.glyphs().idle} idle {status.format_duration(seconds)}", "warn"
            elif seconds is not None:
                text += f"{SEPARATOR}active {status.format_duration(seconds)}"
            lines.append(_Line(now, self._chain_subject(ticket, f"#{ticket}"), [("", text)], role=paint))
        return lines

    def _snapshot(self) -> status.Snapshot | None:
        """The snapshot of the events so far, or None when the journal does not start with `run-start`."""
        try:
            return status.snapshot_of(self._events, self._journal, self._reader_of, self._now())
        except JournalError:
            return None

    # The feed.

    def _add(self, when: datetime, subject: str, spans: list[Span], role: str = "", started: bool = False) -> None:
        self._feed.append(_Line(when, subject, [(r, output.without_paths(text)) for r, text in spans], role, started))

    def _flush(self, ticket: str) -> None:
        waiting = self._waiting.pop(ticket, None)
        if waiting is not None:
            self._add(waiting.time, waiting.subject, waiting.spans)

    def _flush_all(self) -> None:
        for ticket in list(self._waiting):
            self._flush(ticket)

    def _new_group(self) -> None:
        """Start the group of the next ticket, or of the verification of the chain: the feed drops the lines before it."""
        self._flush_all()
        self._feed.clear()
        self._groups += 1

    def _join(self, ticket: str, when: datetime, subject: str, spans: list[Span]) -> None:
        """Add the line of an event that ends what waits for its ticket: the waiting parts, then `spans`."""
        waiting = self._waiting.pop(ticket, None)
        if waiting is None:
            self._add(when, subject, spans)
        else:
            self._add(when, waiting.subject, [*waiting.spans, JOIN, *spans])

    def _wait(self, ticket: str, waiting: _Waiting) -> None:
        self._flush(ticket)
        self._waiting[ticket] = waiting

    def _tokens(self, stream: object) -> str | None:
        """The tokens of an event stream in the short form, or None when the run has no reader or the stream cannot be read."""
        if self._reader is None or stream is None:
            return None
        try:
            found = self._reader(Path(str(stream)))
            return status.format_token_counts(found.tokens_new, found.tokens_cache)
        except Exception:  # noqa: BLE001 - the view only shows data, as `delegate status` does
            return None

    def _group(self, kind: str, event: dict[str, object]) -> None:
        """Start a new group at the step of a ticket, and at the first event of the verification of a chain."""
        if kind in ("step-start", "retry"):
            self._new_group()
            self._in_chain = False
            return
        chain_gate = kind in ("gate-start", "gate-skip") and event.get("phase") == domain.GATES_OF_CHAIN
        if not self._in_chain and (chain_gate or (kind == "verify-start" and "stack" in event)):
            self._new_group()
            self._in_chain = True

    def _read(self, event: dict[str, object], problem: str | None) -> None:
        """Read one event: add the lines that it ends to the feed, and keep the line that waits for the next event."""
        kind, when = str(event["event"]), datetime.fromisoformat(str(event["time"]))
        ticket = str(event.get("ticket", ""))
        subject = f"#{ticket}" if "ticket" in event else ""
        self._group(kind, event)
        if kind == "agent-start":
            # The newest agent of a ticket is the one that runs, as in the snapshot: the one before it ended.
            self._agents = {key: start for key, start in self._agents.items() if key[0] != ticket}
            self._agents[(ticket, str(event["role"]))] = when
        elif kind == "gate-start":
            self._gates[(ticket, str(event["command"]))] = when
        elif kind == "verify-start" and "stack" in event:
            self._chain[ticket] = str(event["stack"])  # the events of the chain verification name its last ticket
        elif kind == "step-end":
            for key in [key for key in self._agents if key[0] == ticket]:
                del self._agents[key]  # a crash ends the step with no result event
        elif kind in ("run-end", "paused"):
            self._agents.clear()
        if problem is not None:
            self._flush(ticket)
            self._add(when, "", [("", f"{output.glyphs().failed} " + (f"{subject} " if subject else "") + problem)], role="bad")
            return
        if kind in SILENT:
            return
        if kind in AGENT_OF_RESULT:
            role = AGENT_OF_RESULT[kind]
            started = self._agents.pop((ticket, role), None)
            spans: list[Span] = [("", f"{role} {event['end_state']}")]
            if started is not None:
                spans += [JOIN, ("", duration((when - started).total_seconds()))]
            tokens = self._tokens(event.get("event_stream"))
            if tokens is not None:
                spans += [JOIN, ("dim", tokens)]
            self._wait(ticket, _Waiting(AGENT, when, self._chain_subject(ticket, subject), spans))
            return
        if kind in RESULT_OF_REPORT:
            text = "report ok" if event["valid"] else "report not valid"
            if event["valid"] and event.get("status") not in (None, "committed"):
                text = f"report {event['status']}"
            waiting = self._waiting.get(ticket)
            if waiting is not None and waiting.kind == AGENT:
                self._join(ticket, when, subject, [("", text)])
            else:
                self._flush(ticket)
                self._add(when, self._chain_subject(ticket, subject), [("", text)])
            return
        if kind in ("gate-result", "gate-skip"):
            self._gate(event, kind, ticket, subject, when)
            return
        if kind == "verdict":
            findings = len(event.get("findings") or [])  # type: ignore[arg-type]
            if event["verdict"] == "ACCEPT":
                spans = [("ok", output.glyphs().built), ("", " "), ("bold", "ACCEPT")]
            else:
                spans = [("warn bold", f"{event['verdict']}{SEPARATOR}{findings} finding{'s' if findings != 1 else ''}")]
            chain = f"stack {event['stack']}" if "stack" in event else None
            self._wait(ticket, _Waiting(VERDICT, when, chain or subject, spans))
            return
        if kind == "step-end":
            state = str(event["state"])
            text = state if state == status.BUILT or not event.get("reason") else f"{state}{SEPARATOR}{event['reason']}"
            self._join(ticket, when, subject, [("", text)])
            return
        if kind == "run-end":
            self._run_end(event, when)
            return
        self._flush(ticket)
        if kind == "paused":
            self._add(when, "", [("", self._paused(event))], role="warn")
            return
        chain = kind in ("verify-start", "fixup-start", "fixup-refused")
        self._add(when, self._chain_subject(ticket, subject) if chain else subject, self._spans(kind, event), started=kind == "step-start")

    def _gate(self, event: dict[str, object], kind: str, ticket: str, subject: str, when: datetime) -> None:
        """Add the result of one gate to the line of the gates of its round, which waits for the next event."""
        command = str(event["command"])
        started = self._gates.pop((ticket, command), None)
        red = kind == "gate-result" and not event["green"]
        if kind == "gate-skip":
            item = f"{output.gate_name(command)} skipped"
        else:
            item = output.gate_name(command) + (f" {duration((when - started).total_seconds())}" if started is not None else "")
            item += " red" if red else ""
        waiting = self._waiting.get(ticket)
        if waiting is None or waiting.kind != GATES:
            waiting = _Waiting(GATES, when, subject, [])
            self._wait(ticket, waiting)
        waiting.gates.append((item, red))
        waiting.red = waiting.red or red
        waiting.time = when
        items: list[Span] = []
        for index, (text, is_red) in enumerate(waiting.gates):
            items += ([("dim", ", ")] if index else []) + [("bad" if is_red else "dim", text)]
        verdict: Span = ("bad", "gates red") if waiting.red else ("ok", "gates green")
        waiting.spans = [verdict, ("dim", " ("), *items, ("dim", ")")]

    def _chain_subject(self, ticket: str, subject: str) -> str:
        return f"stack {self._chain[ticket]}" if ticket in self._chain else subject

    def _run_end(self, event: dict[str, object], when: datetime) -> None:
        """The line of the end of the run joins the newest line that waits: the verdict of a chain, for example."""
        newest = list(self._waiting)[-1] if self._waiting else None
        for ticket in list(self._waiting):
            if ticket != newest:
                self._flush(ticket)
        start = datetime.fromisoformat(str(self._events[0]["time"])) if self._events and self._events[0]["event"] == "run-start" else when
        result = str(event["result"])
        spans: list[Span] = [("ok bold" if result == status.BUILT else "bad", f"run {result}"), JOIN, ("", duration((when - start).total_seconds()))]
        snap = self._snapshot()
        if snap is not None and snap.tokens_new is not None and snap.tokens_cache is not None:
            spans += [JOIN, ("dim", status.format_token_counts(snap.tokens_new, snap.tokens_cache))]
        if newest is None:
            self._add(when, "", spans)
        else:
            waiting = self._waiting.pop(newest)
            self._add(when, waiting.subject, [*waiting.spans, JOIN, *spans])

    def _paused(self, event: dict[str, object]) -> str:
        """The line of a pause: where the run stopped, and the command that goes on with it."""
        ticket, before = event.get("ticket"), event.get("before")
        if before == "gate" and event.get("command"):
            where = f"the gate {output.gate_name(str(event['command']))} of ticket {ticket}"
        elif before == "verifier":
            where = "the verifier of " + (f"stack {event['stack']}" if event.get("stack") else f"ticket {ticket}")
        else:
            where = f"ticket {ticket}"
        run_id = output.short_run_id(str(self._events[0].get("run_id", ""))) if self._events else ""
        return f"paused{SEPARATOR}before {where}{SEPARATOR}go on with delegate run --resume {run_id}"

    def _spans(self, kind: str, event: dict[str, object]) -> list[Span]:
        """The parts of the line of an event that has a line of its own. An event that this version does not know shows its name."""
        if kind == "fixup-start":
            return [("warn", f"fix-up round {event['round']} started")]
        if kind == "continuation":
            return [("warn", f"continuation {event['count']} of {event['limit']}"), JOIN, ("", str(event["trigger"]))]
        return [("", self._text(kind, event))]

    def _text(self, kind: str, event: dict[str, object]) -> str:
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
        if kind == "fixup-refused":
            return f"fix-up round {event['round']} refused"
        if kind == "skip":
            return f"skipped{SEPARATOR}{event['reason']}" if event.get("reason") else "skipped"
        return kind
