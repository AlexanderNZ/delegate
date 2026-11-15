"""The run index (#316): each run registers itself in the shared directory that `DELEGATE_INDEX` names.

A case runs `delegate run` end to end against a real temporary git repository with the scripted adapter, and reads the
files of the index directory. The expected values come from the ticket, not from the code. The journal stays the record of the run.
"""

from __future__ import annotations

import json
from datetime import datetime

from delegate.adapters.run_index import DirectoryRunIndex
from delegate.ports.index import RunRecord

from ..support import HEAD, event_names, make_repo, read_journal, resume_main, run_main, ticket, ticket_of
from delegate.ports.harness import AdapterError

RECORD_FIELDS = {"run_id", "host", "repo", "started", "state", "tickets_ended", "tickets_total", "eta", "heard"}
TWO_TICKETS = HEAD + ticket("a") + ticket("b", ("a",))


def run_with_index(tmp_path, capsys, monkeypatch, index_path):
    repo = make_repo(tmp_path, TWO_TICKETS)
    monkeypatch.setenv("DELEGATE_INDEX", str(index_path))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    return repo, code, out, err


def test_a_run_writes_its_record_at_its_end_with_the_fields_of_the_ticket(tmp_path, capsys, monkeypatch, scripted):
    index = tmp_path / "index"

    repo, code, out, err = run_with_index(tmp_path, capsys, monkeypatch, index)

    assert (code, err) == (0, "")
    events = read_journal(out)
    run_id = events[0]["run_id"]
    assert [path.name for path in index.iterdir()] == [f"{run_id}.json"]
    record = json.loads((index / f"{run_id}.json").read_text())
    assert set(record) == RECORD_FIELDS
    assert record["run_id"] == run_id
    assert record["host"] == "laptop"
    assert record["repo"] == "repo"
    assert record["started"] == events[0]["time"]
    assert (record["state"], record["tickets_ended"], record["tickets_total"]) == ("built", 2, 2)
    # The snapshot of a run that ended has the time of `run-end`, to the second, as its ETA (`delegate status --json` says the same).
    assert datetime.fromisoformat(record["eta"]) == datetime.fromisoformat(events[-1]["time"]).replace(microsecond=0)
    assert datetime.fromisoformat(record["heard"]) >= datetime.fromisoformat(events[-1]["time"])


def test_a_run_writes_its_record_at_its_start_and_after_each_step(tmp_path, capsys, monkeypatch, scripted):
    index = tmp_path / "index"
    seen: list[dict[str, object]] = []

    def watch_the_index(request):
        # The adapter runs between the records of the run, so the file holds the newest record before this step.
        files = list(index.iterdir())
        seen.append(json.loads(files[0].read_text()) if files else {})

    scripted.on_specialist = watch_the_index

    run_with_index(tmp_path, capsys, monkeypatch, index)

    assert [(record["state"], record["tickets_ended"]) for record in seen] == [("running", 0), ("running", 1)]


def test_a_paused_run_and_a_halted_run_write_their_state(tmp_path, capsys, monkeypatch, scripted):
    repo = make_repo(tmp_path, TWO_TICKETS)
    index = tmp_path / "index"
    monkeypatch.setenv("DELEGATE_INDEX", str(index))

    def not_logged_in(request):
        if ticket_of(request) == "b":
            raise AdapterError("claude: not logged in")

    scripted.on_specialist = not_logged_in
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = read_journal(out)[0]["run_id"]

    assert code == 4
    halted = json.loads((index / f"{run_id}.json").read_text())
    assert (halted["state"], halted["tickets_ended"], halted["eta"]) == ("halted", 1, None)

    scripted.on_specialist = None
    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 0, err
    assert json.loads((index / f"{run_id}.json").read_text())["state"] == "built"


def test_a_directory_that_cannot_be_written_gives_the_same_result_and_one_index_warning(tmp_path, capsys, monkeypatch, scripted):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("a file, so the index cannot make its directory\n")
    with_none = tmp_path / "none"
    with_none.mkdir()
    plain = make_repo(with_none, TWO_TICKETS)
    plain_code, plain_out, plain_err = run_main(capsys, str(plain / "workflow.toml"), "--repo", str(plain))
    plain_events = read_journal(plain_out)

    _, code, out, err = run_with_index(tmp_path, capsys, monkeypatch, blocked)

    events = read_journal(out)
    assert (code, err) == (plain_code, plain_err) == (0, "")
    ended = [e for e in events if e["event"] != "index-warning"][-1]
    assert (ended["event"], ended["result"], ended["built"]) == ("run-end", "built", ["a", "b"])
    assert (plain_events[-1]["result"], plain_events[-1]["built"]) == ("built", ["a", "b"])
    warnings = [e for e in events if e["event"] == "index-warning"]
    assert len(warnings) == len([n for n in event_names(plain_events) if n in ("run-start", "step-end", "run-end")])
    assert all(str(blocked) in str(w["reason"]) for w in warnings)
    assert [n for n in event_names(events) if n != "index-warning"] == event_names(plain_events)


def test_with_no_delegate_index_and_no_configuration_file_the_run_writes_its_record_in_the_index_of_the_state_root(tmp_path, capsys, scripted, machine):
    """#323: the index is always on. With no setting, it is `index/` in the state root of this machine (`machine`, which `DELEGATE_STATE_ROOT` names)."""
    repo = make_repo(tmp_path, TWO_TICKETS)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")
    events = read_journal(out)
    assert "index-warning" not in event_names(events)
    run_id = events[0]["run_id"]
    index = machine / "index"
    assert [path.name for path in index.iterdir()] == [f"{run_id}.json"]
    record = json.loads((index / f"{run_id}.json").read_text())
    assert (record["run_id"], record["repo"], record["state"], record["tickets_ended"], record["tickets_total"]) == (run_id, "repo", "built", 2, 2)


def test_the_directory_backend_records_lists_and_forgets_a_run(tmp_path):
    index = DirectoryRunIndex(tmp_path / "shared")
    first = RunRecord("r-1", "laptop", "demo", "2026-11-14T01:00:00+00:00", "running", 1, 3, "2026-11-14T02:00:00+00:00", "2026-11-14T01:30:00+00:00")
    second = RunRecord("r-2", "box", "demo", "2026-11-14T01:10:00+00:00", "built", 2, 2, None, "2026-11-14T01:40:00+00:00")

    index.record(first)
    index.record(second)
    index.record(RunRecord(**{**first.__dict__, "tickets_ended": 2, "heard": "2026-11-14T01:50:00+00:00"}))

    assert sorted(index.list_records(), key=lambda record: record.run_id) == [
        RunRecord(**{**first.__dict__, "tickets_ended": 2, "heard": "2026-11-14T01:50:00+00:00"}), second,
    ]
    assert sorted(path.name for path in (tmp_path / "shared").iterdir()) == ["r-1.json", "r-2.json"]

    index.forget("r-1")
    index.forget("r-1")

    assert [record.run_id for record in index.list_records()] == ["r-2"]


def test_a_missing_index_directory_lists_no_run(tmp_path):
    assert DirectoryRunIndex(tmp_path / "absent").list_records() == []


def test_a_watch_of_a_run_whose_last_index_write_failed_still_ends_at_its_run_end(tmp_path, capsys, monkeypatch, scripted):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("a file\n")
    repo, code, out, err = run_with_index(tmp_path, capsys, monkeypatch, blocked)
    events = read_journal(out)
    assert [e["event"] for e in events[-2:]] == ["run-end", "index-warning"]
    from delegate.cli import delegate

    watch_code = delegate.main(["watch", events[0]["run_id"], "--repo", str(repo), "--poll-seconds", "0.02"])
    captured = capsys.readouterr()

    assert (watch_code, captured.err) == (0, "")
