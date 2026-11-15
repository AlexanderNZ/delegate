"""The ticket table of `delegate status` and of the live view of `delegate watch`, and its fit to the terminal (#335).

The report (`report_of_journal`) gives the lines of `delegate status`: the header lines of the run, and the ticket table of
frame 1i of the design (#117), with the verdict, the branch and the last event of each ticket from the journal. `view_lines` draws
the snapshot for the live view of `delegate watch`: the run line and the ticket table of frame 3a of the design (#143), fitted to
the height of the terminal as in frame 3b (#144). The two tables share their tracks: the rows are flat and in plan order, and
`plan_links` (in `delegate.run.status_picker`) gives the rail and the `after` cell of each row, so the depth of the plan never makes
the table wider. The snapshot is in `delegate.run.status`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..ports.vcs import VersionControl
from . import domain, output, run_state
from .journal import JournalError, plan_ids, read_new_events
from .runs import journal_of
from .state_location import StateLocation
from .status import events_by_ticket, liveness, seconds_between, snapshot_of
from .status_model import (
    ACCEPTED, BUILT, FAILED, HALTED, PAUSED, PAUSING, PENDING, REJECTED, RUNNING, SKIPPED, ChainSnapshot, ReaderOf, Snapshot,
    TicketSnapshot, format_duration, format_token_counts, format_tokens,
)
from .status_picker import PlanLink, plan_links

# What a line shows for a value that it does not have: the ETA before the first ticket ends.
_NONE: str = "-"

def _run_progress(snap: Snapshot) -> str:
    """The progress of the run: the phase of an `economy` chain, the percentage, the tickets that ended, the time used,
    the ETA, and the tokens.

    The ETA shows only while the run runs: in the time from now, or `-` before the first ticket ends. The tokens show
    only when the snapshot has them.
    """
    eta = format_duration(seconds_between(snap.time, snap.eta)) if snap.eta else "-"
    parts = [snap.phase] if snap.phase else []
    parts += [f"{snap.percent}%", f"{snap.tickets_ended} of {snap.tickets_total} ended", f"{format_duration(snap.elapsed_seconds)} used"]
    if snap.state in (RUNNING, PAUSING):
        parts.append(f"ETA {eta}")
    if snap.tokens_new is not None and snap.tokens_cache is not None:
        parts.append(format_token_counts(snap.tokens_new, snap.tokens_cache))
    return ", ".join(parts)


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
    # A branch that a `cleanup` event lists as deleted is gone: no line says that it is kept.
    deleted = {str(b) for e in events if e["event"] == "cleanup" for b in e.get("branches_deleted", [])}  # type: ignore[union-attr]
    lines = [
        f"run {start['run_id']}: {snap.state} (mode {start['mode']}, adapter {start['adapter']})",
        *([f"note: {snap.note}"] if snap.note else []),
        f"progress: {_run_progress(snap)}", f"journal {journal}",
        *[output.stack_change_line(e) for e in events if e["event"] == "stack-change"],
        *[
            f"kept: branch {e['failed_branch']} holds the old work of ticket {e['ticket']}, which a retry started again; delegate clean removes it"
            for e in events if e["event"] == "retry" and e.get("failed_branch") and e["failed_branch"] not in deleted
        ],
    ]
    states = {t.id: t.state for t in snap.tickets}
    # An `economy` run reads the verdicts of the current pass of the chain, as the engine does (#304): a retry that
    # started the verification again leaves the verdict of the first pass out.
    verdict_events = events[run_state.fold(events).chain.start :] if domain.chain_units(str(start["mode"])) else events
    facts: dict[str, dict[str, str]] = {}
    plan = plan_ids(events)
    owns = events_by_ticket(events, plan)
    verdicts_of_run = [e for e in verdict_events if e["event"] == "verdict"]
    for ticket in plan:
        own = owns[ticket]
        # The verdict of a chain (`economy` mode) covers each ticket in its `tickets` field, not only the ticket it names.
        verdicts = [
            str(e["verdict"]) for e in verdicts_of_run
            if e.get("ticket") == ticket or ticket in e.get("tickets", [])  # type: ignore[operator]
        ]
        branch = next((str(e["branch"]) for e in own if e["event"] == "step-start"), "")
        facts[ticket] = {
            "verdict": output.styled("bold", verdicts[-1]) if verdicts else "",
            "branch": branch if states[ticket] == RUNNING else output.styled("dim", branch),
            "last event": output.styled("dim", str(own[-1]["time"])) if own else "",
        }
    rows = _rows(snap, plan_links(events), None, output.glyphs(), live=False, facts=facts)
    table, _ = _table(snap, rows, _STATUS_COLUMNS, _STATUS_TRACKS, frozenset(), output.glyphs())
    return [*lines, "", *table]


def _report(
    repo: Path, run_id: str | None, vcs: VersionControl, reader_of: ReaderOf, now: datetime, *, location: StateLocation | None = None,
) -> list[str]:
    """The lines of the report for a run of the repository: the run with that id, or the newest run when `run_id` is None.

    The errors are the errors of `snapshot`. `location` is as in `snapshot`.
    """
    return report_of_journal(journal_of(repo, run_id, vcs, location=location), reader_of, now)


def report_of_journal(journal: Path, reader_of: ReaderOf, now: datetime) -> list[str]:
    """The lines of the report for the run of the journal file `journal`, as `_report` gives them, with no lookup of the repository."""
    events, _ = read_new_events(journal, 0, 1)
    if not events:
        raise JournalError(f"journal {journal} holds no event")
    return render(events, journal, snapshot_of(events, journal, reader_of, now))


# The run line of the live view draws the bar only on a terminal of this many columns or more, and the bar has 16 cells.
BAR_COLUMNS: int = 100
BAR_CELLS: int = 16

# The columns of the ticket table of the live view, in order (components.md § Ticket table (watch), #143). The rail
# and the live tail have no header. The columns of numbers align right.
_RAIL: str = "rail"
_TAIL: str = "tail"
_TABLE_COLUMNS: tuple[str, ...] = ("#", _RAIL, "plan", "state", "phase", "after", "used", "new", "cache", _TAIL)
_TABLE_NUMBERS: frozenset[str] = frozenset({"#", "used", "new", "cache"})

# The columns of the ticket table of `delegate status` (frame 1i, components.md § Ticket table (status)): the columns of
# the live view, with `verdict` and `branch` after `after`, and `last event` last. Status is a snapshot, so it has no
# live tail: its last column is only the reason of a row that failed, was skipped, or that a pause stopped before.
_STATUS_COLUMNS: tuple[str, ...] = (
    "#", _RAIL, "plan", "state", "phase", "after", "verdict", "branch", "used", "new", "cache", "last event", _TAIL,
)

# The design width of each column of the ticket table of the live view, in cells. A column is as wide as its track, or
# as its widest value when that is wider, so the depth of the plan never makes the table wider. The live tail has no
# track. The table of `delegate status` has the same tracks, with `state` 8, `verdict` 7 and `branch` 15. `last event`
# has no track.
_TRACKS: dict[str, int] = {"#": 3, _RAIL: 2, "plan": 6, "state": 10, "phase": 13, "after": 8, "used": 7, "new": 5, "cache": 6}
_STATUS_TRACKS: dict[str, int] = {**_TRACKS, "state": 8, "verdict": 7, "branch": 15}

# The steps of the fit to the width, in the order of components.md § 80 columns: the columns that drop at each step.
# The bar of the run line goes first, by `BAR_COLUMNS`. Then `new` and `cache` drop, then `used`. The phases stack, so
# their labels stay.
_FIT_STEPS: tuple[frozenset[str], ...] = (frozenset(), frozenset({"new", "cache"}), frozenset({"new", "cache", "used"}))

# The phases of a running ticket. In `economy` mode only the chain verifies, so a ticket has two phases.
_TRACK: tuple[str, ...] = (domain.PHASE_SPECIALIST, domain.PHASE_GATES, domain.PHASE_VERIFIER, domain.PHASE_FIXUP)
_ECONOMY_TRACK: tuple[str, ...] = (domain.PHASE_SPECIALIST, domain.PHASE_GATES)

# The reason of a ticket that a pause stopped before, by the `before` field of the `paused` event.
_PAUSED_CELL: dict[str, str] = {"step": "paused before this step", "gate": "paused before the gate", "verifier": "paused before the verifier"}

# The live tail of the next row while the run is pausing (copy-deck.md): present tense until the engine reads the request.
_PAUSING_CELL: str = "pauses before this step"

# The fit to the height (components.md § Ticket table (watch), Fit to height, #144). When the rows in the middle fold,
# the tickets of the first `_FOLD_KEEP` rows and the `_FOLD_KEEP` tickets before the running ticket stay. The fold of the
# tail alone is enough only when it keeps the `_FOLD_NEAREST` tickets after the running ticket, or each ticket after it.
_FOLD_KEEP: int = 3
_FOLD_NEAREST: int = 3

# The order of the states in the text of a fold row. The glyph of the fold row is the glyph of its first state.
_FOLD_STATES: tuple[str, ...] = (BUILT, FAILED, SKIPPED, RUNNING, PENDING)


@dataclass(frozen=True)
class _Row:
    """One row of the ticket table: the cells by the name of the column, the ticket and its state, and whether the
    live tail counts in the fit to the width. A reason in the tail does not count: the cut of the line ends it."""

    ticket: str
    state: str
    cells: dict[str, str]
    measured: bool = False


def view_lines(
    snap: Snapshot, links: Sequence[PlanLink], width: int, height: int | None = None, idle_limit: int | None = None,
    spin: int | None = None, warned: frozenset[str] = frozenset(),
) -> list[str]:
    """The lines of the live view of a snapshot: the run line, a blank line, and the ticket table, each cut to `width`.

    The spec is `design/docs/components.md`. `links` gives the rail and the
    `after` cell of each ticket (`plan_links`). The rows are flat and in plan order, and the running ticket has a
    sub-row for each phase after its first. When the lines would be more than `height`, the rows fold (`_fit`): first
    the tail, then the rows in the middle. With no `height`, no row folds. When a line of the table is wider than
    `width`, the columns drop in the order of `_FIT_STEPS`. An agent or a gate log that has not changed for
    `idle_limit` seconds or more shows a warning in the live tail. `spin` is the frame of the spinner of a live view
    that animates, or None for the text of a snapshot (`liveness`). `warned` gives the ids of the tickets whose newest
    event is a problem: the live tail of their rows starts with the warning mark (#222). The view names only the run id, the ticket ids, the
    states, the phases, the reasons with no path, and the numbers. It shows no tool call.
    """
    glyph = output.glyphs()
    rows = _fit(snap, _rows(snap, links, idle_limit, glyph, live=True, spin=spin, warned=warned), None if height is None else height - 3)
    for dropped in _FIT_STEPS:
        table, measured = _table(snap, rows, _TABLE_COLUMNS, _TRACKS, dropped, glyph)
        if measured <= width:
            break
    return [output.cut(line, max(width, 1)) for line in [_run_line(snap, width, glyph), "", *table]]


def _run_line(snap: Snapshot, width: int, glyph: output.Glyphs) -> str:
    """The run line: the short run id, the state, the bar, the percentage, the tickets that ended, the time used, the
    ETA while the run runs, and the two token counts when the snapshot has them."""
    dot = f" {glyph.pending} "  # the separator of tokens.md, which has an ASCII form
    state = snap.state + (f"{dot}{snap.phase}" if snap.phase else "")
    role = {PAUSED: "warn", PAUSING: "warn", HALTED: "bad bold", BUILT: "ok bold", FAILED: "bad bold"}.get(snap.state, "")
    progress = f"{snap.percent}%"
    if width >= BAR_COLUMNS:
        filled = snap.percent * BAR_CELLS // 100
        progress = output.styled("ok", glyph.fill * filled) + output.styled("dim", glyph.empty * (BAR_CELLS - filled)) + " " + progress
    parts = [
        "run " + output.styled("bold", output.short_run_id(snap.run_id)), output.styled(role, state), progress,
        f"{snap.tickets_ended}/{snap.tickets_total} ended", output.styled("dim", f"{format_duration(snap.elapsed_seconds)} used"),
    ]
    if snap.state in (RUNNING, PAUSING):
        parts.append("ETA " + (format_duration(seconds_between(snap.time, snap.eta)) if snap.eta else _NONE))
    if snap.tokens_new is not None and snap.tokens_cache is not None:
        parts.append(output.styled("dim", f"{format_tokens(snap.tokens_new)} new{dot}{format_tokens(snap.tokens_cache)} cache"))
    return output.GAP.join(parts)


def _table(
    snap: Snapshot, rows: Sequence[_Row | _Fold], columns: tuple[str, ...], tracks: dict[str, int], dropped: frozenset[str],
    glyph: output.Glyphs,
) -> tuple[list[str], int]:
    """The lines of the ticket table, and the width that the fit to the width measures.

    The lines are the header and the rows, with a line for each fold (`_fold_line`). Each column is as wide as its
    track, or as its widest value when that is wider. The columns of `dropped` are not in the table, and neither are
    `new` and `cache` when the snapshot has no tokens. The width is the widest line, without a reason in the tail
    (`_Row.measured`) and without a fold.
    """
    names = [name for name in columns if name not in dropped and (snap.tokens_new is not None or name not in ("new", "cache"))]
    header = [output.styled("dim", "" if name in (_RAIL, _TAIL) else name) for name in names]
    kept = [row for row in rows if isinstance(row, _Row)]
    totals = [_fold_totals(snap, row) for row in rows if isinstance(row, _Fold) and not row.tail]
    body = [[row.cells.get(name, "") for name in names] for row in kept]
    widths = [
        max(tracks.get(name, 0), *(output.width(cells[index]) for cells in [header, *body]), *(output.width(t.get(name, "")) for t in totals))
        for index, name in enumerate(names)
    ]
    right = {index for index, name in enumerate(names) if name in _TABLE_NUMBERS}
    drawn = iter(line.rstrip(" ") for line in output.columns([header, *body], right=right, tracks=widths))
    lines = [next(drawn)]
    for row in rows:
        lines.append(next(drawn) if isinstance(row, _Row) else _fold_line(snap, row, names, widths, glyph))
    quiet = [[cells[index] if name != _TAIL or row.measured else "" for index, name in enumerate(names)] for row, cells in zip(kept, body)]
    measured = max(output.width(line.rstrip(" ")) for line in output.columns([header, *quiet], right=right, tracks=widths))
    return lines, measured


@dataclass(frozen=True)
class _Fold:
    """One row of the ticket table that holds the rows of the tickets that do not fit the height of the terminal.

    `tickets` are the tickets of the fold, in plan order, and `first` is the plan position of the first. `tail` is
    true for the fold of the tail, the last row above the divider, and false for the fold in the middle. `rail` is the
    rail cell of the fold: the rail runs through the fold in the middle when the row under it has the rail.
    """

    tickets: tuple[TicketSnapshot, ...]
    first: int
    tail: bool
    rail: str = ""


def _fit(snap: Snapshot, rows: list[_Row], room: int | None) -> list[_Row | _Fold]:
    """The rows of the ticket table that fit in `room` rows, with a fold for the rows that do not (#144).

    The spec is components.md § Ticket table (watch), Fit to height. The anchor is the running ticket, or else the
    ticket that the pause stopped before, the first pending ticket, or the last ticket. Its rows never fold. When the
    rows from the top to the anchor, the rows of `_FOLD_NEAREST` tickets after it and a fold of the tail fit, the rows show from the
    top in plan order and the tail folds into one last row. If not, the rows in the middle fold too: the first
    `_FOLD_KEEP` rows and the `_FOLD_KEEP` rows before the anchor stay. The rows after the anchor that fit then show,
    the nearest first, and the rest fold into the tail. The rows of one ticket fold together. With no `room`, or when
    each row fits, no row folds.
    """
    if room is None or len(rows) <= room:
        return list(rows)
    blocks: list[list[_Row]] = []  # the rows of each ticket, in plan order
    for row in rows:
        if blocks and blocks[-1][0].ticket == row.ticket:
            blocks[-1].append(row)
        else:
            blocks.append([row])
    states = [RUNNING if any(row.state == RUNNING for row in block) else block[0].state for block in blocks]
    ids = [block[0].ticket for block in blocks]
    paused = snap.paused_before[0] if snap.paused_before is not None else None
    anchor = next((index for index, state in enumerate(states) if state == RUNNING), None)
    if anchor is None:
        anchor = ids.index(paused) if paused in ids else next((i for i, state in enumerate(states) if state == PENDING), len(blocks) - 1)
    after = blocks[anchor + 1:]
    shown = _nearest(after, room - sum(len(block) for block in blocks[:anchor + 1]))
    middle = blocks[_FOLD_KEEP:max(_FOLD_KEEP, anchor - _FOLD_KEEP)]
    head: list[_Row | _Fold] = [row for block in blocks[:anchor + 1] for row in block]
    # The rows from the top to the anchor can be more than `room` when no ticket, or too few tickets, come after the anchor
    # (the chain row of an `economy` run is under the last ticket, #196): the rows in the middle fold then too.
    if (len(shown) < min(_FOLD_NEAREST, len(after)) or len(head) > room) and len(middle) > 1:
        before = blocks[anchor - _FOLD_KEEP:anchor + 1]
        fold = _Fold(_tickets_of(snap, middle), _FOLD_KEEP + 1, tail=False, rail=before[0][0].cells.get(_RAIL, ""))
        head = [*(row for block in blocks[:_FOLD_KEEP] for row in block), fold, *(row for block in before for row in block)]
        shown = _nearest(after, room - len(head))
    fitted = [*head, *(row for block in shown for row in block)]
    if len(shown) < len(after):
        fitted.append(_Fold(_tickets_of(snap, after[len(shown):]), anchor + 2 + len(shown), tail=True))
    return fitted


def _nearest(after: list[list[_Row]], room: int) -> list[list[_Row]]:
    """The tickets after the anchor that fit in `room` rows, the nearest first: each one when they all fit, or else the
    ones that fit with the fold of the tail."""
    if sum(len(block) for block in after) <= room:
        return after
    shown: list[list[_Row]] = []
    used = 1  # the fold of the tail
    for block in after:
        if used + len(block) > room:
            break
        shown.append(block)
        used += len(block)
    return shown


def _tickets_of(snap: Snapshot, blocks: list[list[_Row]]) -> tuple[TicketSnapshot, ...]:
    by_id = {ticket.id: ticket for ticket in snap.tickets}
    return tuple(by_id[block[0].ticket] for block in blocks)


def _fold_totals(snap: Snapshot, fold: _Fold) -> dict[str, str]:
    """The cells `used`, `new` and `cache` of the fold in the middle: the sums of its tickets that have a step. A ticket
    with no tokens adds nothing, as in the run line."""
    stepped = [t for t in fold.tickets if t.state not in (PENDING, SKIPPED)]
    if not stepped:
        return {}
    cells = {"used": output.styled("dim", format_duration(sum(t.elapsed_seconds for t in stepped)))}
    if snap.tokens_new is not None and any(t.tokens_new is not None and t.tokens_cache is not None for t in stepped):
        cells["new"] = output.styled("dim", format_tokens(sum(t.tokens_new or 0 for t in stepped)))
        cells["cache"] = output.styled("dim", format_tokens(sum(t.tokens_cache or 0 for t in stepped)))
    return cells


def _fold_line(snap: Snapshot, fold: _Fold, names: list[str], widths: list[int], glyph: output.Glyphs) -> str:
    """The line of a fold, on the tracks of the table.

    In `plan` and `state`: the glyph of the first state and the count of each state, as `✓ 17 built` or
    `· 11 pending`. In `after`: the range of the ids, as `104–120`. Then `rows <first>–<last> folded`: in the live
    tail for the fold in the middle, which also gives its totals in `used`, `new` and `cache` (`_fold_totals`), and
    from the first of these columns for the fold of the tail. The text is `dim`, and the rail is `plan`.
    """
    start = {name: sum(widths[:index]) + len(output.GAP) * index for index, name in enumerate(names)}
    end = {name: start[name] + widths[index] for index, name in enumerate(names)}
    counts = {state: sum(1 for t in fold.tickets if t.state == state) for state in _FOLD_STATES}
    first = next(state for state in _FOLD_STATES if counts[state])
    role, mark = {
        BUILT: ("ok", glyph.built), FAILED: ("bad", glyph.failed), SKIPPED: ("dim", glyph.skipped),
        RUNNING: ("now", glyph.running), PENDING: ("dim", glyph.pending),
    }[first]
    words = ", ".join(f"{counts[state]} {state}" for state in _FOLD_STATES if counts[state])
    summary = output.styled("dim", f"{mark} {words}") if role == "dim" else output.styled(role, mark) + output.styled("dim", f" {words}")
    ids, last = (fold.tickets[0].id, fold.tickets[-1].id), fold.first + len(fold.tickets) - 1
    pieces = [(start[_RAIL], fold.rail)] if fold.rail and _RAIL in start else []
    pieces += [(start["plan"], summary), (start["after"], output.styled("dim", glyph.dash.join(ids) if ids[0] != ids[1] else ids[0]))]
    if not fold.tail:
        pieces += [(end[name] - output.width(text), text) for name, text in _fold_totals(snap, fold).items() if name in start]
    folded = f"rows {fold.first}{glyph.dash}{last} folded" if last != fold.first else f"row {last} folded"
    pieces.append((start[_TAIL] if not fold.tail else min(start[name] for name in ("used", "new", "cache", _TAIL) if name in start), output.styled("dim", folded)))
    line = ""
    for cell, text in pieces:
        line += " " * max(1 if line else 0, cell - output.width(line)) + text
    return line


def _rows(
    snap: Snapshot, links: Sequence[PlanLink], idle_limit: int | None, glyph: output.Glyphs, live: bool,
    facts: dict[str, dict[str, str]] | None = None, spin: int | None = None, warned: frozenset[str] = frozenset(),
) -> list[_Row]:
    """The rows of the ticket table in plan order: one row for each ticket, and under the row of a running ticket a
    sub-row for each phase after its first.

    The rail of a row joins it to the row above it: its one blocker is that row. A sub-row has the rail when the next
    row has it, so the rail runs through the sub-rows. `live` gives the live tail of the live view: the activity of the
    running ticket on its row, and the gate that runs on the sub-row of its phase. Without it, as in `delegate status`,
    the tail has only the reason of a row. While the run is pausing, the live tail of the next row gives
    `_PAUSING_CELL` (`_pausing_before`). `facts` gives, for each ticket id, the cells of the columns that the live
    view does not have: `verdict`, `branch` and `last event` of `delegate status`. `spin` and `warned` are as for `view_lines`.
    """
    by_id = {link.id: link for link in links}
    pausing = _pausing_before(snap) if live else None
    rows = []
    for position, ticket in enumerate(snap.tickets, start=1):
        link = by_id.get(ticket.id)
        below = by_id.get(snap.tickets[position].id) if position < len(snap.tickets) else None
        phases, active = _phases(snap, ticket, glyph)
        details = [""] * max(1, len(phases))
        if live and ticket.state == RUNNING:
            details[0] = _activity_tail(ticket, idle_limit, glyph, spin)
            if ticket.gate is not None:
                at = active if 0 < active < len(details) else 0
                details[at] = f" {glyph.pending} ".join(part for part in (details[at], _gate_tail(str(ticket.gate), ticket.gate_idle_seconds, idle_limit, glyph, ticket.gate_name)) if part)
        reason = output.styled("warn", _PAUSING_CELL) if ticket.id == pausing else _reason(snap, ticket, live)
        cells = _row(ticket, position, link, glyph)
        tail = reason or details[0]
        if ticket.id in warned:
            tail = f"{output.styled('warn', glyph.idle)} {tail}".rstrip()  # a problem is the newest event of the ticket (#222)
        cells.update(phase=phases[0] if phases else "", **{_TAIL: tail})
        cells.update((facts or {}).get(ticket.id, {}))
        rows.append(_Row(ticket.id, ticket.state, cells, measured=not reason))
        rail = output.styled("plan", glyph.rail) if below is not None and below.rail else ""
        for phase, detail in zip(phases[1:], details[1:]):
            rows.append(_Row(ticket.id, ticket.state, {_RAIL: rail, "phase": phase, _TAIL: detail}, measured=True))
        for chain in snap.chains:
            if chain.after == ticket.id:
                rows.append(_Row(ticket.id, chain.state, _chain_row(snap, chain, rail, idle_limit, glyph, live, spin), measured=True))
    return rows


def _chain_row(
    snap: Snapshot, chain: ChainSnapshot, rail: str, idle_limit: int | None, glyph: output.Glyphs, live: bool, spin: int | None
) -> dict[str, str]:
    """The cells of the chain row of one stack of an `economy` run (#196), by the name of the column.

    The row goes under the rows of the last ticket of the stack. The `plan` cell holds the stack name, `state` is
    `running`, `accepted` or `rejected`, and `phase` is the active phase of the chain: `gates`, `verifier` or `fix-up`
    with its round. `used` is the time since the verification of the stack started, and `new` and `cache` count its
    agents. The live tail gives the gate that runs and the age of its log, or the activity of the agent. When the chain
    ended, the `verdict` cell holds its verdict, and the live view, which has no such column, gives it in the live tail.
    """
    node, id_role, state = {
        ACCEPTED: (output.styled("ok", glyph.node), "dim", output.styled("ok", glyph.built) + output.styled("dim", f" {ACCEPTED}")),
        REJECTED: (output.styled("bad", glyph.failed), "bad bold", output.styled("bad bold", REJECTED)),
    }.get(chain.state, (output.styled("now", glyph.running), "now bold", output.styled("now bold", RUNNING)))
    cells = {
        _RAIL: rail, "plan": node + " " + output.styled(id_role, chain.stack), "state": state, "used": output.styled("dim", format_duration(chain.elapsed_seconds)),
        "verdict": output.styled("bold", chain.verdict) if chain.verdict else "",
    }
    if chain.tokens_new is not None and chain.tokens_cache is not None:
        cells["new"], cells["cache"] = output.styled("dim", format_tokens(chain.tokens_new)), output.styled("dim", format_tokens(chain.tokens_cache))
    parts: list[str] = []
    if chain.phase is not None:
        rounds = f" {chain.fixup_round}/{domain.fixup_round_limit(snap.mode)}" if chain.fixup_round and snap.mode in domain.FIXUP_ROUND_LIMIT else ""
        cells["phase"] = output.styled("warn" if chain.phase == domain.PHASE_FIXUP else "now", f"{chain.phase} {glyph.node}{rounds}")
        if chain.idle_seconds is not None:
            parts.append(output.styled(*liveness(chain.idle_seconds, idle_limit, spin)))
        if chain.gate_command is not None:
            parts.append(_gate_tail(chain.gate_command, chain.gate_idle_seconds, idle_limit, glyph, chain.gate_name))
    elif live and chain.verdict:
        parts.append(output.styled("ok" if chain.state == ACCEPTED else "bad", f"verdict {chain.verdict}"))
    cells[_TAIL] = f" {glyph.pending} ".join(parts)
    return cells


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
        _RAIL: output.styled("plan", glyph.rail) if link is not None and link.rail else "",
        "plan": node + " " + output.styled(id_role, ticket.id),
        "state": state,
        "after": output.styled("plan", " ".join(link.after)) if link is not None else "",
        "used": output.styled("dim", format_duration(ticket.elapsed_seconds)) if has_step else "",
        "new": output.styled("dim", format_tokens(ticket.tokens_new)) if tokens else "",  # type: ignore[arg-type]
        "cache": output.styled("dim", format_tokens(ticket.tokens_cache)) if tokens else "",  # type: ignore[arg-type]
    }


def _pausing_before(snap: Snapshot) -> str | None:
    """The ticket of the next row while the run is pausing: the first pending ticket after the running ticket in plan
    order, or the first pending ticket when no ticket runs. None when the run is not pausing, or no ticket is pending."""
    if snap.state != PAUSING:
        return None
    running = next((index for index, t in enumerate(snap.tickets) if t.state == RUNNING), 0)
    return next((t.id for t in snap.tickets[running:] if t.state == PENDING), None)


def _reason(snap: Snapshot, ticket: TicketSnapshot, live: bool) -> str:
    """The reason of a ticket that the pause of the run stopped before, that failed, or that was skipped, or empty.

    The reason of a crash ends with the file of its traceback. `delegate status` gives the whole path. The live view
    cuts it to the name of the file, as it does each path (`output.without_paths`).
    """
    if snap.state == PAUSED and snap.paused_before is not None and snap.paused_before[0] == ticket.id:
        return output.styled("warn", _PAUSED_CELL.get(snap.paused_before[1], f"paused before the {snap.paused_before[1]}"))
    if ticket.state == FAILED and ticket.reason:
        text = output.styled("bad", output.without_paths(ticket.reason))
        if ticket.traceback:
            text += output.styled("dim", f" (traceback {output.without_paths(ticket.traceback) if live else ticket.traceback})")
        return text
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
    track = _ECONOMY_TRACK if snap.mode == domain.ECONOMY else _TRACK
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


def _activity_tail(ticket: TicketSnapshot, idle_limit: int | None, glyph: output.Glyphs, spin: int | None) -> str:
    """The live tail of the row of a running ticket: its continuations, and the activity of its agent (`liveness`)."""
    parts = []
    if ticket.continuation is not None:
        parts.append(output.styled("warn", f"cont {ticket.continuation[0]}/{ticket.continuation[1]}"))
    if ticket.idle_seconds is not None:
        parts.append(output.styled(*liveness(ticket.idle_seconds, idle_limit, spin)))
    return f" {glyph.pending} ".join(parts)


def _gate_tail(command: str, log_idle_seconds: int | None, idle_limit: int | None, glyph: output.Glyphs, name: str | None = None) -> str:
    """The detail of the gate that runs: its short name and the age of its log, on the sub-row of its phase (or the chain row)."""
    gate = f"gate {output.gate_name(command, name)}"
    if log_idle_seconds is not None:
        gate += f" {glyph.pending} " + _idle(log_idle_seconds, f"log {format_duration(log_idle_seconds)} ago", idle_limit, glyph)
    return gate
