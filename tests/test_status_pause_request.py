"""`delegate status` with a pause request in the directory of a run (#111).

`delegate pause` writes `pause-request`, and the engine reads it at the next step boundary. When the run stops before
that (Ctrl-C, a kill, a crash), the file stays. The run is `pausing` only while the lock of its run branch names the
run and a process that exists, the rule of an active run of #110. With no live process, `status` shows the state of
the journal and a note that tells what removes the request.

A case writes each journal with the journal class of the engine, and each lock with the lock class of the engine: a
live lock is held by the process of the test, and a stale lock by a child process that ended. A case calls
`delegate.main([...])` against a real temporary git repository.
"""

import json

import pytest

from delegate.cli import delegate

from .support import bare_repo, live_lock, run_start, stale_lock, step_start, write_journal

NOTE = "a pause request waits; delegate run --resume removes it"


def status(repo, capsys, *extra):
    code = delegate.main(["status", "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_with_a_pause_request(repo, *events):
    journal = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), *events)
    (journal.parent / "pause-request").write_text(json.dumps({"time": "2026-10-19T07:00:00+00:00", "pid": 1}) + "\n")
    return journal


@pytest.mark.parametrize(
    "case",
    ["ctrl-c", "kill"],
)
def test_a_pause_request_of_a_run_with_no_live_process_shows_the_state_of_the_journal_and_the_note(tmp_path, capsys, case):
    repo = bare_repo(tmp_path)
    if case == "ctrl-c":
        # Ctrl-C records `interrupted` and releases the lock.
        run_with_a_pause_request(repo, ("interrupted", {"run_id": "r1"}))
    else:
        # A kill leaves the lock of a process that no longer exists.
        stale_lock(run_with_a_pause_request(repo))

    code, out, err = status(repo, capsys, "r1")
    json_code, printed, json_err = status(repo, capsys, "r1", "--json")

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[0] == "run r1: running (mode assure, adapter scripted)"
    assert lines[1] == f"note: {NOTE}"
    assert "pausing" not in out
    assert (json_code, json_err) == (0, "")
    snapshot = json.loads(printed)
    assert (snapshot["state"], snapshot["note"]) == ("running", NOTE)


def test_a_pause_request_of_a_run_with_a_live_process_shows_pausing_and_no_note(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    journal = run_with_a_pause_request(repo)

    with live_lock(journal):
        code, out, err = status(repo, capsys, "r1")
        json_code, printed, json_err = status(repo, capsys, "r1", "--json")

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[0] == "run r1: pausing (mode assure, adapter scripted)"
    assert lines[1].startswith("progress: ")
    assert "note:" not in out
    assert (json_code, json_err) == (0, "")
    snapshot = json.loads(printed)
    assert (snapshot["state"], snapshot["note"]) == ("pausing", None)


def test_a_run_with_no_pause_request_and_no_live_process_has_no_note(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"))

    code, out, err = status(repo, capsys, "r1")
    json_code, printed, _ = status(repo, capsys, "r1", "--json")

    assert (code, err) == (0, "")
    assert out.splitlines()[0] == "run r1: running (mode assure, adapter scripted)"
    assert "note:" not in out
    assert json.loads(printed)["note"] is None
