"""`delegate runs --timeline`: the `all` lane, the 80-column form, the footer and the empty forms (#315, copy deck W9, components.md C26).

The clock of each case is `NOW`, 14:20 UTC, and the window starts at 08:20. At 120 columns one cell is 10 minutes, so cell `n`
holds the times from 08:20 + 10n minutes to the next cell, and the column of now is cell 36. At 80 columns one cell is 12 minutes,
the window has 30 cells, and the column of now is cell 30. Each expected line is written cell by cell from those times and from
the glyphs of `design/docs/tokens.md`, never from the code that makes them.
"""

from __future__ import annotations

import re
import sys

import pytest

from delegate.cli import delegate

from ..support import Terminal
from .journals import built, clock_time, run_end, runs, working, write_run

FOOTER = "detail  delegate watch <run>; on another host, ssh <host> delegate watch <run>"


def two_running_runs_and_an_ended_run(machine) -> None:
    """`gggg` ended tickets at 11:50, 12:30 and 13:30 (cells 21, 25, 31 at 10 minutes) and has the ETA 15:10; `hhhh` ended tickets at 12:35 and 13:00
    (cells 25 and 28) and has an earlier ETA. `bbbb` ended built at 13:00 with a ticket at 12:30: it has a `run-end`, so the `all` lane leaves it out."""
    write_run(machine, "gamma", "gggg", clock_time(11, 20), [
        *built("1", clock_time(11, 20), clock_time(11, 50)), *built("2", clock_time(12, 0), clock_time(12, 30)),
        *built("3", clock_time(13, 0), clock_time(13, 30)), working("4", clock_time(14, 10)),
    ], tickets=("1", "2", "3", "4", "5"), lock=True)
    write_run(machine, "delta", "hhhh", clock_time(12, 0), [
        *built("1", clock_time(12, 0), clock_time(12, 35)), *built("2", clock_time(12, 40), clock_time(13, 0)), working("3", clock_time(14, 0)),
    ], tickets=("1", "2", "3"), lock=True)
    write_run(machine, "beta", "bbbb", clock_time(12, 0), [*built("1", clock_time(12, 0), clock_time(12, 30)), (clock_time(13, 0), "run-end", {
        "result": "built", "built": ["1"], "failed": [], "skipped": [],
    })], tickets=("1",))


def test_the_all_lane_sums_the_tickets_of_the_runs_with_no_run_end_in_the_cells_that_hold_them(machine, capsys, monkeypatch):
    """Cell 25 holds two tickets (12:30 of `gggg`, 12:35 of `hhhh`), so it is the largest cell and gets the top level; cells 21, 28 and 31 hold one ticket each,
    level 4 of 7 (1 of a largest cell of 2, rounded up). The ended run `bbbb` has a ticket in cell 25: if it counted, that cell would hold 3."""
    monkeypatch.setenv("COLUMNS", "120")
    two_running_runs_and_an_ended_run(machine)

    code, out, err = runs(capsys, "--timeline")

    lines = out.splitlines()
    row = next(line for line in lines if line.startswith("all"))
    start = next(line for line in lines if "  gggg  " in line).index("  gggg  ") + len("  gggg  ")
    expected = " " * 21 + "▄" + " " * 3 + "▇" + " " * 2 + "▄" + " " * 2 + "▄" + " " * 4 + "┊" + "░" * 5
    assert (code, err) == (0, "")
    assert lines.index(row) == 6  # the first lane, over the lane of the first run
    assert row[start:start + len(expected)] == expected
    assert re.search(r"  5/8 {4}15:10$", row)  # 3/5 and 2/3 ended; the latest ETA, and no state and no alert
    assert not any(line.startswith("all") for line in lines if line is not row)


def test_the_all_lane_has_no_projection_when_no_run_has_an_eta_and_is_dim_in_colour(machine, monkeypatch):
    """A paused run has no ETA: the lane of `all` ends at the column of now. The sparkline and the projection are `dim` (SGR 2), the column of now is `now` (SGR 36)."""
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.setenv("DELEGATE_NO_TITLE", "1")
    write_run(machine, "gamma", "pppp", clock_time(12, 0), [
        *built("1", clock_time(12, 0), clock_time(12, 30)), (clock_time(13, 0), "paused", {"ticket": "2", "before": "step"}),
    ])
    out = Terminal()
    monkeypatch.setattr(sys, "stdout", out)

    assert delegate.main(["runs", "--timeline"]) == 0

    row = next(line for line in out.getvalue().splitlines() if line.startswith("all"))
    assert "\x1b[2m" + " " * 25 + "▇" in row  # hand-checked: cell 25 holds the one ticket, and it is the largest cell
    assert "\x1b[36m┊\x1b[0m" in row
    assert "░" not in row


def test_the_footer_names_the_command_of_the_detail_at_120_columns_and_drops_at_80(machine, capsys, monkeypatch):
    two_running_runs_and_an_ended_run(machine)
    monkeypatch.setenv("COLUMNS", "120")

    wide = runs(capsys, "--timeline")
    monkeypatch.setenv("COLUMNS", "80")
    narrow = runs(capsys, "--timeline")

    assert wide[0] == narrow[0] == 0
    assert wide[1].splitlines()[-2:] == ["", FOOTER]
    assert FOOTER not in narrow[1]
    assert "detail" not in narrow[1]


def test_at_80_columns_the_label_is_the_run_id_the_host_is_a_line_over_its_lanes_and_the_eta_drops(machine, capsys, monkeypatch):
    """E19 at 80 × 24: cells of 12 minutes, so the window has 30 cells from 08:20 and now is cell 30. `gggg` started at 11:20 (180 minutes: cell 15), ended tickets
    at 11:50, 12:30 and 13:30 (cells 17, 20, 25), and has the ETA 15:10, 50 minutes after now (4 cells of 12 minutes). `bbbb` started at 08:40 (cell 1) and
    ended built at 10:30 (130 minutes: cell 10)."""
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "24")
    write_run(machine, "gamma", "gggg", clock_time(11, 20), [
        *built("1", clock_time(11, 20), clock_time(11, 50)), *built("2", clock_time(12, 0), clock_time(12, 30)),
        *built("3", clock_time(13, 0), clock_time(13, 30)), working("4", clock_time(14, 10)),
    ], tickets=("1", "2", "3", "4", "5"), lock=True)
    write_run(machine, "beta", "bbbb", clock_time(8, 40), [run_end(clock_time(10, 30))])

    code, out, err = runs(capsys, "--timeline")

    lines = out.splitlines()
    all_lane = " " * 17 + "▇" + " " * 2 + "▇" + " " * 4 + "▇" + " " * 4 + "┊" + "░" * 4  # the 3 tickets of `gggg`; `bbbb` has a run-end
    life = " " * 15 + "──●──" + "●" + "────" + "●" + "────" + "▶" + "░" * 4
    assert (code, err) == (0, "")
    assert lines[3] == ""
    assert lines[4].split() == ["ended", "state", "alert"]  # no label, no ETA
    assert lines[5] == ""
    assert lines[6] == "all   " + all_lane + "  3/5"
    assert lines[7] == "laptop"
    assert lines[8] == "gggg  " + life + "  3/5    running"
    assert lines[9] == "bbbb  " + (" " + "─" * 9 + "✓" + " " * 19 + "┊").ljust(35) + "  0/2    built"
    assert len(lines) == 10
    assert all(len(line) <= 80 for line in lines)


def test_no_color_and_a_locale_that_is_not_utf8_give_no_escape_code_and_the_same_columns(machine, monkeypatch):
    """E20: the same frame in ASCII, on a terminal. The Unicode frame, with its codes removed and each glyph mapped to its ASCII glyph of tokens.md, is the ASCII frame."""
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.setenv("DELEGATE_NO_TITLE", "1")
    two_running_runs_and_an_ended_run(machine)

    def frame() -> str:
        out = Terminal()
        monkeypatch.setattr(sys, "stdout", out)
        assert delegate.main(["runs", "--timeline"]) == 0
        return out.getvalue()

    coloured = frame()
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("LC_ALL")
    monkeypatch.setenv("LANG", "C")
    plain = frame()

    ascii_of = str.maketrans("─●✓✗▶░┊│█⚠·–▁▂▃▄▅▆▇", "-*+x>.:|#!.-.:-=+*#")
    assert "\x1b" in coloured
    assert "\x1b" not in plain
    assert re.sub(r"\x1b\[[0-9;]*m", "", coloured).translate(ascii_of) == plain
    assert "=   #  =  =    :....." in next(line for line in plain.splitlines() if line.startswith("all"))  # levels 4, 7, 4, 4 of ".:-=+*#", now, the projection


def test_with_no_run_the_timeline_has_the_two_lines_of_an_empty_machine_at_80_columns_and_no_footer(machine, capsys):
    code, out, err = runs(capsys, "--timeline")

    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "no configuration file · the index of this machine only · no other host", "runs  0 of 1 hosts have an active run", "no run ended in the last 6h",
    ]


def test_a_run_that_ended_alone_still_has_the_all_lane_with_nothing_to_sum(machine, capsys, monkeypatch):
    """Every run in the window has a `run-end`: the lane of `all` has no ticket, no ended count and no ETA, as the totals row of the table has none."""
    monkeypatch.setenv("COLUMNS", "120")
    write_run(machine, "beta", "bbbb", clock_time(8, 40), [*built("1", clock_time(8, 50), clock_time(9, 40)), run_end(clock_time(10, 30))])

    code, out, err = runs(capsys, "--timeline")

    row = next(line for line in out.splitlines() if line.startswith("all"))
    assert (code, err) == (0, "")
    assert row.split() == ["all", "┊"]
    assert out.splitlines()[-1] == FOOTER


@pytest.mark.parametrize("columns", ["80", "120"])
def test_each_line_of_the_timeline_fits_the_terminal(machine, capsys, monkeypatch, columns):
    monkeypatch.setenv("COLUMNS", columns)
    two_running_runs_and_an_ended_run(machine)

    code, out, err = runs(capsys, "--timeline")

    assert (code, err) == (0, "")
    assert all(len(line) <= int(columns) for line in out.splitlines())
