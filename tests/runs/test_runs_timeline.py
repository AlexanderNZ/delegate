"""`delegate runs --timeline`: one lane for each run over the window, to the latest ETA (#314, copy deck W9, components.md C26).

The clock of each case is `NOW`, 14:20 UTC, and the window is the 6 hours before it: it starts at 08:20. At 120 columns one
cell is 10 minutes, so cell `n` holds the times from 08:20 + 10n minutes to the next cell, the window has 36 cells (0 to 35),
the column of now is cell 36, and the projection is the cells after it. Each expected lane is written cell by cell from
those times and from the glyphs of `design/docs/tokens.md`, never from the code that makes them.
"""

from __future__ import annotations

import json
import sys

import pytest

from delegate.cli import delegate

from ..support import Terminal
from .journals import built, clock_time, run_end, runs, working, write_run
from ..support_runs import agent_idle

LIFE, TICKET, NOW_COLUMN, PROJECTION = "─", "●", "┊", "░"


@pytest.fixture(autouse=True)
def wide(monkeypatch):
    """A terminal of 120 columns: one cell is 10 minutes."""
    monkeypatch.setenv("COLUMNS", "120")


def lane_of(out: str, short: str) -> str:
    """The cells of the lane of the run `short`: the 42 characters of the lane column of its row (36 cells of the window, now, and the projection)."""
    row = next(line for line in out.splitlines() if f"{short}  " in line)
    start = row.index(f"{short}  ") + len(f"{short}  ")
    return row[start:]


def test_a_run_that_started_3_hours_ago_has_its_life_its_tickets_now_and_the_projection_to_its_eta(machine, capsys):
    """Run `gggg` started at 11:20 (cell 18). Tickets 1, 2 and 3 end built at 11:50, 12:30 and 13:30 (cells 21, 25 and 31).
    Ticket 4 started at 14:10, ticket 5 waits: 3 of 5 ended, each ticket took 30 minutes, so the ETA is 15:10, 50 minutes after now (5 cells)."""
    events = [
        *built("1", clock_time(11, 20), clock_time(11, 50)), *built("2", clock_time(12, 0), clock_time(12, 30)),
        *built("3", clock_time(13, 0), clock_time(13, 30)), working("4", clock_time(14, 10)),
    ]
    write_run(machine, "gamma", "gggg", clock_time(11, 20), events, tickets=("1", "2", "3", "4", "5"), lock=True)

    code, out, err = runs(capsys, "--timeline")

    assert (code, err) == (0, "")
    expected = " " * 18 + LIFE * 3 + TICKET + LIFE * 3 + TICKET + LIFE * 5 + TICKET + LIFE * 4 + "▶" + PROJECTION * 5
    assert len(expected) == 42
    assert next(line for line in out.splitlines() if " gggg " in line) == f"laptop  gamma  gggg  {expected}  3/5    15:10  running"


def test_a_run_that_ended_ends_its_lane_with_a_check_in_the_cell_of_its_end_and_a_failed_run_with_a_cross(machine, capsys):
    """`bbbb` started at 08:40 (cell 2), a ticket ended at 09:40 (cell 8), and the run ended built at 10:30 (cell 13): the check is in cell 13.
    `ffff` started at 12:00 (cell 22) and ended failed at 13:05 (cell 28): the cross is in cell 28."""
    write_run(machine, "beta", "bbbb", clock_time(8, 40), [*built("1", clock_time(8, 50), clock_time(9, 40)), run_end(clock_time(10, 30))])
    failed = (clock_time(13, 5), "run-end", {"result": "failed", "built": [], "failed": ["1"], "skipped": []})
    write_run(machine, "beta", "ffff", clock_time(12, 0), [working("1", clock_time(12, 0)), failed])

    code, out, err = runs(capsys, "--timeline")

    assert (code, err) == (0, "")
    assert lane_of(out, "bbbb").startswith("  " + LIFE * 6 + TICKET + LIFE * 4 + "✓" + " " * 22 + NOW_COLUMN)
    assert lane_of(out, "ffff").startswith(" " * 22 + LIFE * 6 + "✗" + " " * 7 + NOW_COLUMN)


def test_a_stopped_run_ends_with_a_cross_at_its_last_event_and_a_paused_run_has_no_mark_at_its_end(machine, capsys):
    """`ssss` has no process and no run-end: it stopped at its last event, 10:00 (cell 10). `pppp` paused at 10:30 (cell 13): its life ends there with no mark."""
    write_run(machine, "gamma", "ssss", clock_time(9, 0), [working("1", clock_time(9, 0)), (clock_time(10, 0), "agent-start", {
        "ticket": "1", "role": "specialist", "round": 0, "event_stream": "/s/1.stream.jsonl",
    })])
    write_run(machine, "gamma", "pppp", clock_time(10, 0), [(clock_time(10, 30), "paused", {"ticket": "1", "before": "step"})])

    code, out, err = runs(capsys, "--timeline")

    assert (code, err) == (0, "")
    assert lane_of(out, "ssss").startswith(" " * 4 + LIFE * 6 + "✗" + " " * 25 + NOW_COLUMN)
    assert lane_of(out, "pppp").startswith(" " * 10 + LIFE * 4 + " " * 22 + NOW_COLUMN)


def test_the_column_of_now_is_the_same_in_each_lane_whether_the_run_runs_is_paused_or_ended(machine, capsys):
    started = clock_time(13, 0)
    write_run(machine, "gamma", "gggg", started, [*built("1", started, clock_time(13, 30)), working("2", clock_time(14, 10))], tickets=("1", "2", "3"), lock=True)
    write_run(machine, "gamma", "pppp", clock_time(10, 0), [(clock_time(10, 30), "paused", {"ticket": "1", "before": "step"})])
    write_run(machine, "beta", "bbbb", clock_time(8, 40), [*built("1", clock_time(8, 50), clock_time(9, 40)), run_end(clock_time(10, 30))])

    code, out, err = runs(capsys, "--timeline")

    rows = {short: next(line for line in out.splitlines() if f"  {short}  " in line) for short in ("gggg", "pppp", "bbbb")}
    assert (code, err) == (0, "")
    assert rows["gggg"].index("▶") == rows["pppp"].index(NOW_COLUMN) == rows["bbbb"].index(NOW_COLUMN)
    assert rows["gggg"].index("▶") - rows["gggg"].index("gggg") == len("gggg  ") + 36  # two spaces, then the 36 cells of the window


def test_a_running_run_with_an_alert_draws_its_now_mark_in_warn_and_one_without_draws_it_in_now(machine, monkeypatch):
    """The role `warn` is SGR 33 and the role `now` is SGR 36 (tokens.md). `gggg` has an agent idle for 10 minutes, and `hhhh` has none."""
    monkeypatch.setenv("DELEGATE_NO_TITLE", "1")
    started = clock_time(13, 0)
    write_run(machine, "gamma", "gggg", started, [working("1", started), agent_idle("1", clock_time(14, 10))], adapter="claude-code", lock=True)
    write_run(machine, "delta", "hhhh", started, [working("1", clock_time(14, 15))], lock=True)
    out = Terminal()
    monkeypatch.setattr(sys, "stdout", out)

    assert delegate.main(["runs", "--timeline"]) == 0

    rows = {short: next(line for line in out.getvalue().splitlines() if f"{short}" in line and "▶" in line) for short in ("gggg", "hhhh")}
    assert "\x1b[33m▶" in rows["gggg"] and "\x1b[36m▶" not in rows["gggg"]
    assert "\x1b[36m▶" in rows["hhhh"] and "\x1b[33m▶" not in rows["hhhh"]


def test_the_second_fleet_line_ends_with_the_legend_and_the_axis_label_heads_the_lanes(machine, capsys):
    started = clock_time(13, 0)
    write_run(machine, "gamma", "gggg", started, [*built("1", started, clock_time(13, 30)), working("2", clock_time(14, 10))], tickets=("1", "2", "3"), lock=True)

    code, out, err = runs(capsys, "--timeline")

    lines = out.splitlines()
    assert (code, err) == (0, "")
    assert lines[2] == "13:00–14:20  speeding up  ● a ticket ended · ░ to the ETA · one cell is 10m"
    assert lines[4].startswith("last 6h, to the latest ETA")
    assert lines[4].split() == ["last", "6h,", "to", "the", "latest", "ETA", "ended", "ETA", "state", "alert"]


def test_the_cell_is_12_minutes_and_the_legend_loses_its_glyphs_at_80_columns(machine, capsys, monkeypatch):
    """At 80 columns the window is 30 cells of 12 minutes: a ticket that ended at 13:30 (310 minutes after 08:20, cell 25) is in cell 25, the run started in cell 23, and now is cell 30."""
    monkeypatch.setenv("COLUMNS", "80")
    started = clock_time(13, 0)
    write_run(machine, "gamma", "gggg", started, [*built("1", started, clock_time(13, 30)), working("2", clock_time(14, 10))], tickets=("1", "2", "3"), lock=True)

    code, out, err = runs(capsys, "--timeline")

    assert (code, err) == (0, "")
    assert out.splitlines()[2] == "13:00–14:20  speeding up  one cell is 12m"
    assert lane_of(out, "gggg").startswith(" " * 23 + LIFE * 2 + TICKET + LIFE * 4 + "▶" + PROJECTION * 4)


def test_the_lanes_use_the_ascii_glyphs_of_tokens_md_in_a_locale_that_is_not_utf8(machine, capsys, monkeypatch):
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("NO_COLOR", "1")
    started = clock_time(13, 0)
    write_run(machine, "gamma", "gggg", started, [*built("1", started, clock_time(13, 30)), working("2", clock_time(14, 10))], tickets=("1", "2", "3"), lock=True)
    write_run(machine, "beta", "bbbb", clock_time(8, 40), [run_end(clock_time(10, 30))])

    code, out, err = runs(capsys, "--timeline")

    assert (code, err) == (0, "")
    assert out.splitlines()[2] == "13:00-14:20  speeding up  * a ticket ended . . to the ETA . one cell is 10m"
    assert lane_of(out, "gggg").startswith(" " * 28 + "-" * 3 + "*" + "-" * 4 + ">" + "." * 5)
    assert lane_of(out, "bbbb").startswith("  " + "-" * 11 + "+" + " " * 22 + ":")


def test_timeline_cannot_be_used_with_json(machine, capsys):
    with pytest.raises(SystemExit) as stop:
        runs(capsys, "--timeline", "--json")

    assert stop.value.code == 2
    assert "--timeline cannot be used with --json" in capsys.readouterr().err


def test_with_no_run_the_timeline_has_the_empty_form_of_the_table(machine, capsys):
    code, out, err = runs(capsys, "--timeline")

    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "no configuration file · the index of this machine only · no other host", "runs  0 of 1 hosts have an active run", "no run ended in the last 6h",
    ]
    assert json.loads(runs(capsys, "--json")[1])["runs"] == []
