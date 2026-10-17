"""`DELEGATE_STATE_ROOT` moves the state of the engine out of the git directory (#97).

Claude Code in `dontAsk` mode refuses `Edit` and `Write` on a path with a `.git`
component, also when an allow rule permits them. With the state in
`<git common dir>/delegate`, a specialist on such a host can write neither in
its worktree nor its report. One value serves every repository of a host, so
the engine keeps one directory under the root for each repository.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, WORKFLOW, make_repo, read_journal

ROOT_VARIABLE: str = "DELEGATE_STATE_ROOT"


def run_in(tmp_path: Path, capsys, repo: Path) -> list[dict[str, object]]:
    """Build `workflow.toml` of `repo` with a scripted adapter, and give the events of its journal."""
    adapters.register("scripted", ScriptedAdapter())
    try:
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
        out = capsys.readouterr().out
    finally:
        adapters.unregister("scripted")
    assert code == 0, out
    return read_journal(out)


def journal_of(out: str) -> Path:
    (line,) = [line for line in out.splitlines() if line.startswith("journal ")]
    return Path(line.removeprefix("journal "))


def test_with_a_state_root_the_journal_the_worktrees_and_the_reports_are_under_it_with_no_git_component(tmp_path, capsys, git_identity, monkeypatch):
    root = tmp_path / "state-root"
    monkeypatch.setenv(ROOT_VARIABLE, str(root))
    repo = make_repo(tmp_path)

    events = run_in(tmp_path, capsys, repo)

    paths = [Path(str(event[field])) for event in events for field in ("worktree", "path", "event_stream") if field in event]
    (start,) = [event for event in events if event["event"] == "step-start"]
    assert Path(str(start["worktree"])).is_relative_to(root)
    assert any(event["event"] == "report-validation" for event in events)
    assert paths and all(path.is_relative_to(root) for path in paths)
    assert all(".git" not in path.relative_to(root).parts for path in paths)
    assert not (repo / ".git" / "delegate" / "runs").exists()


def test_two_repositories_with_the_same_state_root_get_two_directories(tmp_path, capsys, git_identity, monkeypatch):
    root = tmp_path / "state-root"
    monkeypatch.setenv(ROOT_VARIABLE, str(root))
    first, second = make_repo(tmp_path / "one"), make_repo(tmp_path / "two")

    first_events, second_events = run_in(tmp_path, capsys, first), run_in(tmp_path, capsys, second)

    first_worktree = Path(str(next(e for e in first_events if e["event"] == "step-start")["worktree"]))
    second_worktree = Path(str(next(e for e in second_events if e["event"] == "step-start")["worktree"]))
    first_dir, second_dir = (path.relative_to(root).parts[0] for path in (first_worktree, second_worktree))
    assert first_dir != second_dir


def test_status_and_watch_find_a_run_under_the_state_root(tmp_path, capsys, git_identity, monkeypatch):
    root = tmp_path / "state-root"
    monkeypatch.setenv(ROOT_VARIABLE, str(root))
    repo = make_repo(tmp_path)
    run_id = str(run_in(tmp_path, capsys, repo)[0]["run_id"])

    status_code = delegate.main(["status", run_id, "--repo", str(repo), "--json"])
    snapshot = json.loads(capsys.readouterr().out)
    watch_code = delegate.main(["watch", run_id, "--repo", str(repo), "--poll-seconds", "0.02"])
    watch_out = capsys.readouterr().out

    assert [path.parent.name for path in root.rglob("journal.jsonl")] == [run_id]
    assert status_code == 0
    assert snapshot["state"] == "built"
    assert watch_code == 0, watch_out


def test_a_resume_finds_its_run_under_the_state_root(tmp_path, capsys, git_identity, monkeypatch):
    root = tmp_path / "state-root"
    monkeypatch.setenv(ROOT_VARIABLE, str(root))
    repo = make_repo(tmp_path)
    run_id = str(run_in(tmp_path, capsys, repo)[0]["run_id"])

    adapters.register("scripted", ScriptedAdapter())
    try:
        code = delegate.main(["run", "--resume", run_id, "--repo", str(repo)])
    finally:
        adapters.unregister("scripted")
    err = capsys.readouterr().err

    # The run ended `built`, so the resume refuses it: the engine found the journal under the root, and nowhere else.
    assert not (repo / ".git" / "delegate" / "runs").exists()
    assert code == 1
    assert "has ended with the result 'built'" in err


def test_with_no_state_root_the_state_is_in_the_git_common_directory(tmp_path, capsys, git_identity, monkeypatch):
    monkeypatch.delenv(ROOT_VARIABLE, raising=False)
    repo = make_repo(tmp_path)

    events = run_in(tmp_path, capsys, repo)

    (start,) = [event for event in events if event["event"] == "step-start"]
    assert Path(str(start["worktree"])).is_relative_to((repo / ".git" / "delegate").resolve())


def test_a_state_root_that_is_not_an_absolute_path_is_an_error(tmp_path, capsys, git_identity, monkeypatch):
    monkeypatch.setenv(ROOT_VARIABLE, "relative/root")
    repo = make_repo(tmp_path)

    code = delegate.main(["status", "--repo", str(repo)])
    err = capsys.readouterr().err

    assert code != 0
    assert ROOT_VARIABLE in err and "absolute" in err
    assert "not a git repository" not in err


def test_a_linked_worktree_of_the_repository_finds_the_same_state_directory(tmp_path, capsys, git_identity, monkeypatch):
    # The state of a repository is one directory for all its worktrees, as `<git common dir>/delegate` is: the
    # coordinator can work in a linked worktree, and the run lock covers every worktree.
    root = tmp_path / "state-root"
    monkeypatch.setenv(ROOT_VARIABLE, str(root))
    repo = make_repo(tmp_path)
    run_id = str(run_in(tmp_path, capsys, repo)[0]["run_id"])
    linked = tmp_path / "feature-wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feature", str(linked)], check=True)

    code = delegate.main(["status", run_id, "--repo", str(linked), "--json"])
    out, err = capsys.readouterr()

    assert code == 0, err
    assert json.loads(out)["state"] == "built"
    assert len(list(root.iterdir())) == 1  # one directory for the repository


def test_a_clone_with_a_separate_git_directory_names_its_directory_after_the_git_directory(tmp_path, capsys, git_identity, monkeypatch):
    # The agent host clones with `--separate-git-dir`, so its git directory is not called `.git`.
    root = tmp_path / "state-root"
    monkeypatch.setenv(ROOT_VARIABLE, str(root))
    source = make_repo(tmp_path / "source")
    work, gitdir = tmp_path / "work", tmp_path / "gitdirs" / "proj"
    gitdir.parent.mkdir()
    cloned = subprocess.run(["git", "clone", "-q", "--separate-git-dir", str(gitdir), str(source), str(work)], capture_output=True, text=True)
    assert cloned.returncode == 0, cloned.stderr

    run_id = str(run_in(tmp_path, capsys, work)[0]["run_id"])

    (directory,) = root.iterdir()
    assert directory.name.startswith("proj-")
    assert (directory / "runs" / run_id / "journal.jsonl").is_file()
    assert not (gitdir / "delegate").exists()
