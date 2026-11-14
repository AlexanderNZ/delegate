"""`delegate runs`: the table of every run of this machine, from each state directory under the state root (#307).

A case writes the journals of runs in state directories under a state root of its own, with the time of each event, and
calls `main(argv)` of the `delegate` command. The commands read the time from the fake clock (the `clock` fixture), and
the zone of the process is UTC, so a clock time in a cell is the same on each machine. No case needs a git repository:
the command reads the state root and no repository.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from delegate.cli import delegate

from .journals import NOW, built, clock_time, run_end, runs, write_run


def two_repositories(root: Path) -> None:
    """`alpha` has a run that works. `beta` has a run that ended 1 hour ago and a run that ended 8 hours ago."""
    started = clock_time(13, 40)
    write_run(
        root, "alpha", "aaaa", started,
        [*built("1", started, clock_time(14, 0), tokens=(12_000, 340_000)), (clock_time(14, 0), "step-start", {
            "ticket": "2", "stack": "python", "branch": "run/demo-2", "worktree": "/w/2", "base_commit": "0" * 40,
            "agent": "python-specialist", "tier": "strong", "model": "m",
        })],
        adapter="claude-code", lock=True,
    )
    write_run(
        root, "beta", "bbbb", clock_time(12, 50),
        [*built("1", clock_time(12, 50), clock_time(13, 20), tokens=(2_000, 50_000)), run_end(clock_time(13, 20))],
        tickets=("1",), adapter="claude-code",
    )
    write_run(
        root, "beta", "cccc", clock_time(5, 50),
        [*built("1", clock_time(5, 50), clock_time(6, 20), tokens=(1_000, 1_000)), run_end(clock_time(6, 20))],
        tickets=("1",), adapter="claude-code",
    )


def test_the_table_lists_the_running_run_then_the_run_that_ended_in_the_window_and_not_the_run_that_ended_8_hours_ago(machine, capsys):
    two_repositories(machine)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "no run index is set up: these are the runs of this machine",
        "runs  1 of 1 reachable  50%  1/2 ended  ETA - · 1 with no ETA  12k new · 340k cache",
        "13:40–14:20",
        "",
        "host    repo   run   state    ended  ETA  new  cache",
        "laptop                        1/2         12k   340k",
        "│       alpha  aaaa  running  1/2    -    12k   340k",
        "│       beta   bbbb  built    1/1          2k    50k",
    ]


def test_the_json_object_has_the_format_the_host_the_window_and_each_listed_run_field_by_field(machine, capsys):
    two_repositories(machine)

    code, out, err = runs(capsys, "--json")

    assert (code, err) == (0, "")
    assert json.loads(out) == {
        "format": 1,
        "host": "laptop",
        "time": "2026-11-14T14:20:00+00:00",
        "window_seconds": 21600,
        "runs": [
            {
                "run_id": "20261114T134000Z-aaaa", "repo": "alpha", "host": "laptop", "state": "running",
                "started": "2026-11-14T13:40:00+00:00", "ended": None, "tickets_ended": 1, "tickets_total": 2, "eta": None,
                "tokens_new": 12000, "tokens_cache": 340000,
            },
            {
                "run_id": "20261114T125000Z-bbbb", "repo": "beta", "host": "laptop", "state": "built",
                "started": "2026-11-14T12:50:00+00:00", "ended": "2026-11-14T13:20:00+00:00", "tickets_ended": 1,
                "tickets_total": 1, "eta": None, "tokens_new": 2000, "tokens_cache": 50000,
            },
        ],
    }
    assert list(json.loads(out)) == ["format", "host", "time", "window_seconds", "runs"]
    assert list(json.loads(out)["runs"][0]) == [
        "run_id", "repo", "host", "state", "started", "ended", "tickets_ended", "tickets_total", "eta", "tokens_new", "tokens_cache",
    ]


def test_the_eta_cell_is_a_clock_time_while_a_run_runs_and_is_blank_for_a_paused_run(machine, capsys):
    started = clock_time(13, 0)
    write_run(
        machine, "gamma", "gggg", started,
        [*built("1", started, clock_time(13, 30)), (clock_time(14, 10), "step-start", {
            "ticket": "2", "stack": "python", "branch": "run/demo-2", "worktree": "/w/2", "base_commit": "0" * 40,
            "agent": "python-specialist", "tier": "strong", "model": "m",
        })],
        tickets=("1", "2", "3"), lock=True,
    )
    write_run(
        machine, "gamma", "pppp", clock_time(13, 10),
        [*built("1", clock_time(13, 10), clock_time(13, 30)), (clock_time(13, 35), "paused", {"ticket": "2", "before": "step"})],
    )

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[4:] == [
        "host    repo   run   state    ended  ETA    new  cache",
        "laptop                        2/5    15:10",
        "│       gamma  pppp  paused   1/2",
        "│       gamma  gggg  running  1/3    15:10",
    ]
    code, out, _ = runs(capsys, "--json")
    assert [(run["state"], run["eta"], run["tokens_new"]) for run in json.loads(out)["runs"]] == [
        ("paused", None, None), ("running", "2026-11-14T15:10:00+00:00", None),
    ]


def test_a_run_with_no_run_end_and_no_process_is_listed_as_stopped_however_old_it_is(machine, capsys):
    started = clock_time(13, 0, days_back=9)
    write_run(machine, "delta", "ssss", started, built("1", started, clock_time(13, 30, days_back=9)))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[-1].split() == ["│", "delta", "ssss", "stopped", "1/2"]


def test_a_run_that_failed_at_start_in_the_window_is_listed_as_failed_and_an_older_one_is_not(machine, capsys):
    recent = machine / "epsilon-0123456789ab" / "runs" / "20261114T130000Z-ffff"
    old = machine / "epsilon-0123456789ab" / "runs" / "20261114T010000Z-oooo"
    for directory, age in ((recent, timedelta(hours=1)), (old, timedelta(hours=8))):
        directory.mkdir(parents=True)
        log = directory / "run.log"
        log.write_text("delegate run: the workflow file is missing\n")
        stamp = (NOW - age).timestamp()
        os.utime(log, (stamp, stamp))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert [line.split() for line in out.splitlines()[-1:]] == [["│", "epsilon", "ffff", "failed", "0/0"]]
    assert "oooo" not in out


def test_a_journal_that_cannot_be_read_is_skipped_and_named_on_stderr_and_the_other_runs_are_listed(machine, capsys):
    two_repositories(machine)
    broken = machine / "zeta-0123456789ab" / "runs" / "20261114T130000Z-xxxx" / "journal.jsonl"
    broken.parent.mkdir(parents=True)
    broken.write_text("this is not json\n")

    code, out, err = runs(capsys)

    assert code == 0
    assert err.startswith(f"delegate runs: skipped the journal {broken}: ")
    assert "aaaa" in out and "bbbb" in out and "xxxx" not in out


def test_no_run_in_the_window_prints_one_line_and_exits_0(machine, capsys):
    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines() == ["no run index is set up: these are the runs of this machine", "", "no run ended in the last 6h"]


def test_the_eta_is_the_local_clock_time_of_the_zone_of_the_machine(machine, monkeypatch, capsys):
    started = clock_time(13, 0)
    write_run(
        machine, "gamma", "gggg", started,
        [*built("1", started, clock_time(13, 30)), (clock_time(14, 10), "step-start", {
            "ticket": "2", "stack": "python", "branch": "run/demo-2", "worktree": "/w/2", "base_commit": "0" * 40,
            "agent": "python-specialist", "tier": "strong", "model": "m",
        })],
        tickets=("1", "2", "3"), lock=True,
    )
    monkeypatch.setenv("TZ", "Pacific/Auckland")  # UTC+13 on this date: the ETA 15:10 UTC is 04:10 the next day
    time.tzset()

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[-1].split() == ["│", "gamma", "gggg", "running", "1/3", "04:10"]


def test_a_terminal_with_no_utf8_locale_gets_the_ascii_rail(machine, monkeypatch, capsys):
    two_repositories(machine)
    monkeypatch.setenv("LC_ALL", "C")

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert [line[0] for line in out.splitlines()[-2:]] == ["|", "|"]
