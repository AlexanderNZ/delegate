"""The run index follows each stop of a run, and the backfill does not bring back a record that `delegate clean` forgot (#327).

Each case runs `delegate run` against a real temporary git repository with the scripted adapter, and reads the files of the
index directory (the directory backend). The expected values come from the ticket and from ADR 0008, not from the code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from delegate.cli import delegate

from ..support import HEAD, make_repo, read_journal, run_main, state_dir, ticket, ticket_of
from ..support_run import write_agents

GATE = "test -f feature.txt"
REASON = "the guard has no rule for this command"


pytestmark = pytest.mark.scripted_adapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abc"}, checks_setup=True)


@pytest.fixture
def index(tmp_path, monkeypatch) -> Path:
    directory = tmp_path / "shared-index"
    monkeypatch.setenv("DELEGATE_INDEX", str(directory))
    return directory


def records_of(directory: Path) -> dict[str, dict[str, object]]:
    """Each record of the index directory, by run id."""
    return {path.stem: json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))}


def test_a_run_that_crashed_has_a_record_with_the_state_stopped(tmp_path, capsys, index, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))

    def crash(request):
        if ticket_of(request) == "b":
            raise RuntimeError("harness binary missing")

    scripted.on_specialist = crash

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    events = read_journal(out)
    run_id = events[0]["run_id"]
    assert "crashed" in [e["event"] for e in events]
    assert {run_id: (record["state"], record["tickets_ended"]) for run_id, record in records_of(index).items()} == {run_id: ("stopped", 1)}


def test_a_run_that_ctrl_c_interrupted_has_a_record_with_the_state_stopped(tmp_path, capsys, index, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))

    def interrupt(request):
        if ticket_of(request) == "b":
            raise KeyboardInterrupt

    scripted.on_specialist = interrupt

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    capsys.readouterr()

    assert code == 130
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    events = [json.loads(line) for line in (run_dir / "journal.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "interrupted"
    assert {run_id: (record["state"], record["tickets_ended"]) for run_id, record in records_of(index).items()} == {run_dir.name: ("stopped", 1)}


def test_a_run_that_the_setup_check_stopped_has_a_record_with_the_state_stopped(tmp_path, capsys, index, scripted, home):
    repo = make_repo(tmp_path)
    write_agents(repo)
    scripted.denials = {GATE: REASON}

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    assert "setup-problem" in (run_dir / "journal.jsonl").read_text()
    assert {run_id: (record["state"], record["tickets_ended"]) for run_id, record in records_of(index).items()} == {run_dir.name: ("stopped", 0)}


def test_the_records_of_a_run_that_clean_forgot_do_not_come_back_at_delegate_runs_or_delegate_run(tmp_path, capsys, index, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert (code, err) == (0, "")
    run_id = read_journal(out)[0]["run_id"]
    assert list(records_of(index)) == [run_id]
    assert delegate.main(["clean", run_id, "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert records_of(index) == {}

    assert delegate.main(["runs"]) == 0
    capsys.readouterr()

    assert records_of(index) == {}

    other = make_repo(tmp_path / "other", HEAD + ticket("a"))
    code, out, err = run_main(capsys, str(other / "workflow.toml"), "--repo", str(other))
    assert (code, err) == (0, "")
    other_id = read_journal(out)[0]["run_id"]

    assert list(records_of(index)) == [other_id]


def test_a_run_that_clean_abandon_ended_gets_no_record_from_delegate_runs(tmp_path, capsys, index, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))

    def crash(request):
        if ticket_of(request) == "b":
            raise RuntimeError("harness binary missing")

    scripted.on_specialist = crash
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = read_journal(out)[0]["run_id"]
    assert list(records_of(index)) == [run_id]
    assert delegate.main(["clean", "--abandon", run_id, "--reason", "not needed", "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert records_of(index) == {}

    assert delegate.main(["runs"]) == 0
    capsys.readouterr()

    assert records_of(index) == {}


def test_a_built_run_with_no_record_that_clean_did_not_touch_gets_its_record_from_delegate_runs(tmp_path, capsys, index, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = read_journal(out)[0]["run_id"]
    (index / f"{run_id}.json").unlink()

    assert delegate.main(["runs"]) == 0
    capsys.readouterr()

    assert {run_id: record["state"] for run_id, record in records_of(index).items()} == {run_id: "built"}
