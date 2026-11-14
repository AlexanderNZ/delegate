"""`delegate runs`: a totals row is the sum of the rows under it, and the runs that ended are in the group `ended` (#322).

The rule is from the ticket: the rows under the totals row of a host are the runs that the totals count (the runs with no `run-end` of a host that
is reachable), and the runs that ended in the window are in one group after the hosts, with the host on each row. A run in trouble shows why in its
cell `alert`. The expected numbers are the sums of the numbers of the case, worked out by hand, and the parser of the table reads the output only.
"""

from __future__ import annotations

import os
import re
from datetime import timedelta

import pytest

from .journals import NOW, built, clock_time, run_end, runs, working, write_run
from .test_runs_alert import agent_idle, failed_end
from .test_runs_hosts import index, record
from .test_runs_read_hosts import document, run_of


@pytest.fixture(autouse=True)
def wide(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "120")


def fleet_of_two_hosts(root, index, ssh) -> None:
    """Two hosts, with a run that works, a stopped run, a paused run and runs that ended on both.

    `laptop`: a run that works (1 of 2 tickets, 12k new, 340k cache), a stopped run (1 of 2, no tokens), a paused run (0 of 2), a run that
    ended built and a run that ended failed. `build-host` gives a document: a run that works (3 of 6, 20k new, 500k cache), a stopped run (2 of 4),
    and a run that ended built (6 of 6). The sums of the runs with no `run-end` are 7 of 16 on both hosts: 2 of 6 on `laptop`, and 5 of 10 on `build-host`.
    """
    started = clock_time(13, 40)
    write_run(
        root, "alpha", "aaaa", started,
        [*built("1", started, clock_time(14, 0), tokens=(12_000, 340_000)), working("2", clock_time(14, 0))], adapter="claude-code", lock=True,
    )
    started = clock_time(13, 0)
    write_run(root, "beta", "ssss", started, [*built("1", started, clock_time(13, 30))])
    write_run(root, "gamma", "pppp", clock_time(13, 10), [(clock_time(13, 35), "paused", {"ticket": "1", "before": "step"})])
    started = clock_time(12, 0)
    write_run(root, "delta", "bbbb", started, [*built("1", started, clock_time(13, 0)), run_end(clock_time(13, 0))], tickets=("1",))
    write_run(root, "epsilon", "ffff", started, [working("1", started), failed_end(clock_time(13, 5))], tickets=("1",))
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    running = run_of("build-host", "docs-site", "a41e", "running", 3, 6)
    stopped = run_of("build-host", "api-service", "5d02", "stopped", 2, 4, eta=None, tokens=(1_000, 2_000), do="resume")
    finished = run_of("build-host", "web-app", "e2b1", "built", 6, 6, eta=None, do="merge")
    finished["ended"] = clock_time(13, 10).isoformat()
    ssh.serves("build-host", document("build-host", running, stopped, finished))


def ended_cells(line: str) -> tuple[int, int] | None:
    """The numbers `N/M` of a line of the table, or None when the line has none."""
    found = re.search(r"\s(\d+)/(\d+)(?:\s|$)", line)
    return (int(found.group(1)), int(found.group(2))) if found else None


def groups_of(out: str) -> list[list[str]]:
    """The groups of the table: the lines between two blank lines, after the header line."""
    lines = out.splitlines()
    table = lines[next(number for number, line in enumerate(lines) if line.startswith("host")) + 1:]
    groups: list[list[str]] = [[]]
    for line in table:
        if line:
            groups[-1].append(line)
        else:
            groups.append([])
    return [group for group in groups if group]


def test_each_number_of_a_totals_row_is_the_sum_of_the_rows_under_it_and_the_fleet_line_is_the_sum_of_the_totals_rows(machine, index, ssh, capsys):
    fleet_of_two_hosts(machine, index, ssh)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    hosts = [group for group in groups_of(out) if group[0].split()[0] in ("laptop", "build-host")]
    assert [group[0].split()[0] for group in hosts] == ["laptop", "build-host"]
    totals = []
    for group in hosts:
        under = [ended_cells(line) for line in group[1:]]
        totals.append(ended_cells(group[0]))
        assert totals[-1] == (sum(first for first, _ in under), sum(second for _, second in under))
    assert totals == [(2, 6), (5, 10)]  # laptop: 1/2 + 1/2 + 0/2; build-host: 3/6 + 2/4
    assert f"{sum(first for first, _ in totals)}/{sum(second for _, second in totals)} ended" in out.splitlines()[0]
    assert "7/16 ended" in out.splitlines()[0]
    assert [line.split()[2] for line in hosts[0][1:]] == ["aaaa", "pppp", "ssss"]  # the rows under `laptop` are its three runs with no `run-end`, newest start first
    assert [line.split()[2] for line in hosts[1][1:]] == ["a41e", "5d02"]


def test_the_tokens_of_a_totals_row_are_the_sums_of_the_tokens_of_the_rows_under_it(machine, index, ssh, capsys):
    fleet_of_two_hosts(machine, index, ssh)

    _, out, _ = runs(capsys)

    hosts = {group[0].split()[0]: group for group in groups_of(out)}
    assert hosts["laptop"][0].split()[-2:] == ["12k", "340k"]  # only the run that works has tokens
    assert hosts["build-host"][0].split()[-2:] == ["21k", "502k"]  # 20k + 1k new, 500k + 2k cache
    assert "33k new · 842k cache" in out.splitlines()[0]


def test_the_runs_that_ended_are_in_the_group_ended_after_the_hosts_with_their_host_on_each_row_and_no_totals_row(machine, index, ssh, capsys):
    fleet_of_two_hosts(machine, index, ssh)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    groups = groups_of(out)
    assert [group[0].split()[0] for group in groups[:2]] == ["laptop", "build-host"]
    finished = groups[2]
    assert finished[0].split() == ["ended"]
    assert [line.split()[:4] for line in finished[1:]] == [
        ["laptop", "epsilon", "ffff", "failed"], ["laptop", "delta", "bbbb", "built"], ["build-host", "web-app", "e2b1", "built"],
    ]
    assert [group[0].split()[0] for group in groups[3:]] == ["resume"]  # the fourth group is the legend
    assert not any("│" in line for line in finished)  # the host is in the place of the rail
    assert not any(line.split()[2:3] in (["bbbb"], ["ffff"], ["e2b1"]) for group in groups[:2] for line in group)  # no run that ended is under a host


def test_the_ended_group_folds_to_one_row_for_each_host_when_the_lines_do_not_fit_the_height(machine, index, ssh, capsys, monkeypatch):
    fleet_of_two_hosts(machine, index, ssh)
    monkeypatch.setenv("LINES", "17")

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    finished = groups_of(out)[2]
    assert [line.split() for line in finished] == [["ended"], ["laptop", "✓", "1", "built,", "1", "failed"], ["build-host", "✓", "1", "built"]]


def test_the_count_in_trouble_is_the_number_of_rows_with_an_alert_of_trouble_and_a_stopped_run_has_the_alert_stopped(machine, index, ssh, capsys):
    """Five rows are in trouble: two stopped runs, a halted run, a run with an idle agent and a run that failed at start. The paused run, the run that works, and the runs that ended are not."""
    fleet_of_two_hosts(machine, index, ssh)
    write_run(machine, "zeta", "iiii", clock_time(13, 1), [working("1", clock_time(14, 0)), agent_idle("1", clock_time(14, 10))], adapter="claude-code", lock=True)
    write_run(
        machine, "eta", "hhhh", clock_time(13, 5),
        [(clock_time(13, 40), "halted", {"ticket": "1", "role": "specialist", "round": 0, "message": "no"})],
    )
    log = machine / "theta-0123456789ab" / "runs" / "20261114T133000Z-llll" / "run.log"
    log.parent.mkdir(parents=True)
    log.write_text("delegate run: the workflow file is missing\n")
    stamp = (NOW - timedelta(hours=1)).timestamp()
    os.utime(log, (stamp, stamp))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    row = {line.split()[2]: line for line in out.splitlines() if re.match(r"(│|laptop|build-host)\s+\S+\s+\w{4}\s", line)}
    for short, words in {"ssss": "stopped", "5d02": "stopped", "hhhh": "halted", "iiii": "⚠ idle 10m00s", "llll": "failed at start"}.items():
        assert words in row[short], short
    for short in ("aaaa", "pppp", "a41e", "bbbb", "ffff", "e2b1"):
        assert not re.search(r"stopped|halted|failed at start|idle", row[short].replace("failed ", "")), short
    assert "⚠ 5 in trouble" in out.splitlines()[1]
    assert row["ssss"].split()[-2:] == ["stopped", "resume"] and row["hhhh"].split()[-2:] == ["halted", "resume"]  # the word `do` is not `watch`
