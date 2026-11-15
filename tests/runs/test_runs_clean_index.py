"""`delegate clean` and the run index (#317): the command forgets the record of each run that it removes.

Each case makes a real run in a real temporary git repository with the scripted adapter, and reads the files of the index
directory (the directory backend) after `delegate clean`. The run that ended built has its record removed by `delegate clean <run>`
(the case is in `test_runs_hosts`); the cases here are the other ways to remove a run.
"""

from __future__ import annotations

import os

import pytest

from delegate import adapters
from delegate.adapters.run_index import DirectoryRunIndex
from delegate.cli import delegate

from ..support import HEAD, ScriptedAdapter, make_repo, read_journal, run_main, ticket
from ..test_clean_abandon import REASON, a_paused_run


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abcx"})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def test_clean_ended_forgets_the_record_of_each_run_that_ended_built(tmp_path, capsys, monkeypatch, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    directory = tmp_path / "shared-index"
    monkeypatch.setenv("DELEGATE_INDEX", str(directory))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert (code, err) == (0, "")
    assert len(list(directory.iterdir())) == 1

    code = delegate.main(["clean", "--ended", "--repo", str(repo)])
    captured = capsys.readouterr()

    assert (code, captured.err) == (0, "")
    assert list(directory.iterdir()) == []


def test_a_run_that_is_paused_keeps_its_record_when_clean_keeps_everything_of_it(tmp_path, capsys, monkeypatch, scripted):
    directory = tmp_path / "shared-index"
    monkeypatch.setenv("DELEGATE_INDEX", str(directory))
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)
    assert [path.name for path in directory.iterdir()] == [f"{run_id}.json"]

    code = delegate.main(["clean", run_id, "--repo", str(repo)])
    captured = capsys.readouterr()

    assert (code, captured.err) == (0, "")
    assert f"run {run_id}: not cleaned" in captured.out
    assert [path.name for path in directory.iterdir()] == [f"{run_id}.json"]


def test_clean_abandon_forgets_the_record_of_the_run(tmp_path, capsys, monkeypatch, scripted):
    directory = tmp_path / "shared-index"
    monkeypatch.setenv("DELEGATE_INDEX", str(directory))
    repo, run_id = a_paused_run(tmp_path, capsys, scripted)

    code = delegate.main(["clean", "--abandon", run_id, "--reason", REASON, "--repo", str(repo)])
    captured = capsys.readouterr()

    assert (code, captured.err) == (0, "")
    assert list(directory.iterdir()) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root can remove a file from a directory that is read-only")
def test_a_record_that_cannot_be_forgotten_is_an_error_of_the_run_and_the_exit_code_is_1(tmp_path, capsys, monkeypatch, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    directory = tmp_path / "shared-index"
    monkeypatch.setenv("DELEGATE_INDEX", str(directory))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = read_journal(out)[0]["run_id"]
    directory.chmod(0o500)
    try:
        code = delegate.main(["clean", run_id, "--repo", str(repo)])
        captured = capsys.readouterr()
    finally:
        directory.chmod(0o700)

    assert code == 1
    assert f"cannot forget run {run_id}" in captured.err
    assert [record.run_id for record in DirectoryRunIndex(directory).list_records()] == [run_id]
