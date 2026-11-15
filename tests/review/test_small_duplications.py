"""Each small rule has one place in the code (#337): the short host name, the state words, the times of a record, and the time of an event.

A case drives a public seam: `host_name`, `other_hosts`, `record_runs` or the port `RunRecord`. A system boundary (the host name of the
operating system) is patched. The expected values come from the ticket and from the words of the design, not from the code.
"""

from __future__ import annotations

import json
import re
import socket
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import delegate
from delegate.adapters.run_index import DirectoryRunIndex
from delegate.ports.index import RunRecord
from delegate.run import fleet_rules, host, status_model, user_config
from delegate.run.fleet_hosts import other_hosts
from delegate.run.index import record_runs

from ..runs.journals import NOW, built, clock_time, working, write_run

GOOD = "2026-11-14T13:00:00+00:00"


def a_record(**changes: object) -> RunRecord:
    """A record of a run of the host `far`, running, started and heard at 13:00 UTC, with the changes that the case names."""
    return replace(
        RunRecord("far-1", "far", "demo", GOOD, "running", 1, 3, None, GOOD), **changes,
    )


@pytest.mark.parametrize("full, short", [("Box.example.net", "box"), ("box", "box"), ("a.b.c", "a")])
def test_the_short_host_name_is_the_same_for_the_run_and_for_the_configuration(monkeypatch, full, short):
    monkeypatch.delenv(host.HOST_VARIABLE, raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: full)

    assert host.short_host_name() == full.split(".", 1)[0]
    assert host.host_name() == full.split(".", 1)[0]
    assert short in user_config.machine_names({})
    assert full.lower() in user_config.machine_names({})


def test_the_delegate_host_variable_names_this_machine_for_the_run_and_for_the_configuration(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "Box.example.net")
    monkeypatch.setenv(host.HOST_VARIABLE, "Nick")

    assert host.host_name() == "Nick"
    assert user_config.machine_names({host.HOST_VARIABLE: "Nick"}) == {"localhost", "box.example.net", "box", "nick"}


def test_the_state_words_of_the_rules_of_the_fleet_are_the_state_words_of_the_status():
    assert fleet_rules.STOPPED == status_model.STOPPED
    assert fleet_rules.HALTED == status_model.HALTED
    assert fleet_rules.RUNNING == status_model.RUNNING
    assert fleet_rules.TROUBLE_STATES == {status_model.STOPPED, status_model.HALTED}
    assert fleet_rules.WORKING_STATES == {status_model.RUNNING, status_model.PAUSING}
    assert fleet_rules.RESUME_STATES == {status_model.PAUSED, status_model.STOPPED, status_model.HALTED}


def test_a_record_gives_the_time_of_its_start_and_of_its_write_as_a_time_with_a_zone():
    record = a_record(started="2026-11-14T13:00:00+00:00", heard="2026-11-14T14:30:00+12:00")

    assert record.started_time() == datetime(2026, 11, 14, 13, 0, tzinfo=timezone.utc)
    assert record.heard_time() == datetime(2026, 11, 14, 2, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", ["", "not a time", "2026-13-45T99:00:00+00:00"])
def test_a_record_with_a_time_that_cannot_be_read_gives_none_for_that_time_only(bad):
    assert a_record(started=bad).started_time() is None
    assert a_record(started=bad).heard_time() is not None
    assert a_record(heard=bad).heard_time() is None
    assert a_record(heard=bad).started_time() is not None


def test_a_run_with_a_bad_time_in_its_record_is_not_listed_and_its_host_is_heard_at_the_newest_good_write():
    records = [
        a_record(run_id="far-bad-start", started="not a time", heard="2026-11-14T13:10:00+00:00"),
        a_record(run_id="far-bad-heard", heard="not a time"),
        a_record(run_id="far-good", heard="2026-11-14T13:05:00+00:00"),
    ]

    rows, names, lost = other_hosts(records, "laptop", NOW, None, [])

    assert [row.run_id for row in rows] == ["far-good"]
    assert names == ["far"]
    assert [(entry.name, entry.heard) for entry in lost] == [("far", datetime(2026, 11, 14, 13, 10, tzinfo=timezone.utc))]


def write_journal_of_a_run_that_works(root) -> str:
    started = clock_time(10, 0)
    path = write_run(root, "alpha", "aaaa", started, [*built("1", started, clock_time(11, 0)), working("2", clock_time(13, 50))], lock=True)
    return path.parent.name


def test_the_backfill_writes_a_record_again_when_the_time_of_its_write_cannot_be_read(tmp_path):
    root = tmp_path / "state"
    run_id = write_journal_of_a_run_that_works(root)
    index = DirectoryRunIndex(tmp_path / "index")
    index.record(RunRecord(run_id, "laptop", "alpha", clock_time(10, 0).isoformat(), "running", 1, 2, None, "not a time"))

    problem = record_runs(root, "laptop", NOW, index)

    assert problem is None
    kept = {record.run_id: record for record in index.list_records()}
    assert kept[run_id].heard == NOW.isoformat()


def test_the_backfill_keeps_a_record_as_new_as_its_journal(tmp_path):
    root = tmp_path / "state"
    run_id = write_journal_of_a_run_that_works(root)
    index = DirectoryRunIndex(tmp_path / "index")
    assert record_runs(root, "laptop", NOW, index) is None
    first = json.loads((tmp_path / "index" / f"{run_id}.json").read_text())

    assert record_runs(root, "laptop", NOW, index) is None

    assert json.loads((tmp_path / "index" / f"{run_id}.json").read_text()) == first


#: A parse of the `time` of an event on one line: `fromisoformat(...["time"]...)`.
INLINE_EVENT_TIME = re.compile(r"fromisoformat\(.*\[\s*[\x27\x22]time[\x27\x22]\s*\]")


def test_only_the_journal_module_parses_the_time_of_an_event():
    # The one helper is `journal.event_time`. A module that parses `event["time"]` itself can drift from it.
    source = Path(delegate.__file__).parent
    found = sorted(
        str(path.relative_to(source)) for path in source.rglob("*.py") if INLINE_EVENT_TIME.search(path.read_text())
    )

    assert found == ["run/journal.py"]
