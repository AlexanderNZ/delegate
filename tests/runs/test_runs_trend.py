"""`delegate runs`: the trend column of the table, and the trend of the second fleet line (#310).

The clock of each case is `NOW`, 14:20. The window is the 6 hours before it, so the 6 cells of a trend, oldest first, are
08:20–09:20, 09:20–10:20, 10:20–11:20, 11:20–12:20, 12:20–13:20 and 13:20–14:20. A case writes the journals of runs with
`journals.write_run`, calls `main(argv)`, and reads the table, the second fleet line and `--json`. Each expected cell
comes from the hours of the tickets in the case, and each expected glyph from `design/docs/tokens.md`, never from the
code that makes them.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from .journals import built, clock_time, runs, write_run


def at(hours: int, minutes: int) -> datetime:
    return clock_time(hours, minutes)


def tickets_ended_at(root, repo: str, short: str, times: list[datetime], started: datetime | None = None, extra=(), lock: bool = True) -> None:
    """A run with one ticket for each time: the ticket starts a minute before its time, and ends built at its time."""
    events = []
    for number, time in enumerate(times, start=1):
        events += built(str(number), time - timedelta(minutes=1), time)
    events += list(extra)
    write_run(root, repo, short, started or at(8, 0), events, tickets=tuple(str(n) for n in range(1, len(times) + 1)), lock=lock)


def trends(capsys) -> dict[str, list[int]]:
    code, out, err = runs(capsys, "--json")
    assert (code, err) == (0, "")
    return {run["run_id"].rsplit("-", 1)[-1]: run["trend"] for run in json.loads(out)["runs"]}


def test_each_ticket_counts_in_the_hour_that_it_ended_built_and_a_revived_ticket_counts_once(machine, capsys):
    """Tickets end at 09:00, 09:30, 09:50, 12:30, 13:30, 14:00 and 14:10: the cells are 1, 2, 0, 0, 1, 3.

    Ticket 1 is the one that ends at 09:00. The verification fails it at 09:10. A retry revives it, with a new
    `step-end` built at 13:40: the ticket counts once, in the hour of its first `step-end` built. Cell 0 is 1, and
    cell 5 is 3 and not 4.
    """
    times = [at(9, 0), at(9, 30), at(9, 50), at(12, 30), at(13, 30), at(14, 0), at(14, 10)]
    revived = (
        (at(9, 10), "step-end", {"ticket": "1", "state": "failed", "reason": "verification"}),
        (at(13, 35), "retry", {"ticket": "1", "phase": "specialist", "round": 0, "reason": "failed", "revived": ["1"]}),
        (at(13, 40), "step-end", {"ticket": "1", "state": "built", "reason": None}),
    )
    tickets_ended_at(machine, "alpha", "aaaa", times, extra=revived)

    assert trends(capsys) == {"aaaa": [1, 2, 0, 0, 1, 3]}


def test_a_ticket_that_ended_before_the_window_is_in_no_cell(machine, capsys):
    tickets_ended_at(machine, "alpha", "aaaa", [at(8, 10), at(8, 19), at(8, 21)], started=at(7, 0))

    assert trends(capsys) == {"aaaa": [1, 0, 0, 0, 0, 0]}


def table_row(out: str, short: str) -> str:
    return next(line for line in out.splitlines() if f" {short} " in line or line.split()[1:2] == [short])


def test_one_scale_serves_each_run_so_the_same_count_has_the_same_glyph_in_two_runs(machine, capsys):
    """Run A ends 4 tickets in the last cell, and run B ends 1: the largest cell is 4, so A shows ▇ (4 of 4) and B shows ▂ (1 of 4 is 2 of 7 steps, rounded up)."""
    tickets_ended_at(machine, "alpha", "aaaa", [at(13, 30), at(13, 40), at(13, 50), at(14, 0)])
    tickets_ended_at(machine, "beta", "bbbb", [at(14, 10)])

    code, out, err = runs(capsys)

    row_a, row_b = table_row(out, "aaaa"), table_row(out, "bbbb")
    assert (code, err) == (0, "")
    assert "▇" in row_a and "▂" in row_b and "▇" not in row_b
    assert row_a.index("▇") == row_b.index("▂")


def test_a_cell_with_no_ticket_is_blank_and_the_second_line_has_the_sparkline_of_the_runs_with_no_run_end(machine, capsys):
    """One run ends tickets at 09:00, 11:30, 13:30 and 14:00: its cells are 1, 0, 0, 1, 0, 2. The largest cell is 2, so 1 is ▄ (4 of 7) and 2 is ▇.

    An ended run has 3 tickets in the last hour, and it is out of the `all` sparkline. The last 2 hours have 2
    tickets and the 2 hours before have 1, and 2 is more than 1.25 × 1. The table has one scale for both runs, and its
    largest cell is 3: the run with no `run-end` shows 1 as ▃ (3 of 7) and 2 as ▅ (5 of 7), and the ended run shows ▇.
    """
    tickets_ended_at(machine, "alpha", "aaaa", [at(9, 0), at(11, 30), at(13, 30), at(14, 0)])
    other = at(10, 0)
    tickets_ended_at(machine, "omega", "oooo", [at(13, 40), at(13, 50), at(14, 0)], started=other, extra=[(at(14, 5), "run-end", {"result": "built", "built": ["1"], "failed": [], "skipped": []})], lock=False)

    _, out, _ = runs(capsys)
    lines = out.splitlines()

    assert lines[2] == "08:00–14:20  trend: tickets ended an hour, last 6h · all ▄  ▄ ▇ speeding up"
    assert lines[2].count("▇") == 1
    assert table_row(out, "aaaa").endswith("▃  ▃ ▅") or "▃  ▃ ▅" in table_row(out, "aaaa")
    assert "      ▇" in table_row(out, "oooo")


@pytest.mark.parametrize(
    ("before", "recent", "word"),
    [
        (2, 3, "speeding up"),   # 3 is more than 1.25 × 2
        (4, 6, "speeding up"),   # 6 is more than 1.25 × 4 = 5
        (0, 1, "speeding up"),   # any ticket after none
        (4, 5, "steady"),        # 5 is exactly 1.25 × 4: not more
        (4, 3, "steady"),        # 3 is exactly 0.75 × 4: not fewer
        (3, 3, "steady"),
        (4, 2, "slowing"),       # 2 is fewer than 0.75 × 4 = 3
        (1, 0, "slowing"),       # no ticket after some
    ],
)
def test_the_trend_word_compares_the_last_2_hours_with_the_2_hours_before(machine, capsys, before, recent, word):
    """`before` tickets end in the hour 11:20–12:20 and `recent` tickets end in the hour 13:20–14:20."""
    times = [at(11, 30) + timedelta(minutes=number) for number in range(before)] + [at(13, 30) + timedelta(minutes=number) for number in range(recent)]
    tickets_ended_at(machine, "alpha", "aaaa", times)

    _, out, _ = runs(capsys)

    assert out.splitlines()[2].endswith(f" {word}")


def test_no_word_shows_when_the_last_4_hours_have_no_ticket(machine, capsys):
    tickets_ended_at(machine, "alpha", "aaaa", [at(9, 0)])

    _, out, _ = runs(capsys)

    assert out.splitlines()[2] == "08:00–14:20  trend: tickets ended an hour, last 6h · all ▇"


def test_the_ascii_glyphs_take_the_same_columns_when_the_locale_is_not_utf8(machine, capsys, monkeypatch):
    """The scale is the one of tokens.md: ▁▂▃▄▅▆▇ is .:-=+*# . Cells 1, 0, 0, 1, 0, 2 give `=  = #`."""
    tickets_ended_at(machine, "alpha", "aaaa", [at(9, 0), at(11, 30), at(13, 30), at(14, 0)])
    _, unicode_out, _ = runs(capsys)
    monkeypatch.setenv("LC_ALL", "C")

    _, ascii_out, _ = runs(capsys)

    assert ascii_out.splitlines()[2] == "08:00-14:20  trend: tickets ended an hour, last 6h . all =  = # speeding up"
    row_unicode, row_ascii = table_row(unicode_out, "aaaa"), table_row(ascii_out, "aaaa")
    assert "=  = #" in row_ascii
    assert row_ascii.index("=  = #") == row_unicode.index("▄  ▄ ▇")
    assert len(row_ascii) == len(row_unicode)


def test_a_run_with_no_ticket_ended_has_six_zero_cells_in_the_json(machine, capsys):
    write_run(machine, "alpha", "aaaa", at(13, 0), [], lock=True)

    assert trends(capsys) == {"aaaa": [0, 0, 0, 0, 0, 0]}
