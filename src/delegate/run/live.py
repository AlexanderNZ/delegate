"""The live view of `delegate watch` on a terminal: the run line and the ticket table at the top, and below a divider
the feed of the ticket that runs now (#116).

`LiveView` is a sink of `delegate.run.watch.follow`. At each poll it makes a frame: the run line and the ticket table
(`status_table.view_lines`, of the same snapshot as `delegate status`, with the rail and the `after` cells of
`status_picker.plan_links`), a divider, and the lines of the feed. The command (in `delegate.cli.watch`) draws the frame in place on the terminal. The spec is
`design/docs/components.md`.

A line of the feed is short: the local time (`HH:MM`), the ticket, and what happened. It shows no path and no `None`.
The parts sit on the tracks of components.md § Feed: the time (5 cells), 2 cells, the subject (`#70`, or `stack python`
of a chain) on a track as wide as the widest subject of the feed and at least `ID_CELLS`, 2 cells, and the text. A line
with no subject, as a problem, starts its text on the track of the subject.
The view joins the events that belong together into one line: the result of an agent with its report, the results of
the gates of one round, and a verdict or the gates with the end of the step. A problem and a stall are lines that start
with the glyph of a failure (`✗`).

The feed keeps only the lines of the ticket that runs now. Its `step-start` (or a `retry`) starts a new group, so the
lines of a ticket that ended stay until the next ticket starts. In `economy` mode the verification of the chain is one
group, and its lines name the stack. While an agent runs, the last line of a frame tells it, with the tokens of the agent
(in `dim`, when the run has a reader of the event streams) and the time since its last activity. Each part of a line
has the paint of its role (`output.paint`), which is plain text when `NO_COLOR` is set or standard output is not a
terminal.

The time since the last activity of an agent is in the live tail of its row and at the end of its line (#147,
`status.liveness`). When the frame has paint, an agent that is active shows a spinner, and the view draws the frame
again after each `SPIN_SECONDS` of the wait between two reads of the journal: the spinner steps one frame at each draw.
A draw between two reads composes nothing (#351): the view keeps the lines of the newest frame with `SPIN_CELL` in the
place of each spinner, and puts the next frame of the spinner in those cells. The wait between two reads stays the same.
A frame with no spinner is drawn once for each read. With no paint, there is no spinner: the line gives `active <time> ago`, as a snapshot.

The view reads the event streams only through the reader that the command gives (the `activity` method of the adapter
of the run), so this module never imports an adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import domain, feed_text, output, run_state, status, status_model, status_picker, status_table
from .clock import Clock, utc_now
from .feed_text import JOIN, SEPARATOR, Span, duration
from .journal import JournalError, event_time

# The least width of the track of the subject of the feed (components.md § Feed: `#id` 3).
ID_CELLS: int = 3

# The role of the agent of each result event (the `role` of its `agent-start`), and the result event that each report event belongs to.
AGENT_OF_RESULT: dict[str, str] = {"adapter-result": "specialist", "fixup-result": "fix-up", "verify-result": "verifier"}
RESULT_OF_REPORT: dict[str, str] = {"report-validation": "adapter-result", "fixup-report": "fixup-result", "verify-report": "verify-result"}

# The events that add no line of their own: their facts are in the line of another event, or in the table.
SILENT: frozenset[str] = frozenset(
    {"agent-start", "gate-start", "idle-timeout", "agent-timeout", "run-branch-advance", "lock-broken", "pause-requested", "cleanup"}
)

# The kinds of a line that waits for the next event of its ticket, to join it.
AGENT, GATES, VERDICT = "agent", "gates", "verdict"

# The lines of the feed that the fit to the height keeps at the least (components.md § Ticket table (watch), Fit to
# height, #144): the table has the rows of the terminal, less the run line, the blank line, its header, the divider and
# these lines.
FEED_LINES: int = 4

# The minutes with no activity after which the live tail of a running ticket warns, when `watch` has no `--stall-minutes`.
# The pane of the Claude Code plugin uses the same default.
IDLE_WARNING_MINUTES: float = 5

# The seconds between two draws of a frame with a spinner, while the view waits for the next read of the journal (#147).
SPIN_SECONDS: float = 0.125

# The cell of the spinner in a composed frame, until a draw puts the frame of the spinner in its place (#351). It is a character
# of the private use area, one cell wide as each frame of the spinner, and no text of a frame holds it.
SPIN_CELL: str = "\ue000"


def local_time(when: datetime) -> str:
    """The local time of the clock of this computer, as `HH:MM`."""
    return when.astimezone().strftime("%H:%M")


@dataclass
class _Line:
    """A line of the feed. `role` paints the whole line, as for a problem, except a part with a role of its own. With no
    `role`, the time and the subject are `dim`, and the subject of a `started` line is `now` and bold."""

    time: datetime
    subject: str
    spans: list[Span]
    role: str = ""
    started: bool = False

    def painted(self, id_cells: int) -> str:
        """The line with the paint of each part. The subject sits on a track of `id_cells` cells, 2 cells before the text."""
        gap = output.GAP
        padding = " " * (id_cells - output.width(self.subject)) + gap
        if self.role:
            head: list[Span] = [(self.role, local_time(self.time) + gap + (self.subject + padding if self.subject else ""))]
            spans = [(role or self.role, text) for role, text in self.spans]
        else:
            head = [("dim", local_time(self.time)), ("", gap)]
            if self.subject:
                head += [("now bold" if self.started else "dim", self.subject), ("", padding)]
            spans = self.spans
        merged: list[Span] = []
        for role, text in [*head, *spans]:  # the parts of one role next to each other get one paint
            if merged and merged[-1][0] == role:
                merged[-1] = (role, merged[-1][1] + text)
            else:
                merged.append((role, text))
        return "".join(output.styled(role, text) for role, text in merged)


@dataclass
class _Waiting:
    """A line of the feed that waits for the next event of its ticket: the result of an agent, gates, or a verdict."""

    kind: str
    time: datetime
    subject: str
    spans: list[Span]
    gates: list[tuple[str, bool]] = field(default_factory=list)  # the text of each gate, and whether it is red


class LiveView:
    """The sink of `follow` for a terminal. `frame` gives the lines to draw; `draw` gets them at each poll and at the end,
    and also after each `SPIN_SECONDS` of the wait between two polls while the frame has a spinner (`wait`).

    `reader_of` gives the reader of the event streams for the adapter of the run, as for `delegate status`. `clock` gives
    the time of now and the wait (`delegate.run.clock`). `size` gives the columns and the lines of the terminal, with 0
    lines when the terminal does not give its size. `stall_minutes` is the `--stall-minutes` of the watch: an agent with
    no activity for that time shows a warning in the table. With None, the time is `IDLE_WARNING_MINUTES`.
    """

    def __init__(
        self, journal: Path, reader_of: status_model.ReaderOf, clock: Clock,
        size: Callable[[], tuple[int, int]], draw: Callable[[list[str]], None], stall_minutes: float | None = None,
    ) -> None:
        self._journal, self._clock, self._size, self._draw = journal, clock, size, draw
        self._reader_of = status.caching(reader_of)  # a stream that did not change is not parsed again (#183)
        self._idle_limit = int((IDLE_WARNING_MINUTES if stall_minutes is None else stall_minutes) * 60)
        self._events: list[dict[str, object]] = []
        self._feed: list[_Line] = []  # the lines of the group of the ticket that runs now
        self._groups = 0  # the count of the groups so far
        self._in_chain = False  # the group is the verification of an `economy` chain
        self._waiting: dict[str, _Waiting] = {}  # by ticket id, in the order the lines started
        # The start and the event stream of the agent that runs, by ticket and role.
        self._agents: dict[tuple[str, str], tuple[datetime, object]] = {}
        self._warned: set[str] = set()  # the tickets whose newest event is a problem: their rows have the warning mark (#222)
        self._timed_out: set[tuple[str, str]] = set()  # the (ticket, role) of an agent that the engine ended at the agent timeout
        self._gates: dict[tuple[str, str], datetime] = {}  # the start of the gate that runs, by ticket and command
        self._chain: dict[str, str] = {}  # the stack of the chain verification, by the ticket that its events name
        self._reader: status_model.StreamReader | None = None
        # The facts of the newest frame, which a draw between two reads of the journal uses again (#147): the snapshot,
        # the tokens of each agent that runs, and the time of the clock.
        self._snap: status_model.Snapshot | None = None
        self._agent_counts: dict[tuple[str, str], str | None] = {}
        self._at: datetime | None = None
        self._spin = 0  # the frame of the spinner of the next draw
        self._spinning = False  # the newest frame has a spinner
        # The lines of the newest frame with `SPIN_CELL` in the place of the spinner, and the size that they fit (#351): a spin
        # puts its frame of the spinner in these lines, and composes nothing.
        self._template: list[str] = []
        self._template_size: tuple[int, int] | None = None
        self._on_snapshot: Callable[[status_model.Snapshot | None], None] | None = None

    def on_snapshot(self, callback: Callable[[status_model.Snapshot | None], None]) -> None:
        """Call `callback` with the snapshot of each poll, after the poll makes it and before the poll draws, so the command
        makes no second snapshot for the title (#351). The snapshot is None when the journal does not start with `run-start`."""
        self._on_snapshot = callback

    def _now(self) -> datetime:
        """The time of the clock, in UTC."""
        return utc_now(self._clock)

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
        self._add(self._now(), "", feed_text.stall(minutes), role="bad")

    def time_limit(self, minutes: float) -> None:
        self._flush_all()
        self._add(self._now(), "", feed_text.time_limit(minutes))

    def poll(self) -> None:
        columns, rows = self._size()
        lines = self.frame(columns, rows, final=False)
        if self._on_snapshot is not None:
            self._on_snapshot(self._snap)
        self._draw(lines)
        self._spin += 1

    def wait(self, seconds: float) -> None:
        """Wait for the next read of the journal. While the frame has a spinner, draw it again after each `SPIN_SECONDS`
        of the wait, so the spinner steps. A spin composes no table: it puts the next frame of the spinner in the cells of the
        spinner of the newest frame (#351), and `draw` writes the lines that changed. A new size of the terminal composes the
        frame again. The last part of the wait has no draw: the next poll draws."""
        left = seconds
        while self._spinning and left > SPIN_SECONDS:
            self._clock.sleep(SPIN_SECONDS)
            left -= SPIN_SECONDS
            size = self._size()
            if size != self._template_size:
                self._draw(self._compose(*size, final=False))
            else:
                frames = output.spinner()
                glyph = frames[self._spin % len(frames)]
                self._draw([line.replace(SPIN_CELL, glyph) for line in self._template])
            self._spin += 1
        self._clock.sleep(left)

    def interrupted(self) -> None:
        return None  # `end` draws the last frame; the command restores the terminal and prints the line

    def end(self) -> None:
        self._flush_all()
        columns, rows = self._size()
        self._draw(self.frame(columns, rows, final=True))

    # The frame.

    def frame(self, columns: int, rows: int, final: bool) -> list[str]:
        """The lines to draw: the run line, a blank line, the table, the divider, and the feed, each cut to `columns`.

        A frame fits in `rows`, with the last line free for the cursor. The table gets the rows that are left when the
        feed keeps `FEED_LINES` lines, and the rows that do not fit fold (`status_table.view_lines`). The feed gets the rows
        that the table does not use, and keeps its newest lines. The last frame keeps the whole feed, so the terminal
        scrolls it. With 0 `rows`, the size of the terminal is not known: no row folds, and the frame keeps each line. A
        frame of a watch that goes on also tells each agent that runs now; the last frame does not.
        """
        self._at = self._now()
        self._snap = self._snapshot(self._at)
        self._agent_counts = {key: self._agent_tokens(stream) for key, (_, stream) in self._agents.items()}
        return self._compose(columns, rows, final)

    def _compose(self, columns: int, rows: int, final: bool) -> list[str]:
        """The lines of `frame`, from the facts of the newest frame, with the spinner at its next frame."""
        width = max(columns, 1)
        known = rows > 0
        room = max(0, rows - 1)
        snap = self._snap
        spin = SPIN_CELL if output.colour_on() else None
        feed = [*self._feed, *(_Line(w.time, w.subject, w.spans) for w in self._waiting.values())]
        if not final and snap is not None:
            feed += self._running(snap, spin)
        block: list[str] = []
        if snap is not None:
            height = room - 1 - FEED_LINES if known else None
            block = status_table.view_lines(
                snap, status_picker.plan_links(self._events), width, height, self._idle_limit, spin, frozenset(self._warned),
            )
        id_cells = max([ID_CELLS, *(output.width(line.subject) for line in feed)])
        lines = [line.painted(id_cells) for line in feed]
        if not final and known:
            block = block[:room]
            space = max(0, room - len(block) - 1)
            lines = lines[-space:] if space else []
        divider = [output.styled("dim", output.glyphs().rule * width)] if lines else []
        # The spinner shows for an agent that is active: in the live tail of its row, and at the end of its line.
        idle = [t.idle_seconds for t in snap.tickets if t.idle_seconds is not None] if snap is not None and not final else []
        self._spinning = spin is not None and any(status.liveness(seconds, self._idle_limit, spin)[0] == "now" for seconds in idle)
        self._template = [output.cut(line, width) for line in [*block, *divider, *lines]]
        self._template_size = (columns, rows)
        frames = output.spinner()
        return [line.replace(SPIN_CELL, frames[self._spin % len(frames)]) for line in self._template]

    def _running(self, snap: status_model.Snapshot, spin: int | str | None) -> list[_Line]:
        """A line for each agent that runs now: its role, the time since it started, its tokens in `dim` when the run has a
        reader of the event streams, and the time since its last activity (`status.liveness`). `spin` is the frame of the
        spinner (the text of its cell in a composed frame, `SPIN_CELL`), or None when the frame has no paint."""
        now = self._at or self._now()
        idle = {t.id: t.idle_seconds for t in snap.tickets}
        lines = []
        for (ticket, role), (started, _) in self._agents.items():
            spans: list[Span] = [("", f"{role} running{SEPARATOR}{duration((now - started).total_seconds())}")]
            tokens = self._agent_counts.get((ticket, role))
            if tokens is not None:
                spans += [JOIN, ("dim", tokens)]
            paint = "now"
            seconds = idle.get(ticket)
            if seconds is not None:
                tail_role, text = status.liveness(seconds, self._idle_limit, spin)
                if tail_role == "warn":
                    paint = "warn"  # the whole line warns
                spans += [JOIN, ("dim" if tail_role == "dim" else "", text)]
            lines.append(_Line(now, self._chain_subject(ticket, f"#{ticket}"), spans, role=paint))
        return lines

    def _agent_tokens(self, stream: object) -> str | None:
        """The tokens of the stream of an agent that runs, in the short form `12k new · 14k cache`, or None when the run has
        no reader or the stream cannot be read. A stream that the harness has not opened yet has 0 tokens."""
        if self._reader is None or stream is None:
            return None
        found = status.read_stream(self._reader, Path(str(stream)), is_live=True)
        if found is None:
            return None
        return f"{status_model.format_tokens(found.tokens_new)} new{SEPARATOR}{status_model.format_tokens(found.tokens_cache)} cache"

    def _snapshot(self, now: datetime | None = None) -> status_model.Snapshot | None:
        """The snapshot of the events so far at `now` (default: the clock), or None when the journal does not start with
        `run-start`."""
        try:
            return status.snapshot_of(self._events, self._journal, self._reader_of, self._now() if now is None else now)
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

    def _result_tokens(self, event: dict[str, object]) -> str | None:
        """The tokens of the agent that a result event ends: the totals of the event, or else the tokens of its stream."""
        totals = status.journal_totals(event)
        if totals is not None:
            return status_model.format_token_counts(totals.tokens_new, totals.tokens_cache)
        return self._tokens(event.get("event_stream"))

    def _tokens(self, stream: object) -> str | None:
        """The tokens of an event stream in the short form, or None when the run has no reader or the stream cannot be read."""
        if self._reader is None or stream is None:
            return None
        try:
            found = self._reader(Path(str(stream)))
            return status_model.format_token_counts(found.tokens_new, found.tokens_cache)
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
        kind, when = str(event["event"]), event_time(event)
        ticket = str(event.get("ticket", ""))
        subject = f"#{ticket}" if "ticket" in event else ""
        self._group(kind, event)
        if kind == "agent-start":
            # The newest agent of a ticket is the one that runs, as in the snapshot: the one before it ended.
            self._agents = {key: start for key, start in self._agents.items() if key[0] != ticket}
            self._agents[(ticket, str(event["role"]))] = (when, event.get("event_stream"))
        elif kind == "agent-timeout":
            self._timed_out.add((ticket, str(event["role"])))  # the result line of this agent says so (#179)
        elif kind == "gate-start":
            self._gates[(ticket, str(event["command"]))] = when
        elif kind == "verify-start" and "stack" in event:
            self._chain[ticket] = str(event["stack"])  # the events of the chain verification name its last ticket
        elif kind == "step-end":
            for key in [key for key in self._agents if key[0] == ticket]:
                del self._agents[key]  # a crash ends the step with no result event
        elif kind in run_state.STOP_EVENTS:
            self._agents.clear()
        # A problem has the warning line in the place of the line of its event, which the warning says again. The result of
        # an agent is the exception: its line ends the agent that ran, and the warning comes below it.
        if problem is None or kind in AGENT_OF_RESULT:
            self._line(kind, event, ticket, subject, when)
        if "ticket" in event:
            self._warned.discard(ticket)  # the next event of the ticket takes the mark of its warning away
        if problem is not None:
            # The run goes on past a problem (#222): a warning, in `warn`, and a mark on the row of its ticket.
            self._flush(ticket)
            self._add(when, "", feed_text.warning(subject, problem), role="warn")
            if "ticket" in event:
                self._warned.add(ticket)

    def _line(self, kind: str, event: dict[str, object], ticket: str, subject: str, when: datetime) -> None:
        """Add the lines that an event ends to the feed, and keep the line that waits for the next event."""
        if kind in SILENT:
            return
        if kind in AGENT_OF_RESULT:
            role = AGENT_OF_RESULT[kind]
            agent = self._agents.pop((ticket, role), None)
            ended = "timed out" if (ticket, role) in self._timed_out else str(event["end_state"])
            self._timed_out.discard((ticket, role))
            seconds = (when - agent[0]).total_seconds() if agent is not None else None
            spans = feed_text.agent_result(role, ended, seconds, self._result_tokens(event))
            self._wait(ticket, _Waiting(AGENT, when, self._chain_subject(ticket, subject), spans))
            return
        if kind in RESULT_OF_REPORT:
            text = feed_text.report(event)
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
            self._wait(ticket, _Waiting(VERDICT, when, feed_text.verdict_subject(event) or subject, feed_text.verdict(event)))
            return
        if kind == "step-end":
            self._join(ticket, when, subject, [("", feed_text.step_end(event))])
            return
        if kind == "run-end":
            self._run_end(event, when)
            return
        self._flush(ticket)
        run_id = output.short_run_id(str(self._events[0].get("run_id", ""))) if self._events else ""
        role, spans = feed_text.own_line(kind, event, run_id)
        if role:  # a pause, a halt and a crash are of the run, not of a ticket: they have no subject
            subject = ""
        elif kind in feed_text.CHAIN_KINDS:
            subject = self._chain_subject(ticket, subject)
        self._add(when, subject, spans, role=role, started=kind == "step-start")

    def _gate(self, event: dict[str, object], kind: str, ticket: str, subject: str, when: datetime) -> None:
        """Add the result of one gate to the line of the gates of its round, which waits for the next event."""
        command = str(event["command"])
        started = self._gates.pop((ticket, command), None)
        item, red = feed_text.gate_item(event, kind, (when - started).total_seconds() if started is not None else None)
        waiting = self._waiting.get(ticket)
        if waiting is None or waiting.kind != GATES:
            waiting = _Waiting(GATES, when, subject, [])
            self._wait(ticket, waiting)
        waiting.gates.append((item, red))
        waiting.time = when
        waiting.spans = feed_text.gates(waiting.gates)

    def _chain_subject(self, ticket: str, subject: str) -> str:
        return f"stack {self._chain[ticket]}" if ticket in self._chain else subject

    def _run_end(self, event: dict[str, object], when: datetime) -> None:
        """The line of the end of the run joins the newest line that waits: the verdict of a chain, for example."""
        newest = list(self._waiting)[-1] if self._waiting else None
        for ticket in list(self._waiting):
            if ticket != newest:
                self._flush(ticket)
        start = event_time(self._events[0]) if self._events and self._events[0]["event"] == "run-start" else when
        snap = self._snapshot()
        tokens = None
        if snap is not None and snap.tokens_new is not None and snap.tokens_cache is not None:
            tokens = status_model.format_token_counts(snap.tokens_new, snap.tokens_cache)
        spans = feed_text.run_end(event, (when - start).total_seconds(), tokens)
        if newest is None:
            self._add(when, "", spans)
        else:
            waiting = self._waiting.pop(newest)
            self._add(when, waiting.subject, [*waiting.spans, JOIN, *spans])
