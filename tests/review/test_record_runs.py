"""`record_runs` writes the records of the runs and builds no view (#334).

A record needs the fold of the journal and one snapshot. The ETA of 2 hours ago (the slip) and the trend are for the view only, so the call that
writes the records makes neither. A case counts the snapshots that the call makes, by the time of each, and the trend cells that it works out.
"""

from __future__ import annotations

import sys
from datetime import timedelta

from delegate.adapters.run_index import DirectoryRunIndex
from delegate.run import fleet_rules, status
from delegate.run.index import record_runs

from ..runs.journals import NOW, built, clock_time, working, write_run


def write_working_run(root) -> None:
    """A run that started 4 hours ago, built one ticket in an hour, and works on the second since 13:50 (a process holds its lock): its ETA of 2 hours ago exists, so a view works it out."""
    write_run(
        root, "alpha", "aaaa", clock_time(10, 0),
        [*built("1", clock_time(10, 0), clock_time(11, 0)), working("2", clock_time(13, 50))], lock=True,
    )


def test_record_runs_makes_one_snapshot_for_a_run_and_none_of_the_eta_of_2_hours_ago(tmp_path, monkeypatch):
    root = tmp_path / "state"
    write_working_run(root)
    times = []
    original = status.snapshot_at

    def counted(events, journal, reader_of, now, **kwargs):
        times.append(now)
        return original(events, journal, reader_of, now, **kwargs)

    for module in [module for name, module in sys.modules.items() if name.startswith("delegate.run.")]:
        if getattr(module, "snapshot_at", None) is original:  # `status` and each module that imported the name
            monkeypatch.setattr(module, "snapshot_at", counted)

    problem = record_runs(root, "laptop", NOW, DirectoryRunIndex(tmp_path / "index"))

    assert problem is None
    assert times == [NOW]  # one snapshot, at the time of the call; none at NOW - SLIP_WINDOW_SECONDS
    assert NOW - timedelta(seconds=fleet_rules.SLIP_WINDOW_SECONDS) not in times


def test_record_runs_works_out_no_trend(tmp_path, monkeypatch):
    root = tmp_path / "state"
    write_working_run(root)
    cells = []
    original = fleet_rules.trend_cells

    def counted(*args, **kwargs):
        cells.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(fleet_rules, "trend_cells", counted)

    problem = record_runs(root, "laptop", NOW, DirectoryRunIndex(tmp_path / "index"))

    assert problem is None
    assert cells == []


def test_record_runs_still_writes_the_record_of_the_run(tmp_path):
    root = tmp_path / "state"
    write_working_run(root)
    index = DirectoryRunIndex(tmp_path / "index")

    problem = record_runs(root, "laptop", NOW, index)

    assert problem is None
    [record] = index.list_records()
    assert (record.run_id, record.host, record.repo, record.state, record.tickets_ended, record.tickets_total) == (
        "20261114T100000Z-aaaa", "laptop", "alpha", "running", 1, 2,
    )
