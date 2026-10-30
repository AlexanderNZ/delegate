"""Where each harness adapter finds the file of an agent: the project directory first, then the user directory (#205)."""

from pathlib import Path

import pytest
from delegate import adapters

# The adapter name, the project directory of its agent files, and the user directory of them under the home directory.
HARNESSES = [
    pytest.param("claude-code", Path(".claude/agents"), Path(".claude/agents"), id="claude-code"),
    pytest.param("opencode", Path(".opencode/agents"), Path(".config/opencode/agents"), id="opencode"),
]


def write(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("rules\n")
    return path


@pytest.mark.parametrize(("name", "project", "user"), HARNESSES)
def test_the_project_file_comes_before_the_user_file(tmp_path, monkeypatch, name, project, user):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    in_project = write(repo / project / "python-verifier.md")
    write(tmp_path / "home" / user / "python-verifier.md")

    assert adapters.get(name).agent_file("python-verifier", repo) == in_project


@pytest.mark.parametrize(("name", "project", "user"), HARNESSES)
def test_a_file_in_the_user_directory_outside_the_repository_is_found_when_the_project_has_none(tmp_path, monkeypatch, name, project, user):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "repo").mkdir()
    in_home = write(tmp_path / "home" / user / "python-verifier.md")

    assert adapters.get(name).agent_file("python-verifier", tmp_path / "repo") == in_home


@pytest.mark.parametrize(("name", "project", "user"), HARNESSES)
def test_an_agent_with_no_file_has_no_path(tmp_path, monkeypatch, name, project, user):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "repo").mkdir()
    write(tmp_path / "repo" / project / "python-specialist.md")

    assert adapters.get(name).agent_file("python-verifier", tmp_path / "repo") is None
