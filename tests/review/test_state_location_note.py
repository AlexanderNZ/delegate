"""The state location gives its warning as a value, and the driver of each command prints it once (#331).

The warning of a state directory that the variable and the `state-root` file name in two ways used to go to standard error from
`locate_state`, once for each process. A caller that is a library could not route it, and the result depended on the calls before it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from delegate import adapters
from delegate.adapters.git import GitVersionControl
from delegate.cli import delegate
from delegate.run.state_location import STATE_ROOT_FILE, STATE_ROOT_VARIABLE, locate_state

from ..support import ScriptedAdapter, make_repo

MARKER = "names the state directory"


def conflict(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """A repository whose `state-root` file names one directory while the variable names another. Return the repository and the recorded directory."""
    repo = make_repo(tmp_path)
    recorded = tmp_path / "recorded"
    (repo / ".git" / "delegate").mkdir()
    (repo / ".git" / "delegate" / STATE_ROOT_FILE).write_text(f"{recorded}\n")
    monkeypatch.setenv(STATE_ROOT_VARIABLE, str(tmp_path / "root"))
    return repo, recorded


def test_locate_state_writes_nothing_to_standard_error_and_gives_the_warning_as_the_note(tmp_path, capsys, git_identity, monkeypatch):
    repo, recorded = conflict(tmp_path, monkeypatch)

    location = locate_state(repo, GitVersionControl())

    chosen = location.directories[0]
    assert capsys.readouterr().err == ""
    assert location.note == (
        f"delegate: {STATE_ROOT_VARIABLE} names the state directory {chosen}, "
        f"but {location.shared / 'delegate' / STATE_ROOT_FILE} names {recorded}; using {chosen}"
    )


def test_a_second_lookup_in_the_same_process_gives_the_note_again(tmp_path, git_identity, monkeypatch):
    repo, _ = conflict(tmp_path, monkeypatch)

    first = locate_state(repo, GitVersionControl())
    second = locate_state(repo, GitVersionControl())

    assert first.note is not None and second.note == first.note


def test_a_state_root_file_that_agrees_with_the_variable_gives_no_note(tmp_path, git_identity, monkeypatch):
    repo, _ = conflict(tmp_path, monkeypatch)
    chosen = locate_state(repo, GitVersionControl()).directories[0]
    (repo / ".git" / "delegate" / STATE_ROOT_FILE).write_text(f"{chosen}\n")

    assert locate_state(repo, GitVersionControl()).note is None


def test_a_lookup_with_no_variable_gives_no_note(tmp_path, git_identity, monkeypatch):
    repo, _ = conflict(tmp_path, monkeypatch)
    monkeypatch.delenv(STATE_ROOT_VARIABLE)

    assert locate_state(repo, GitVersionControl()).note is None


COMMANDS = {
    "status": ["status", "--repo", "{repo}"],
    "watch": ["watch", "--repo", "{repo}"],
    "status of a run": ["status", "abc", "--repo", "{repo}"],
    "pause": ["pause", "abc", "--repo", "{repo}"],
    "stop": ["stop", "abc", "--repo", "{repo}"],
    "clean": ["clean", "abc", "--repo", "{repo}"],
    "clean --abandon": ["clean", "--abandon", "abc", "--reason", "why", "--repo", "{repo}"],
    "run --resume": ["run", "--resume", "abc", "--repo", "{repo}"],
    "run --dry-run": ["run", "{workflow}", "--dry-run", "--repo", "{repo}"],
}


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_each_command_prints_the_note_one_time_and_does_so_again_in_the_next_command_of_the_process(
    name, tmp_path, capsys, git_identity, monkeypatch
):
    repo, _ = conflict(tmp_path, monkeypatch)
    argv = [part.format(repo=repo, workflow=repo / "workflow.toml") for part in COMMANDS[name]]

    adapters.register("scripted", ScriptedAdapter())  # the dry run reads the adapter of the workflow
    try:
        for _ in range(2):
            delegate.main(argv)
            err = capsys.readouterr().err

            assert err.count(MARKER) == 1, err
    finally:
        adapters.unregister("scripted")
