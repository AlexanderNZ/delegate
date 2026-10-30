"""`delegate status` of a run with no `run-end`, no pause and no live process (#193).

A crash, a kill, Ctrl-C or a stop of the machine leaves a journal that has no `run-end`. The run is `running` only while
the lock of its run branch names the run and a process that exists, the rule of an active run (#110). With no live
process, `status` shows `stopped` and gives the command that goes on with the run.

A case writes each journal with the journal class of the engine, and each lock with the lock class of the engine: a
live lock is held by the process of the test, and a stale lock by a child process that ended. A case calls
`delegate.main([...])` against a real temporary git repository.
"""

import json

import pytest

from delegate.cli import delegate

from .support import bare_repo, live_lock, run_start, stale_lock, step_start, write_journal

RESUME = "delegate run --resume r1"


def status(repo, capsys, *extra):
    code = delegate.main(["status", "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("case", ["kill", "ctrl-c", "no lock"])
def test_a_run_with_no_run_end_and_no_live_process_is_stopped_in_the_text_and_in_json(tmp_path, capsys, case):
    repo = bare_repo(tmp_path)
    events = [run_start("r1", "a"), step_start("a")]
    if case == "ctrl-c":
        events.append(("interrupted", {"run_id": "r1"}))  # Ctrl-C records `interrupted` and releases the lock
    journal = write_journal(repo, "r1", *events)
    if case == "kill":
        stale_lock(journal)  # a kill leaves the lock of a process that no longer exists

    code, out, err = status(repo, capsys, "r1")
    json_code, printed, json_err = status(repo, capsys, "r1", "--json")

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[0] == "run r1: stopped (mode assure, adapter scripted)"
    assert lines[1].startswith("note: ") and RESUME in lines[1]
    assert "ETA" not in out
    assert (json_code, json_err) == (0, "")
    snapshot = json.loads(printed)
    assert snapshot["state"] == "stopped" and RESUME in snapshot["note"] and snapshot["eta"] is None


def test_a_run_whose_lock_holder_is_alive_is_running(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    journal = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"))

    with live_lock(journal):
        code, out, err = status(repo, capsys, "r1")
        json_code, printed, json_err = status(repo, capsys, "r1", "--json")

    assert (code, err) == (0, "")
    assert out.splitlines()[0] == "run r1: running (mode assure, adapter scripted)"
    assert "note:" not in out
    assert (json_code, json_err) == (0, "")
    snapshot = json.loads(printed)
    assert (snapshot["state"], snapshot["note"]) == ("running", None)
