"""`delegate runs`: the two fleet lines above the table, and the totals on the first row of the host (#308).

A case writes the journals of runs with `journals.write_run`, as the cases of the table do, and calls `main(argv)`. The
terminal is 120 columns wide, so the bar shows; a case that needs another width sets `COLUMNS`. Each expected line
comes from the design (`design/docs/components.md`, C24, and the copy deck, W8.fleet.line) and from the sums of the
tickets of the case that the docstring of the case names, never from the code that makes the line.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

from .journals import NOW, agent_started, built, clock_time, run_end, runs, working, write_run


@pytest.fixture(autouse=True)
def wide(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "120")


def one_running(root, repo: str, short: str, started, ended: int, total: int, **options) -> None:
    """A run that works: `ended` tickets of `total` ended built, a ticket runs now, and a process holds the lock."""
    tickets = tuple(str(number) for number in range(1, total + 1))
    events = []
    for number in range(1, ended + 1):
        events += built(str(number), started, started + timedelta(minutes=10 * number), **options.get("built", {}))
    events.append(working(str(ended + 1), clock_time(14, 10)))
    write_run(root, repo, short, started, events, tickets=tickets, lock=True, adapter=options.get("adapter", "scripted"))


def test_the_percentage_is_the_sum_of_the_tickets_that_ended_over_the_sum_of_the_tickets_and_not_the_mean_of_the_percentages(machine, capsys):
    """Three runs end 3 of 4, 1 of 10 and 1 of 10 tickets: 5 of 24 is 20%. The mean of 75%, 10% and 10% is 31%."""
    one_running(machine, "alpha", "aaaa", clock_time(13, 0), 3, 4)
    one_running(machine, "beta", "bbbb", clock_time(13, 5), 1, 10)
    one_running(machine, "gamma", "cccc", clock_time(13, 10), 1, 10)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[1].startswith("runs  3 of 3 reachable  ███░░░░░░░░░░░░░ 20%  5/24 ended  ETA ")


def three_kinds(root) -> None:
    """Run A ends 1 of 3 tickets, with a latest ETA 15:10. Run B ends 1 of 2, with ETA 14:35. Run C is paused after 1 of 2 tickets. Run D has 0 of 2.

    The ticket of run A takes 30 minutes, and its next ticket runs 10 minutes: (3 − 1) × 30 − 10 = 50 minutes, so
    the ETA is 15:10. Run B: 20 minutes for a ticket, and its next ticket runs 5 minutes: 20 − 5 = 15 minutes, so 14:35.
    Run E ended at 13:20 and started at 12:00: it is in no sum.
    """
    a = clock_time(13, 0)
    write_run(root, "alpha", "aaaa", a, [*built("1", a, clock_time(13, 30), tokens=(10_000, 50_000)), working("2", clock_time(14, 10))],
              tickets=("1", "2", "3"), adapter="claude-code", lock=True)
    b = clock_time(13, 30)
    write_run(root, "beta", "bbbb", b, [*built("1", b, clock_time(13, 50), tokens=(20_000, 70_000)), working("2", clock_time(14, 15))],
              adapter="claude-code", lock=True)
    c = clock_time(13, 10)
    write_run(root, "gamma", "cccc", c, [*built("1", c, clock_time(13, 30), tokens=(4_000, 6_000)), (clock_time(13, 35), "paused", {"ticket": "2", "before": "step"})],
              adapter="claude-code")
    d = clock_time(13, 50)
    write_run(root, "delta", "dddd", d, [working("1", d)], adapter="claude-code", lock=True)
    e = clock_time(12, 0)
    write_run(root, "omega", "eeee", e, [*built("1", e, clock_time(13, 20), tokens=(9_000_000, 9_000_000)), run_end(clock_time(13, 20))],
              tickets=("1",), adapter="claude-code")


def test_the_eta_is_the_latest_the_runs_with_no_eta_are_counted_and_an_ended_run_is_out_of_the_tokens_and_the_time(machine, capsys):
    three_kinds(machine)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[1:3] == [
        "runs  4 of 4 reachable  █████░░░░░░░░░░░ 33%  3/9 ended  ETA 15:10 · 2 with no ETA  34k new · 126k cache",
        "13:00–14:20  trend: tickets ended an hour, last 6h · all      ▇ speeding up",
    ]


def test_the_phrase_with_no_eta_is_left_out_when_each_run_has_an_eta(machine, capsys):
    a = clock_time(13, 0)
    write_run(machine, "alpha", "aaaa", a, [*built("1", a, clock_time(13, 30)), working("2", clock_time(14, 10))], tickets=("1", "2", "3"), lock=True)

    _, out, _ = runs(capsys)

    assert out.splitlines()[1].endswith("1/3 ended  ETA 15:10")


def test_the_trouble_count_holds_a_stopped_run_and_a_run_with_an_idle_agent_and_not_a_paused_run_or_a_busy_agent(machine, capsys):
    started = clock_time(13, 0)
    write_run(machine, "alpha", "ssss", started, built("1", started, clock_time(13, 30)))  # no process holds its lock: stopped
    write_run(machine, "beta", "iiii", clock_time(13, 10), [working("1", clock_time(14, 0)), agent_started("1", clock_time(14, 10))],
              adapter="claude-code", lock=True)  # its agent is idle for 10 minutes, past the 5 minutes of the stall limit
    write_run(machine, "gamma", "pppp", clock_time(13, 20), [(clock_time(13, 40), "paused", {"ticket": "1", "before": "step"})])
    write_run(machine, "delta", "bbbb", clock_time(13, 30), [working("1", clock_time(14, 0)), agent_started("1", clock_time(14, 18))],
              adapter="claude-code", lock=True)  # its agent is idle for 2 minutes

    _, out, _ = runs(capsys)

    assert out.splitlines()[2] == "13:00–14:20  ⚠ 2 in trouble  trend: tickets ended an hour, last 6h · all      ▇ speeding up"


def test_a_run_that_failed_at_start_in_the_window_is_in_trouble_and_out_of_each_sum(machine, capsys):
    started = clock_time(13, 0)
    write_run(machine, "alpha", "aaaa", started, [working("1", started)], lock=True)
    failed = machine / "epsilon-0123456789ab" / "runs" / "20261114T120000Z-ffff"
    failed.mkdir(parents=True)
    (failed / "run.log").write_text("delegate run: the workflow file is missing\n")
    stamp = (NOW - timedelta(hours=1)).timestamp()
    os.utime(failed / "run.log", (stamp, stamp))

    _, out, _ = runs(capsys)

    assert out.splitlines()[1:3] == ["runs  1 of 1 reachable  ░░░░░░░░░░░░░░░░ 0%  0/2 ended  ETA - · 1 with no ETA", "13:00–14:20  ⚠ 1 in trouble  trend: tickets ended an hour, last 6h · all"]


def test_no_trouble_leaves_the_phrase_out_of_the_second_line(machine, capsys):
    one_running(machine, "alpha", "aaaa", clock_time(13, 0), 1, 3)

    _, out, _ = runs(capsys)

    assert out.splitlines()[2] == "13:00–14:20  trend: tickets ended an hour, last 6h · all     ▇  speeding up"  # the ticket ended at 13:10: cell 4


def test_the_first_row_of_the_host_holds_the_ended_count_summed_the_latest_eta_and_the_tokens_summed_without_an_ended_run(machine, capsys):
    three_kinds(machine)

    _, out, _ = runs(capsys)

    assert out.splitlines()[5].split() == ["laptop", "3/9", "15:10", "34k", "126k"]
    assert "9.0M" not in out.splitlines()[5]


def test_a_terminal_under_100_columns_drops_the_bar_and_keeps_the_rest_of_the_line(machine, monkeypatch, capsys):
    three_kinds(machine)
    monkeypatch.setenv("COLUMNS", "99")

    _, out, _ = runs(capsys)

    assert out.splitlines()[1] == "runs  4 of 4 reachable  33%  3/9 ended  ETA 15:10 · 2 with no ETA  34k new · 126k cache"


def test_the_ascii_locale_draws_the_bar_and_the_separators_with_ascii_glyphs(machine, monkeypatch, capsys):
    three_kinds(machine)
    monkeypatch.setenv("LC_ALL", "C")

    _, out, _ = runs(capsys)

    assert out.splitlines()[1:3] == [
        "runs  4 of 4 reachable  #####........... 33%  3/9 ended  ETA 15:10 . 2 with no ETA  34k new . 126k cache",
        "13:00-14:20  trend: tickets ended an hour, last 6h . all      # speeding up",
    ]


def test_with_only_an_ended_run_the_fleet_line_says_no_run_is_reachable_and_the_table_still_lists_it(machine, capsys):
    started = clock_time(12, 0)
    write_run(machine, "omega", "eeee", started, [*built("1", started, clock_time(13, 0)), run_end(clock_time(13, 0))], tickets=("1",))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[1] == "runs  0 of 0 reachable"
    assert lines[2] == ""
    assert lines[-3].split() == ["laptop", "omega", "eeee", "built", "1/1", "▇", "merge"]
    assert lines[-4] == "ended" and lines[-5].startswith("host")  # the group `ended` is the only group: no host has a run under it
