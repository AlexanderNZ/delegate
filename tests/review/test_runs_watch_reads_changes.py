"""`delegate runs --watch` reads only what changed (#350).

The redraw that changes nothing opens no journal and no record file: that case is in `tests/runs/test_runs_watch.py`. These cases are the parts of it that a
redraw needs: the directory backend of the run index reads again only the record files that changed, and the use case keeps the ETA of 2 hours ago of a run for a minute.
"""

from __future__ import annotations

import builtins
import io
import sys
from datetime import timedelta

from delegate.adapters.run_index import DirectoryRunIndex
from delegate.ports.index import RunRecord
from delegate.run import fleet_rules, status
from delegate.run.fleet import EtaMemory, fleet_of
from delegate.run.journal import JournalReads

from ..runs.journals import NOW, built, clock_time, working, write_run


def record(run_id: str, state: str = "running") -> RunRecord:
    return RunRecord(run_id, "other", "alpha", clock_time(10, 0).isoformat(), state, 1, 2, None, NOW.isoformat())


def opened_files(monkeypatch) -> list[str]:
    """The list that gets each path that the code opens through `open`, `io.open` and `Path.read_text`."""
    paths: list[str] = []
    real = io.open

    def spy(file, *args, **kwargs):
        paths.append(str(file))
        return real(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(builtins, "open", spy)
    return paths


def test_a_second_listing_of_the_index_reads_only_the_record_file_that_another_writer_changed(tmp_path, monkeypatch):
    directory = tmp_path / "index"
    mine, theirs = DirectoryRunIndex(directory), DirectoryRunIndex(directory)
    for run_id in ("r1", "r2", "r3"):
        theirs.record(record(run_id))
    assert sorted(r.run_id for r in mine.list_records()) == ["r1", "r2", "r3"]
    theirs.record(record("r2", state="halted"))
    opened = opened_files(monkeypatch)

    listed = {r.run_id: r.state for r in mine.list_records()}

    assert listed == {"r1": "running", "r2": "halted", "r3": "running"}
    assert opened == [str(directory / "r2.json")]


def test_a_listing_of_an_index_that_did_not_change_reads_no_file(tmp_path, monkeypatch):
    directory = tmp_path / "index"
    index = DirectoryRunIndex(directory)
    index.record(record("r1"))
    index.record(record("r2"))
    assert len(index.list_records()) == 2
    opened = opened_files(monkeypatch)

    first, second = index.list_records(), index.list_records()

    assert sorted(r.run_id for r in first) == sorted(r.run_id for r in second) == ["r1", "r2"]
    assert opened == []


def test_a_record_file_that_goes_leaves_the_next_listing(tmp_path):
    directory = tmp_path / "index"
    index = DirectoryRunIndex(directory)
    index.record(record("r1"))
    index.record(record("r2"))
    assert len(index.list_records()) == 2

    DirectoryRunIndex(directory).forget("r1")

    assert [r.run_id for r in index.list_records()] == ["r2"]


def snapshot_times(monkeypatch) -> list:
    """The time of each snapshot that the code makes (`status.snapshot_at`), in order."""
    times = []
    original = status.snapshot_at

    def counted(events, journal, reader_of, now, **kwargs):
        times.append(now)
        return original(events, journal, reader_of, now, **kwargs)

    for module in [module for name, module in sys.modules.items() if name.startswith("delegate.run.")]:
        if getattr(module, "snapshot_at", None) is original:  # `status` and each module that imported the name
            monkeypatch.setattr(module, "snapshot_at", counted)
    return times


def test_the_eta_of_2_hours_ago_of_a_run_is_worked_out_again_after_a_minute_and_not_before(tmp_path, monkeypatch):
    root = tmp_path / "state"
    write_run(
        root, "alpha", "aaaa", clock_time(10, 0), [*built("1", clock_time(10, 0), clock_time(11, 0)), working("2", clock_time(13, 50))], lock=True,
    )
    times, etas, reads = snapshot_times(monkeypatch), EtaMemory(), JournalReads()
    slip = timedelta(seconds=fleet_rules.SLIP_WINDOW_SECONDS)
    asks = lambda *offsets: [fleet_of(root, "laptop", NOW + timedelta(seconds=n), lambda adapter: None, reads, None, False, None, None, etas) for n in offsets]  # noqa: E731

    asks(0, 30, 59)
    assert [t for t in times if t < NOW] == [NOW - slip]  # one snapshot of 2 hours ago for three redraws

    asks(60)
    assert [t for t in times if t < NOW] == [NOW - slip, NOW + timedelta(seconds=60) - slip]
