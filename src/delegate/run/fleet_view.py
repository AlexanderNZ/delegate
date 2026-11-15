"""The view of `delegate runs` (#334): the table and the timeline, from a `Fleet` to lines. The spec is `design/docs/components.md`: the table C25 (frames W8, E15–E18) and the timeline C26 (frame W9).

The columns are `host repo run state ended ETA slip new cache trend alert do` (C25), with the legend under them (C27). Above them are the two fleet lines (C24) and the header line of the
settings (`settings_line`, #323). With `timeline` (#314, #315) `fleet_lines` has one lane for each run in place of the table, and the lane `all` first (`fleet_rules.lane`).

The rows under the totals row of a host are the runs that the totals count (#322). The runs that ended in the window are in one group `ended` after the hosts. An unreachable host says so
in its row, with `heard <duration> ago` and the command `check host`, and the row under it gives the reason (#323). The table fits the terminal (C25, #312): `fleet_lines` drops the columns of
`FIT_STEPS` until the table fits the width, and folds the rows until the lines fit the height. This module reads no file, no host and no journal: the `Fleet` holds all it shows.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby

from . import fleet_rules, output
from .fleet_model import IDLE_LIMIT_SECONDS, Fleet, LostHost, RunRow, alert_text
from .status import BAR_CELLS, BAR_COLUMNS, BUILT, FAILED, HALTED, PAUSED, PAUSING, RUNNING, STOPPED, format_duration, format_tokens
from .journal import ABANDONED

# The columns of the table, in order. `new` and `cache` are numbers and align right.
COLUMNS: tuple[str, ...] = ("host", "repo", "run", "state", "ended", "ETA", "slip", "new", "cache", "trend", "alert", "do")
NUMBERS: frozenset[int] = frozenset({7, 8})

# The steps of the fit to the width (components.md, C25), in order: the columns that drop at each step. The bar and the legend
# drop first, by `BAR_COLUMNS`. Then `new` and `cache` drop, then `trend`, then `slip`, then `repo`.
FIT_STEPS: tuple[frozenset[str], ...] = (
    frozenset(), frozenset({"new", "cache"}), frozenset({"new", "cache", "trend"}), frozenset({"new", "cache", "trend", "slip"}),
    frozenset({"new", "cache", "trend", "slip", "repo"}),
)

# The command of each word of the column `do`, for the legend (copy deck W8.do.*). The placeholders stay as the copy deck writes them.
COMMANDS: dict[str, str] = {
    fleet_rules.WATCH: "delegate watch <run>", fleet_rules.RESUME: "delegate run --resume <run>",
    fleet_rules.RETRY: "delegate run --resume <run> --retry <ticket>", fleet_rules.MERGE: "git merge --ff-only <run branch>",
    fleet_rules.CHECK_HOST: "ssh <host>",
}

# The words that the last line of the legend ends with, after `; `, when a host other than this machine has a `do` word (copy deck W8.legend.otherHost).
OTHER_HOST: str = "on another host, put ssh <host> in front of each command"

# The header of the group of the runs that ended (copy deck W8.group.ended).
ENDED_GROUP: str = "ended"

# The state word of a host that this machine cannot read (copy deck W8.state.unreachable).
UNREACHABLE: str = "unreachable"

# The header of the columns after the lane in the timeline (copy deck W9.table.header).
W9_HEADER: tuple[str, ...] = ("ended", "ETA", "state", "alert")

# The label of the first lane of the timeline (copy deck W9.lane.all), and the line under the lanes (copy deck W9.footer). The placeholders stay as the copy deck writes them.
ALL: str = "all"
FOOTER: str = "detail  delegate watch <run>; on another host, ssh <host> delegate watch <run>"

# The words of the header line for the index of this machine only, with no shared directory (#323).
OWN_INDEX: str = "the index of this machine only"

# The role of the state word in the table (components.md, C25). A state that has no entry has no role.
STATE_ROLES: dict[str, str] = {
    RUNNING: "now bold", PAUSING: "warn", PAUSED: "warn", STOPPED: "bad", HALTED: "bad bold", BUILT: "ok bold", FAILED: "bad bold",
    ABANDONED: "dim",
}


def _clock(eta: datetime) -> str:
    """The local clock time of an ETA, `HH:MM`."""
    return eta.astimezone().strftime("%H:%M")


def _whole_minutes(seconds: int) -> str:
    """A duration in whole minutes, `55m`, or `1h05m` from an hour."""
    minutes = seconds // 60
    return f"{minutes}m" if minutes < 60 else format_duration(minutes * 60)


def slip_text(seconds: int) -> str:
    """The slip as the copy deck writes it (W8.slip): `+55m`, whole minutes, or `+1h05m` from an hour."""
    return "+" + _whole_minutes(seconds)


def heard_text(heard: datetime, now: datetime) -> str:
    """The alert of an unreachable host (copy deck W8.alert.heard): `heard 47m ago`, in whole minutes, or in seconds under a minute."""
    seconds = max(int((now - heard).total_seconds()), 0)
    return f"heard {format_duration(seconds) if seconds < 60 else _whole_minutes(seconds)} ago"


def sparkline(cells: Sequence[int], largest: int, glyph: output.Glyphs) -> str:
    """The sparkline of the cells (tokens.md): one glyph for each cell, scaled to `largest`, and a space for a cell with no ticket."""
    return "".join(" " if not level else glyph.spark[level - 1] for level in (fleet_rules.spark_level(cell, largest) for cell in cells))


def _cells(row: RunRow, glyph: output.Glyphs, largest: int) -> list[str]:
    """The cells of the row of one run, in the order of `COLUMNS`. A cell with no value is empty.

    The ETA is a clock time, or `-` while the run runs and has no estimate; it is empty for a run that is paused,
    halted, stopped or ended. The ended count is dim for a run that ended. `trend` is dim, on the scale `largest`. `alert` is in `warn`, and `do` is plain.
    """
    if row.unreachable:  # the last-known values, dim, and nothing that this machine cannot know (#317)
        return [
            output.styled("plan", glyph.rail), output.styled("dim", row.repo), output.styled("dim", output.short_run_id(row.run_id)),
            output.styled("dim", row.state), output.styled("dim", f"{row.tickets_ended}/{row.tickets_total}"), *[""] * 7,
        ]
    eta = "" if row.state not in (RUNNING, PAUSING) else _clock(row.eta) if row.eta else "-"
    slip = "" if row.slip_seconds is None else output.styled("warn", slip_text(row.slip_seconds))
    counted = f"{row.tickets_ended}/{row.tickets_total}"
    found = row.shown_alert()
    return [
        output.styled("plan", glyph.rail), row.repo, output.styled("bold", output.short_run_id(row.run_id)),
        output.styled(STATE_ROLES.get(row.state, ""), row.state), output.styled("dim", counted) if row.ended else counted, eta, slip,
        "" if row.tokens_new is None else output.styled("dim", format_tokens(row.tokens_new)),
        "" if row.tokens_cache is None else output.styled("dim", format_tokens(row.tokens_cache)),
        output.styled("dim", sparkline(row.trend, largest, glyph)),
        "" if found is None else output.styled("warn", alert_text(found, glyph.idle)), row.do() or "",
    ]


def _totals(host: str, rows: Sequence[RunRow]) -> list[str]:
    """The cells of the first row of the host group: its name, and the totals of its runs with no `run-end` (`fleet_rules.totals`).

    The totals are the ended count summed, the latest ETA, and the tokens summed. A run that ended is out of every total.
    """
    total = fleet_rules.totals([row.facts() for row in rows])
    has_runs = any(row.ended is None for row in rows)
    return [
        host, "", "", "", f"{total.tickets_ended}/{total.tickets_total}" if has_runs else "", _clock(total.eta) if total.eta else "", "",
        "" if total.tokens_new is None else output.styled("dim", format_tokens(total.tokens_new)),
        "" if total.tokens_cache is None else output.styled("dim", format_tokens(total.tokens_cache)), "", "", "",
    ]


def window_label() -> str:
    """The window in hours, as the copy deck writes it: `6h`."""
    return f"{fleet_rules.WINDOW_SECONDS // 3600}h"


def _fleet_line(fleet: Fleet, width: int, glyph: output.Glyphs) -> str:
    """The first fleet line (C24): the runs that are reachable, the bar, the percentage, the tickets that ended, the latest ETA, and the tokens.

    The bar is the bar of the run line of `delegate watch`: 16 cells, and only on a terminal of `BAR_COLUMNS` or more. The
    ETA is `-` when no run has one, and the runs with no ETA are counted after it, when there is one. The tokens are
    two sums, `new` and `cache`, and no cost. They drop when the line is wider than `width` (#312), as the columns
    `new` and `cache` of the table do.
    """
    runs = [row.facts() for row in fleet.runs]
    total = fleet_rules.totals(runs)
    count, listed = fleet_rules.reachable(runs)
    head = f"runs  {count} of {listed} reachable"
    if not count:
        return head
    percent = fleet_rules.percent(total)
    progress = f"{percent}%"
    if width >= BAR_COLUMNS:
        filled = percent * BAR_CELLS // 100
        progress = output.styled("ok", glyph.fill * filled) + output.styled("dim", glyph.empty * (BAR_CELLS - filled)) + " " + progress
    dot = f" {glyph.pending} "
    eta = f"ETA {_clock(total.eta) if total.eta else '-'}" + (f"{dot}{total.without_eta} with no ETA" if total.without_eta else "")
    parts = [head, progress, f"{total.tickets_ended}/{total.tickets_total} ended", eta]
    line = output.GAP.join(parts)
    if total.tokens_new is not None and total.tokens_cache is not None:
        tokens = output.styled("dim", f"{format_tokens(total.tokens_new)} new{dot}{format_tokens(total.tokens_cache)} cache")
        if output.width(line + output.GAP + tokens) <= width:
            line += output.GAP + tokens
    return line


def _span_line(fleet: Fleet, width: int, glyph: output.Glyphs, timeline: bool = False) -> str | None:
    """The second fleet line (C24): the earliest start of the runs of the sums to now, and the runs in trouble. None with no run in the sums.

    The line ends with the trend (#310): the words `trend: tickets ended an hour, last 6h`, the sparkline of all the runs of the
    sums on a scale of its own largest cell, and the trend word when there is one. The limit of idle time is the stall limit of `delegate watch`.
    When the line is wider than `width`, the words and the sparkline drop and the trend word stays (#312), as the column `trend` of the table drops.

    With `timeline` (#314), the line has no sparkline: it ends with the trend word and the legend of the lanes (`_legend`).
    """
    runs = [row.facts() for row in fleet.runs]
    start = fleet_rules.earliest_start(runs)
    if start is None:
        return None
    parts = [output.styled("dim", f"{_clock(start)}{glyph.dash}{_clock(fleet.time)}")]
    trouble = fleet_rules.in_trouble(runs, IDLE_LIMIT_SECONDS)
    if trouble:
        parts.append(output.styled("warn", f"{glyph.idle} {trouble} in trouble"))
    cells = fleet_rules.trend_sum(runs)
    word = fleet_rules.trend_word(cells)
    if timeline:
        return output.GAP.join([*parts, *([output.styled("dim", word)] if word else []), output.styled("dim", _legend(width, glyph))])
    trend = f"trend: tickets ended an hour, last {window_label()} {glyph.pending} all {sparkline(cells, max(cells), glyph)}" + (f" {word}" if word else "")
    line = output.GAP.join([*parts, output.styled("dim", trend.rstrip(" "))])
    if output.width(line) > width:
        line = output.GAP.join([*parts, *([output.styled("dim", word)] if word else [])])
    return line


def _legend(width: int, glyph: output.Glyphs) -> str:
    """The legend of the lanes (copy deck W9.fleet.legend): what a ticket and the projection look like, and the minutes of one cell.

    The glyphs drop under `BAR_COLUMNS`, as the legend of the table does (C27).
    """
    cell = f"one cell is {fleet_rules.cell_minutes(width)}m"
    dot = f" {glyph.pending} "
    return f"{glyph.node} a ticket ended{dot}{glyph.empty} to the ETA{dot}{cell}" if width >= BAR_COLUMNS else cell


def legend_lines(runs: Sequence[RunRow], host: str, unreachable: bool = False) -> list[str]:
    """The legend (C27): one line for each word of the column `do` that the rows hold, once, with its command (copy deck W8.do.*).

    `host` is the name of this machine. `unreachable` is True when the table has the row of an unreachable host: its word `check host` is in the legend (#317).
    When a host other than `host` has a `do` word, the last line ends with `; on another host, put ssh <host> in front of each command` (#318, copy deck W8.legend.otherHost).
    """
    present = {row.do() for row in runs} | ({fleet_rules.CHECK_HOST} if unreachable else set())
    rows = [[word, COMMANDS[word]] for word in fleet_rules.DO_WORDS if word in present]
    if rows and (unreachable or any(row.host != host and row.do() for row in runs)):
        rows[-1][1] += f"; {OTHER_HOST}"
    return [line.rstrip(" ") for line in output.columns(rows)]


@dataclass(frozen=True)
class _Ended:
    """The one row that holds the runs that ended of one host, when the lines do not fit the height (C25): `✓ 1 built`, `✗ 1 failed`."""

    host: str
    runs: tuple[RunRow, ...]

    def text(self, glyph: output.Glyphs) -> str:
        """The glyph of the first state and the count of each state, as the fold row of `delegate watch` writes them: `✓ 1 built, 1 failed`.

        `built` comes first, then `failed`, then each other state by name. The glyph is `ok` for built and `bad` for failed.
        """
        counts = Counter(row.state for row in self.runs)
        order = [state for state in (BUILT, FAILED) if counts[state]] + sorted(state for state in counts if state not in (BUILT, FAILED))
        role, mark = {BUILT: ("ok", glyph.built), FAILED: ("bad", glyph.failed)}.get(order[0], ("dim", glyph.skipped))
        return output.styled(role, mark) + output.styled("dim", " " + ", ".join(f"{counts[state]} {state}" for state in order))


@dataclass(frozen=True)
class _Reason:
    """The row under the row of an unreachable host that says why the command could not read it (#323). Its text starts in the column `state`."""

    reason: str

    def text(self, glyph: output.Glyphs) -> str:
        return output.styled("warn", self.reason)


@dataclass(frozen=True)
class _Section:
    """One group of the table: its name, the runs under its row (for its row), and the items that show under its row.

    A host group (#322) holds the runs that its totals count, and the runs of an unreachable host. The group `ended` (`ended_group`) holds the runs
    that ended in the window, each with its host in the place of the rail, and it has a header and no totals. `lost` is the host when the command could
    not read it (#323): its first item is the row of the reason.
    """

    host: str
    runs: tuple[RunRow, ...]
    items: tuple[RunRow | _Ended | _Reason, ...]
    ended_group: bool = False
    lost: LostHost | None = None

    @property
    def unreachable(self) -> bool:
        return self.lost is not None

    def cells(self, now: datetime) -> list[str]:
        """The cells of the row of the group: the header `ended`, the totals of the host (`_totals`), or for an unreachable host `unreachable`,
        `heard <duration> ago` when it was heard from, and `check host` (C25, #317)."""
        if self.ended_group:
            return [output.styled("dim", ENDED_GROUP), *[""] * (len(COLUMNS) - 1)]
        if self.lost is None:
            return _totals(self.host, self.runs)
        heard = "" if self.lost.heard is None else output.styled("warn", heard_text(self.lost.heard, now))
        return [self.host, "", "", output.styled("warn", UNREACHABLE), *[""] * 6, heard, fleet_rules.CHECK_HOST]


def _candidates(fleet: Fleet) -> list[list[_Section]]:
    """The sections of the table for each step of the fit to the height (C25), from the most to the fewest rows.

    The rows under the row of a host are the runs that its totals count, and the runs of an unreachable host (#322). The runs that ended are in the
    group `ended` after the hosts. First each run has its row. Then the runs that ended fold into one row for each host. Then each host whose runs
    have no alert and no command folds to its totals row, with no run row and no row of ended runs. An unreachable host has an alert and a command on its row, so it does not fold.
    """
    groups: list[tuple[str, tuple[RunRow, ...], tuple[RunRow, ...], bool]] = []
    for host, rows in fleet.hosts():
        under = tuple(row for row in rows if row.unreachable or row.ended is None)
        done = tuple(row for row in rows if not (row.unreachable or row.ended is None))
        groups.append((host, under, done, bool(under) and not any(row.unreachable or row.alert() or row.do() for row in under)))

    def section(host: str, under: tuple[RunRow, ...], quiet: bool, fold_quiet: bool) -> _Section:
        lost = fleet.lost_host(host)
        if lost is not None:
            return _Section(host, under, (_Reason(lost.reason), *under), lost=lost)
        return _Section(host, under, () if fold_quiet and quiet else under)

    def build(fold_ended: bool, fold_quiet: bool) -> list[_Section]:
        sections = [section(host, under, quiet, fold_quiet) for host, under, _, quiet in groups if under or fleet.lost_host(host)]
        items: list[RunRow | _Ended] = []
        for host, _, done, quiet in groups:
            if done and not (fold_quiet and quiet):
                items += [_Ended(host, done)] if fold_ended else done
        return [*sections, *([_Section(ENDED_GROUP, (), tuple(items), True)] if items else [])]

    return [build(False, False), build(True, False), build(True, True)]


def _table(sections: Sequence[_Section], now: datetime, dropped: frozenset[str], glyph: output.Glyphs, largest: int) -> tuple[list[str], int]:
    """The lines of the table without the columns of `dropped`, and the width of the widest line that is not the row of the ended runs.

    The row of the ended runs has its text from the column `state`, over the cells that are blank on it. A blank line is between two groups.
    A row of the group `ended` has its host in the place of the rail.
    """
    keep = [index for index, name in enumerate(COLUMNS) if name not in dropped]
    right = {place for place, index in enumerate(keep) if index in NUMBERS}
    rail = output.styled("plan", glyph.rail)
    rows = [[output.styled("dim", COLUMNS[index]) for index in keep]]
    folds: dict[int, _Ended | _Reason] = {}
    for number, section in enumerate(sections):
        if number:
            rows.append([""] * len(keep))
        cells = section.cells(now)
        rows.append([cells[index] for index in keep])
        for item in section.items:
            if isinstance(item, RunRow):
                found = _cells(item, glyph, largest)
                if section.ended_group:
                    found[0] = item.host
                rows.append([found[index] for index in keep])
            else:
                folds[len(rows)] = item
                rows.append([rail if isinstance(item, _Reason) else item.host, *[""] * (len(keep) - 1)])
    lines = [line.rstrip(" ") for line in output.columns(rows, right=right)]
    widths = [max(output.width(row[place]) for row in rows) for place in range(len(keep))]
    state = keep.index(COLUMNS.index("state"))
    start = sum(widths[:state]) + len(output.GAP) * state
    for place, item in folds.items():
        lead = rail if isinstance(item, _Reason) else item.host
        lines[place] = lead + " " * (start - output.width(lead)) + item.text(glyph)
    return lines, max(output.width(line) for place, line in enumerate(lines) if place not in folds)


# The role and the glyph of each kind of cell of a lane (components.md C26). The life of the run has no role.
def _lane_marks(glyph: output.Glyphs) -> dict[str, tuple[str, str]]:
    return {
        fleet_rules.LIFE: ("", glyph.rule), fleet_rules.TICKET: ("ok", glyph.node), fleet_rules.DONE: ("ok", glyph.built),
        fleet_rules.FAIL: ("bad", glyph.failed), fleet_rules.NOW: ("now", glyph.now), fleet_rules.PROJECTION: ("dim", glyph.empty),
    }


def _lane_text(row: RunRow, fleet: Fleet, minutes: int, length: int, glyph: output.Glyphs) -> str:
    """The lane of one run, `length` cells wide (C26, #314): see `fleet_rules.lane`.

    A run that works (`fleet_rules.WORKING_STATES`) shows `▶` in the column of now, in `warn` when it has an alert, else in `now`. A run that
    ended ends with `✓` when it is built and `✗` when it is not. A stopped or halted run ends with `✗` at its newest event, and a
    paused run ends at its newest event with no mark. A run that failed at start is one `✗` in the cell of its log.
    """
    running = row.ended is None and row.state in fleet_rules.WORKING_STATES and not row.unreachable
    finish = row.ended or row.last_event
    outcome = None
    if row.unreachable:
        pass  # the last-known life of the run, up to the time that its host was heard from, and no mark of an end (#317)
    elif row.ended is not None:
        outcome = fleet_rules.DONE if row.state == BUILT else fleet_rules.FAIL
    elif row.state in fleet_rules.TROUBLE_STATES:
        outcome = fleet_rules.FAIL
    kinds = fleet_rules.lane(
        now=fleet.time, minutes=minutes, length=length, started=row.started, tickets=row.built, finish=finish, outcome=outcome,
        running=running, eta=row.eta,
    )
    marks = _lane_marks(glyph)
    marks[fleet_rules.RUNNING] = ("warn" if row.alert() else "now", glyph.running)
    marks[""] = ("", " ")
    return "".join(output.styled(marks[kind][0], marks[kind][1] * len(list(group))) for kind, group in groupby(kinds))


def _all_lane(fleet: Fleet, minutes: int, projection: int, glyph: output.Glyphs) -> str:
    """The lane of `all` (C26, #315): the sparkline of the tickets that ended built in each cell of the window, over the runs with no `run-end`,
    on a scale whose largest cell has the top level; then the column of now, and `░` to the latest ETA (`projection` cells).
    """
    times = [time for row in fleet.runs if row.ended is None and not row.unreachable for time in row.built]
    cells = fleet_rules.ticket_cells(times, fleet.time, minutes)
    return (
        output.styled("dim", sparkline(cells, max(cells), glyph)) + output.styled("now", glyph.now)
        + output.styled("dim", glyph.empty * projection)
    )


def _timeline(fleet: Fleet, width: int, glyph: output.Glyphs) -> list[str]:
    """The lines under the fleet lines of the timeline (C26, #314, #315): the header, a blank line, the lane of `all`, one lane for each run, and the footer.

    A line is the label of the run (`host repo run`, with the host on the first row and the rail under it), the lane, and
    `ended ETA state alert` as in the table. The lane is as long as the window, the column of now, and the projection to the latest ETA of the runs.
    The first lane is `all` (`_all_lane`), with the ended count and the latest ETA of the runs with no `run-end`.

    Under `BAR_COLUMNS` the label is the run id only, the host is a line over its lanes, the column `ETA` and the axis label drop, and so does the footer
    (`W9.footer`), as the legend of the table does.
    """
    compact = width < BAR_COLUMNS
    minutes = fleet_rules.cell_minutes(width)
    latest = fleet_rules.totals([row.facts() for row in fleet.runs]).eta
    projection = fleet_rules.projection_cells(latest, fleet.time, minutes)
    length = fleet_rules.window_cells(minutes) + 1 + projection
    rail = output.styled("plan", glyph.rail)
    summary = _totals(fleet.host, fleet.runs)

    def tail(ended: str, eta: str, state: str, alert: str) -> list[str]:
        return [ended, state, alert] if compact else [ended, eta, state, alert]

    def cells_of(row: RunRow) -> list[str]:
        """The cells of the row, with `unreachable` and `heard <duration> ago` for the run of an unreachable host (C26, #317)."""
        found = _cells(row, glyph, 0)
        if row.unreachable and row.heard is not None:
            found[3], found[10] = output.styled("warn", UNREACHABLE), output.styled("warn", heard_text(row.heard, fleet.time))
        return found

    shown = [(host, rows) for host, rows in fleet.hosts() if rows]  # a host that could not be read and has no last-known run has no lane
    groups = [[(row, cells_of(row)) for row in rows] for _, rows in shown]
    blank = [""] * (1 if compact else 3)
    label_rows = [[ALL] if compact else [ALL, "", ""]]
    lane_rows = [[_all_lane(fleet, minutes, projection, glyph), *tail(summary[4], summary[5], "", "")]]
    for number, group in enumerate(groups):
        if number and not compact:
            label_rows.append(blank)
            lane_rows.append([""] * 5)
        for place, (row, found) in enumerate(group):
            label_rows.append([found[2]] if compact else [row.host if place == 0 else rail, found[1], found[2]])
            lane_rows.append([_lane_text(row, fleet, minutes, length, glyph), *tail(found[4], found[5], found[3], found[10])])
    labels = output.columns(label_rows)
    label_width = max(output.width(label) for label in labels)
    header = [name for name in W9_HEADER if not compact or name != "ETA"]
    body = output.columns([["", *[output.styled("dim", name) for name in header]], *lane_rows])
    lead = "" if compact else output.styled("dim", f"last {window_label()}, to the latest ETA")
    lane_end = label_width + len(output.GAP) + length
    top = lead + " " * (lane_end - output.width(lead)) + body[0][length:]  # the header row from the gap before `ended`
    lanes = [label + " " * (label_width - output.width(label)) + output.GAP + line for label, line in zip(labels, body[1:])]
    if compact:
        placed = [lanes[0]]
        taken = 1
        for number, group in enumerate(groups):
            placed += [*([""] if number else []), shown[number][0], *lanes[taken:taken + len(group)]]
            taken += len(group)
        return [top, "", *placed]
    return [top, "", *lanes, "", FOOTER]


def settings_line(fleet: Fleet, glyph: output.Glyphs) -> str:
    """The header line of `delegate runs` (#323): the configuration file that the command read, the run index, and the other hosts that it read.

    `no configuration file` when there is none, `the index of this machine only` when no shared directory is set, and `no other host` when the command
    read none. With `--local`, the command reads no other host, and the line says so.
    """
    view = fleet.view
    config = "no configuration file" if view.config is None else f"config {view.config}"
    index = OWN_INDEX if view.shared_index is None else f"index {view.shared_index}"
    if not fleet.others_read:
        hosts = "--local: no other host is read"
    else:
        hosts = f"hosts {', '.join(fleet.read)}" if fleet.read else "no other host"
    return output.styled("dim", f" {glyph.pending} ".join([config, index, hosts]))


def fleet_lines(fleet: Fleet, width: int = BAR_COLUMNS, height: int | None = None, timeline: bool = False) -> list[str]:
    """The lines of `delegate runs`: the header line of the settings, the two fleet lines, a blank line, the table, and the legend.

    `width` and `height` are the size of the terminal. The bar of the first fleet line and the legend need `BAR_COLUMNS`. When a
    line of the table is wider than `width`, the columns drop in the order of `FIT_STEPS`. Each line is cut to `width`. When the
    lines are more than the lines of `height`, less one for the prompt, the rows fold (`_candidates`). With no `height`, no row folds.
    The table has the header, the row of the host with its totals, and one row for each listed run, with the rail under the name
    of the host. The legend has a blank line over it, and no line when the table has no command. With no listed run, there are no
    fleet lines, and the rest is the two lines of an empty machine (copy deck W8.empty and W8.empty.ended); under them is the table of each host that
    could not be read, when there is one (#323).

    With `timeline` (#314), the table and the legend are the lanes of `_timeline`; the empty machine has the same lines, and no row folds.
    """
    glyph = output.glyphs()
    index_line = [settings_line(fleet, glyph)]
    if not fleet.runs:
        head = [*index_line, f"runs  0 of {fleet.host_count} hosts have an active run", f"no run ended in the last {window_label()}"]
        if not fleet.lost or timeline:
            return [output.cut(line, max(width, 1)) for line in head]
    else:
        head = [line for line in (*index_line, _fleet_line(fleet, width, glyph), _span_line(fleet, width, glyph, timeline)) if line is not None]
    if timeline:
        return [output.cut(line.rstrip(" "), max(width, 1)) for line in [*head, "", *_timeline(fleet, width, glyph)]]
    largest = max((cell for row in fleet.runs for cell in row.trend), default=0)
    candidates = _candidates(fleet)
    for sections in candidates:
        shown = [item for section in sections for item in section.items if isinstance(item, RunRow)]
        legend = legend_lines(shown, fleet.host, any(section.unreachable for section in sections)) if width >= BAR_COLUMNS else []
        rows = sum(1 + len(section.items) for section in sections) + len(sections) - 1  # a row for each host, and a blank line between two hosts
        if height is None or len(head) + 1 + 1 + rows + (1 + len(legend) if legend else 0) <= height - 1:
            break
    for dropped in FIT_STEPS:
        table, measured = _table(sections, fleet.time, dropped, glyph, largest)
        if measured <= width:
            break
    return [output.cut(line, max(width, 1)) for line in [*head, "", *table, *([""] if legend else []), *legend]]
