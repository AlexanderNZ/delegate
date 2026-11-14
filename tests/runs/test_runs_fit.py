"""`delegate runs` fits the terminal and has its empty forms (#312): the frames E15, E16 and E17 of the design.

The spec is `design/docs/components.md` (C24 and C25) and the copy deck (W8.empty, W8.empty.ended, W8.noIndex). A case writes
the journals of runs with `journals.write_run`, sets the size of the terminal (`COLUMNS` and `LINES`) and calls `main(argv)`.
The expected columns and rows come from the frames and from the ticket, not from the code. A standard output that is a
terminal is the `Terminal` of the support module.
"""

from __future__ import annotations

import sys
from datetime import timedelta

import pytest

from delegate.cli import delegate

from ..support import Terminal
from .journals import NOW, built, clock_time, run_end, runs, working, write_run
from .test_runs_alert import agent_idle

NO_INDEX = "no run index is set up: these are the runs of this machine"


def at_size(monkeypatch, columns: int, lines: int = 1000) -> None:
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(lines))


def four_kinds(root) -> None:
    """Four runs of one machine: a run that works, a run with an idle agent, a paused run, and a run that ended built."""
    write_run(root, "delegate", "3c98", clock_time(13, 0), [working("1", clock_time(14, 0))], lock=True)
    write_run(root, "web-app", "1195", clock_time(13, 1), [working("1", clock_time(14, 0)), agent_idle("1", clock_time(14, 10))],
              adapter="claude-code", lock=True)
    write_run(root, "api-service", "36c5", clock_time(13, 2), [(clock_time(13, 40), "paused", {"ticket": "1", "before": "step"})])
    started = clock_time(13, 3)
    write_run(root, "delegate", "e2b1", started, [*built("1", started, clock_time(13, 30)), run_end(clock_time(13, 30))], tickets=("1",))


def busy(root, count: int, first: str = "w") -> None:
    """`count` runs that work, one in each repository: the lock of a run branch is the lock of its repository, so one repository holds one live run."""
    for number in range(count):
        write_run(root, f"app{number:02d}", f"{first}{number:03d}", clock_time(13, 0) + timedelta(minutes=number), [working("1", clock_time(14, 0))], lock=True)


def ended(root, count: int) -> None:
    """`count` runs that ended built at 13:00."""
    for number in range(count):
        started = clock_time(12, 0) + timedelta(minutes=number)
        write_run(root, f"done{number:02d}", f"b{number:03d}", started, [*built("1", started, clock_time(13, 0)), run_end(clock_time(13, 0))], tickets=("1",))


def header_of(out: str) -> list[str]:
    (line,) = [line for line in out.splitlines() if line.startswith("host")]
    return line.split()


# The table of `four_kinds` is 95 cells wide with each column. Each step of the fit takes the columns that the design drops
# and the cells that they hold, with the 2 cells of the gap: `new` and `cache` 12 (83 cells left), `trend` 8 (75), `slip` 6 (69), `repo` 13 (56).
ALL = ["host", "repo", "run", "state", "ended", "ETA", "slip", "new", "cache", "trend", "alert", "do"]


@pytest.mark.parametrize(
    "columns, header",
    [
        (95, ALL),
        (94, [name for name in ALL if name not in ("new", "cache")]),
        (83, [name for name in ALL if name not in ("new", "cache")]),
        (82, [name for name in ALL if name not in ("new", "cache", "trend")]),
        (75, [name for name in ALL if name not in ("new", "cache", "trend")]),
        (74, [name for name in ALL if name not in ("new", "cache", "trend", "slip")]),
        (69, [name for name in ALL if name not in ("new", "cache", "trend", "slip")]),
        (68, [name for name in ALL if name not in ("new", "cache", "trend", "slip", "repo")]),
        (56, [name for name in ALL if name not in ("new", "cache", "trend", "slip", "repo")]),
    ],
)
def test_the_columns_drop_in_the_order_new_and_cache_then_trend_then_slip_then_repo_until_the_table_fits(machine, monkeypatch, capsys, columns, header):
    four_kinds(machine)
    at_size(monkeypatch, columns)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert header_of(out) == header


def e15_runs(root) -> None:
    """14 runs that work and 5 runs that ended, each in a repository of its own, with a repository name of 30 cells.

    2 runs have an idle agent, 4 are paused, and 8 run with nothing wrong. The 6 lines over the rows, the row of the host and the
    19 rows are 26 lines: more than the 23 lines of a screen of 24 lines.
    """
    for number in range(14):
        repo, short = f"repository-with-a-long-name-{number:02d}", f"w{number:03d}"
        started = clock_time(13, 0) + timedelta(minutes=number)
        if number < 2:
            write_run(root, repo, short, started, [working("1", clock_time(14, 0)), agent_idle("1", clock_time(14, 10))], adapter="claude-code", lock=True)
        elif number < 6:
            write_run(root, repo, short, started, [(clock_time(13, 40), "paused", {"ticket": "1", "before": "step"})])
        else:
            write_run(root, repo, short, started, [working("1", clock_time(14, 0))], lock=True)
    for number in range(5):
        started = clock_time(12, 0) + timedelta(minutes=number)
        write_run(root, f"done{number:02d}", f"b{number:03d}", started, [*built("1", started, clock_time(13, 0)), run_end(clock_time(13, 0))], tickets=("1",))


def test_a_frame_of_80_by_24_has_the_columns_of_e15_and_folds_the_ended_runs_into_one_row(machine, monkeypatch, capsys):
    e15_runs(machine)
    at_size(monkeypatch, 80, 24)

    code, out, err = runs(capsys)

    lines = out.splitlines()
    assert (code, err) == (0, "")
    assert len(lines) == 21  # 6 lines, 14 rows, and the row of the ended runs: the 23 lines of the screen are enough
    assert lines[:2] == [NO_INDEX, "runs  14 of 14 reachable  0%  0/28 ended  ETA - · 14 with no ETA"]  # no bar
    assert lines[4:8] == [
        "host    run   state    ended  ETA  alert          do",  # `new`, `cache`, `trend`, `slip` and `repo` are not there
        "laptop                 0/28",
        "│       w013  running  0/2    -",
        "│       w012  running  0/2    -",
    ]
    assert [line.split()[-2:] for line in lines[6:20] if "idle" in line] == [["10m00s", "watch"]] * 2
    assert lines[-1] == "│" + " " * 13 + "✓ 5 built"  # from the column `state`
    assert "delegate watch <run>" not in out  # the legend drops under 100 columns
    assert "b000" not in out


def test_without_the_fold_the_frame_of_80_by_24_has_more_lines_than_the_screen(machine, monkeypatch, capsys):
    e15_runs(machine)
    at_size(monkeypatch, 80, 1000)

    _, out, _ = runs(capsys)

    assert len(out.splitlines()) == 25 and "b000" in out


def test_more_runs_than_rows_at_120_columns_fold_the_ended_runs_first_and_no_run_that_works(machine, monkeypatch, capsys):
    busy(machine, 4)
    ended(machine, 3)
    at_size(monkeypatch, 120, 1000)
    unfolded = runs(capsys)[1].splitlines()
    at_size(monkeypatch, 120, 12)  # 11 lines for the output: the 13 lines of the table and the lines over it do not fit

    code, out, err = runs(capsys)

    lines = out.splitlines()
    assert (code, err) == (0, "")
    assert len(unfolded) == 15 and unfolded[-2:] == ["", "merge  git merge --ff-only <run branch>"]
    assert [line.split()[2] for line in lines[6:10]] == ["w003", "w002", "w001", "w000"]
    assert lines[10] == "│                    ✓ 3 built"
    assert len(lines) == 11
    assert "merge" not in out  # the words of the legend are the words of the rows that show


def test_a_host_with_no_alert_and_no_command_folds_to_its_totals_row_when_the_ended_fold_is_not_enough(machine, monkeypatch, capsys):
    busy(machine, 8)
    ended(machine, 1)
    at_size(monkeypatch, 120, 10)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[4:] == ["host    repo  run  state  ended  ETA  slip  new  cache  trend  alert  do", "laptop" + " " * 20 + "0/16"]


def test_a_host_with_an_alert_does_not_fold_to_its_totals_row(machine, monkeypatch, capsys):
    four_kinds(machine)
    busy(machine, 8)
    at_size(monkeypatch, 120, 10)

    code, out, err = runs(capsys)

    lines = out.splitlines()
    assert (code, err) == (0, "")
    assert len(lines) == 21  # the lines do not fit, and nothing more can fold: the ended run is the only one that folds. The legend has 3 lines

    assert [line.split()[2] for line in lines[6:17]].count("1195") == 1 and "w000" in out
    assert lines[-4] == "│                          ✓ 1 built"


def test_the_fleet_lines_drop_the_tokens_and_the_words_of_the_trend_when_they_do_not_fit_the_width(machine, monkeypatch, capsys):
    started = clock_time(13, 40)
    write_run(machine, "alpha", "aaaa", started, [*built("1", started, clock_time(14, 0), tokens=(12_000, 340_000)), working("2", clock_time(14, 0))],
              adapter="claude-code", lock=True)
    at_size(monkeypatch, 120)
    wide = runs(capsys)[1].splitlines()
    at_size(monkeypatch, 70)

    _, out, _ = runs(capsys)

    assert wide[1:3] == [
        "runs  1 of 1 reachable  ████████░░░░░░░░ 50%  1/2 ended  ETA - · 1 with no ETA  12k new · 340k cache",
        "13:40–14:20  trend: tickets ended an hour, last 6h · all      ▇ speeding up",
    ]
    assert out.splitlines()[1:3] == ["runs  1 of 1 reachable  50%  1/2 ended  ETA - · 1 with no ETA", "13:40–14:20  speeding up"]


def test_each_line_is_cut_to_the_width_of_the_terminal(machine, monkeypatch, capsys):
    four_kinds(machine)
    at_size(monkeypatch, 30)

    _, out, _ = runs(capsys)

    assert max(len(line) for line in out.splitlines()) == 30


def test_a_machine_with_no_run_prints_the_line_of_the_index_and_the_two_empty_lines_of_the_copy_deck(machine, capsys):
    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines() == [NO_INDEX, "runs  0 of 1 hosts have an active run", "no run ended in the last 6h"]


def test_the_line_of_the_missing_index_is_the_first_line_of_a_table_with_runs(machine, capsys):
    four_kinds(machine)

    _, out, _ = runs(capsys)

    assert out.splitlines()[0] == NO_INDEX
    assert out.splitlines()[1].startswith("runs  3 of 3 reachable")


# The Unicode glyph and the ASCII glyph of each glyph that the table can hold (tokens.md).
ASCII_OF = str.maketrans({"│": "|", "–": "-", "·": ".", "⚠": "!", "█": "#", "░": ".", "✓": "+", "✗": "x", **dict(zip("▁▂▃▄▅▆▇", ".:-=+*#"))})


def on_a_terminal(monkeypatch, columns: int) -> Terminal:
    at_size(monkeypatch, columns)
    out = Terminal()
    monkeypatch.setattr(sys, "stdout", out)
    return out


def test_a_terminal_gets_escape_codes_and_no_color_with_a_c_locale_gets_none_and_the_same_columns_in_ascii(machine, monkeypatch):
    four_kinds(machine)
    monkeypatch.setenv("DELEGATE_NO_TITLE", "1")  # the title of the terminal is an OSC 2 sequence, and its switch is not `NO_COLOR` (tokens.md)
    colour = on_a_terminal(monkeypatch, 120)
    assert delegate.main(["runs"]) == 0
    assert "\x1b[" in colour.getvalue()  # the case can see an escape code

    monkeypatch.setenv("NO_COLOR", "1")
    plain = on_a_terminal(monkeypatch, 120)
    assert delegate.main(["runs"]) == 0
    monkeypatch.delenv("LC_ALL")
    monkeypatch.setenv("LANG", "C")
    ascii_out = on_a_terminal(monkeypatch, 120)
    assert delegate.main(["runs"]) == 0

    assert "\x1b" not in plain.getvalue() and "\x1b" not in ascii_out.getvalue()
    assert ascii_out.getvalue() == plain.getvalue().translate(ASCII_OF)  # each glyph has the width of its Unicode glyph
    assert ascii_out.getvalue().isascii()
    assert "! idle 10m00s" in ascii_out.getvalue() and "|       web-app" in ascii_out.getvalue()


def test_the_fold_row_of_the_ended_runs_has_the_ascii_glyph_in_a_c_locale(machine, monkeypatch, capsys):
    started = clock_time(12, 0)
    write_run(machine, "delegate", "e2b1", started, [*built("1", started, clock_time(13, 0)), run_end(clock_time(13, 0))], tickets=("1",))
    write_run(machine, "delegate", "f00d", started, [working("1", started), (clock_time(13, 0), "run-end", {"result": "failed", "built": [], "failed": ["1"], "skipped": []})], tickets=("1",))
    monkeypatch.delenv("LC_ALL")
    monkeypatch.setenv("LANG", "C")
    at_size(monkeypatch, 120, 8)

    _, out, _ = runs(capsys)

    assert out.splitlines()[-1] == "|" + " " * 18 + "+ 1 built, 1 failed"
