"""The default state root is outside the git directory (#200).

A tool that refuses a path with a `.git` component failed in each worktree of a ticket, because the default state
directory was `<git common dir>/delegate`: Claude Code in `dontAsk` mode refused `Edit` and `Write`, and the Vite dev
server refused to serve the files. By default the state of a repository is now in
`$XDG_STATE_HOME/delegate/<name>-<key>`, or `~/.local/state/delegate/<name>-<key>`. A run that started under
`<git common dir>/delegate` stays there, and each command still finds it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from delegate import adapters
from delegate.cli import delegate
from delegate.run.journal import Journal

from .support import (
    ScriptedAdapter, event_names, kill_a_run, live_lock, make_repo, read_journal, resume_main, run_end, run_start, step_end,
    step_start, write_journal,
)
from .test_run_multi import HEAD, ticket

ROOT_VARIABLE: str = "DELEGATE_STATE_ROOT"
THREE = HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c")


def run_events(repo: Path, capsys) -> list[dict[str, object]]:
    """Build `workflow.toml` of `repo` with a scripted adapter, and give the events of its journal."""
    adapters.register("scripted", ScriptedAdapter())
    try:
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
        out = capsys.readouterr().out
    finally:
        adapters.unregister("scripted")
    assert code == 0, out
    return read_journal(out)


def only_directory(root: Path) -> Path:
    (directory,) = root.iterdir()
    return directory


def test_with_no_state_root_the_run_keeps_its_state_under_xdg_state_home_and_no_path_has_a_git_component(tmp_path, capsys, git_identity, monkeypatch):
    xdg = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    monkeypatch.delenv(ROOT_VARIABLE, raising=False)
    repo = make_repo(tmp_path)

    events = run_events(repo, capsys)

    run_id = str(events[0]["run_id"])
    state = only_directory(xdg / "delegate")
    assert state.name.startswith(f"{repo.name}-")
    paths = [Path(str(event[field])) for event in events for field in ("worktree", "path", "event_stream") if field in event]
    assert any(path.name == "a" for path in paths)  # the worktree of the ticket
    assert all(path.is_relative_to(state) and ".git" not in path.parts for path in paths)
    assert (state / "runs" / run_id / "journal.jsonl").is_file()
    assert (state / "runs" / run_id / "reports").is_dir() and list((state / "runs" / run_id / "reports").iterdir())
    assert not (repo / ".git" / "delegate" / "runs").exists()


def test_with_no_xdg_state_home_the_root_is_dot_local_state_delegate_of_the_home(tmp_path, capsys, git_identity, monkeypatch):
    home = tmp_path / "test-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.delenv(ROOT_VARIABLE, raising=False)
    repo = make_repo(tmp_path)

    events = run_events(repo, capsys)

    state = only_directory(home / ".local" / "state" / "delegate")
    assert (state / "runs" / str(events[0]["run_id"]) / "journal.jsonl").is_file()


def test_an_xdg_state_home_that_is_empty_counts_as_not_set(tmp_path, capsys, git_identity, monkeypatch):
    home = tmp_path / "test-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", "")
    monkeypatch.delenv(ROOT_VARIABLE, raising=False)
    repo = make_repo(tmp_path)

    events = run_events(repo, capsys)

    state = only_directory(home / ".local" / "state" / "delegate")
    assert (state / "runs" / str(events[0]["run_id"]) / "journal.jsonl").is_file()


def test_the_state_root_variable_still_sets_the_root(tmp_path, capsys, git_identity, monkeypatch):
    root = tmp_path / "chosen-root"
    monkeypatch.setenv(ROOT_VARIABLE, str(root))
    repo = make_repo(tmp_path)

    events = run_events(repo, capsys)

    assert (only_directory(root) / "runs" / str(events[0]["run_id"]) / "journal.jsonl").is_file()
    assert not (tmp_path.parent / "xdg-state").exists()


def test_a_new_run_writes_state_root_and_status_finds_it_from_another_xdg_state_home(tmp_path, capsys, git_identity, monkeypatch):
    repo = make_repo(tmp_path)
    run_id = str(run_events(repo, capsys)[0]["run_id"])
    state = only_directory(Path(os.environ["XDG_STATE_HOME"]) / "delegate")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "another-shell"))

    code = delegate.main(["status", run_id, "--repo", str(repo), "--json"])
    out, err = capsys.readouterr()

    assert (repo / ".git" / "delegate" / "state-root").read_text() == f"{state}\n"
    assert code == 0, err
    assert json.loads(out)["state"] == "built"


def move_to_the_git_directory(repo: Path, run_id: str) -> Path:
    """Move the state of a run that the engine wrote to `<git common dir>/delegate`, where a version before #200 kept it.

    Return the journal. The state-root file goes too, as an older version wrote none.
    """
    recorded = repo / ".git" / "delegate" / "state-root"
    source = Path(recorded.read_text().strip())
    recorded.unlink()
    target = repo / ".git" / "delegate"
    for entry in source.iterdir():
        shutil.move(str(entry), str(target / entry.name))
    shutil.rmtree(source)
    for worktree in (target / "worktrees" / run_id).glob("*"):
        subprocess.run(["git", "-C", str(repo), "worktree", "repair", str(worktree)], check=True, capture_output=True)
    return target / "runs" / run_id / "journal.jsonl"


def test_status_finds_a_run_that_started_in_the_git_directory(tmp_path, capsys, git_identity):
    repo = make_repo(tmp_path)
    run_id = "20250101T000000Z-aaaa"
    write_journal(repo, run_id, run_start(run_id, "a"), step_start("a"), step_end("a"), run_end("built", ["a"]))
    assert (repo / ".git" / "delegate" / "runs" / run_id / "journal.jsonl").is_file()

    code = delegate.main(["status", run_id, "--repo", str(repo), "--json"])
    out, err = capsys.readouterr()

    assert code == 0, err
    assert json.loads(out)["run_id"] == run_id


def test_a_run_that_started_in_the_git_directory_resumes_with_run_resume_and_stays_there(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id, _ = kill_a_run(repo, 3)  # killed as the specialist of ticket b starts
    journal = move_to_the_git_directory(repo, run_id)
    before = journal.read_bytes()

    code, out, err = resume_main(repo, capsys, run_id, "--break-lock")

    assert (code, err) == (0, "")
    assert journal.read_bytes().startswith(before) and len(journal.read_bytes()) > len(before)
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    assert event_names(events).count("resume") == 1 and events[-1]["event"] == "run-end"
    assert not list((Path(os.environ["XDG_STATE_HOME"]) / "delegate").rglob("journal.jsonl"))
    assert not (repo / ".git" / "delegate" / "state-root").exists()


def test_status_with_no_run_id_lists_the_active_runs_of_both_directories_newest_first(tmp_path, capsys, git_identity):
    repo = make_repo(tmp_path)
    run_events(repo, capsys)  # one run, so the repository has its directory under the state root
    new_state = Path((repo / ".git" / "delegate" / "state-root").read_text().strip())
    old_id, new_id = "20250101T000000Z-0ld0", "20250101T000001Z-0new"
    old, new = (
        repo / ".git" / "delegate" / "runs" / old_id / "journal.jsonl",  # the directory of the versions before #200
        new_state / "runs" / new_id / "journal.jsonl",
    )
    for path, run_id, branch in ((old, old_id, "run/old"), (new, new_id, "run/new")):
        journal = Journal(path)
        for name, fields in (run_start(run_id, "a", run_branch=branch), step_start("a")):
            journal.append(name, **fields)
        time.sleep(0.05)  # the second run starts later than the first

    with live_lock(old, "run/old"), live_lock(new, "run/new"):
        code = delegate.main(["status", "--repo", str(repo)])
    out, err = capsys.readouterr()

    assert code == 0, err
    assert "2 runs are active" in out
    assert out.index("0new") < out.index("0ld0")


def test_run_dry_run_prints_the_state_directory(tmp_path, capsys, git_identity, monkeypatch):
    xdg = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    repo = make_repo(tmp_path)
    adapters.register("scripted", ScriptedAdapter())
    try:
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run"])
    finally:
        adapters.unregister("scripted")
    out, err = capsys.readouterr()

    assert code == 0, err
    (line,) = [line for line in out.splitlines() if line.startswith("state directory ")]
    printed = Path(line.removeprefix("state directory "))
    assert printed.parent == xdg / "delegate" and printed.name.startswith(f"{repo.name}-")
    assert not (xdg / "delegate").exists()  # a dry run creates nothing
