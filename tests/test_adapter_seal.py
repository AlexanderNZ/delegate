"""The harness port seals an agent and judges a command against the guard of the sealed agent (#216).

`seal` finds the agent file of an agent for a directory (the project directory first, then the user directory) and
writes the seal into another directory. `permits` runs the guard of the sealed agent on one command: None when the guard
permits it, else the reason of the denial. Each case drives the real adapter on real files that the renderer made.
"""

import hashlib
from pathlib import Path

import pytest
from delegate import adapters
from delegate.definitions.declaration import Agent
from delegate.definitions.render import render
from delegate.ports.harness import SealedAgent

pytestmark = pytest.mark.usefixtures("claude_version")

# The adapter name, the project directory of its agent files, and the user directory of them under the home directory.
HARNESSES = [
    pytest.param("claude-code", Path(".claude/agents"), Path(".claude/agents"), id="claude-code"),
    pytest.param("opencode", Path(".opencode/agents"), Path(".config/opencode/agents"), id="opencode"),
]

# The harnesses that seal by a copy of the agent file. The Claude Code seal is a plugin: see tests/test_claude_code_seal.py.
COPY_SEALS = [HARNESSES[1]]

GATE = "python3 -m pytest -rs"


def write_pair(root: Path, name: str, directory: Path, tiers) -> Path:
    """Render the pair `python` with the gate `GATE` and write its files under `root / directory`."""
    target = root / directory
    target.mkdir(parents=True, exist_ok=True)
    for file_name, content in render([Agent(key="python", description="The python stack.", gate_commands=[GATE])], tiers)[name].items():
        (target / file_name).write_text(content)
    return target


@pytest.mark.parametrize(("name", "project", "user"), COPY_SEALS)
def test_an_agent_in_the_project_directory_is_sealed_as_a_copy_in_the_seal_directory(tmp_path, home, tiers, name, project, user):
    repo = tmp_path / "repo"
    source = write_pair(repo, name, project, tiers) / "python-verifier.md"
    write_pair(home, name, user, tiers)  # the user directory holds a file too, and the project file wins
    (home / user / "python-verifier.md").write_text("the user file\n")

    sealed = adapters.get(name).seal("python-verifier", repo, tmp_path / "seal")

    assert isinstance(sealed, SealedAgent)
    assert (sealed.agent, sealed.handle, sealed.directory) == ("python-verifier", "python-verifier", tmp_path / "seal")
    [sealed_file] = sealed.files
    assert sealed_file.path.read_bytes() == source.read_bytes()
    assert sealed_file.path.is_relative_to(tmp_path / "seal")
    assert sealed_file.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize(("name", "project", "user"), COPY_SEALS)
def test_an_agent_in_the_user_directory_is_sealed_when_the_project_has_none(tmp_path, home, tiers, name, project, user):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = write_pair(home, name, user, tiers) / "python-verifier.md"

    sealed = adapters.get(name).seal("python-verifier", repo, tmp_path / "seal")

    assert sealed is not None
    [sealed_file] = sealed.files
    assert sealed_file.path.read_bytes() == source.read_bytes()
    assert sealed_file.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert source.is_file()  # the seal copies: the source stays


@pytest.mark.parametrize(("name", "project", "user"), HARNESSES)
def test_a_missing_agent_is_not_sealed_and_the_seal_directory_gets_no_file(tmp_path, home, tiers, name, project, user):
    repo = tmp_path / "repo"
    write_pair(repo, name, project, tiers)

    sealed = adapters.get(name).seal("docs-verifier", repo, tmp_path / "seal")

    assert sealed is None
    assert not (tmp_path / "seal").exists() or list((tmp_path / "seal").iterdir()) == []


@pytest.mark.parametrize(("name", "project", "user"), HARNESSES)
def test_the_guard_of_a_sealed_verifier_permits_its_gate(tmp_path, home, tiers, name, project, user):
    repo = tmp_path / "repo"
    write_pair(repo, name, project, tiers)
    adapter = adapters.get(name)
    sealed = adapter.seal("python-verifier", repo, tmp_path / "seal")

    assert sealed is not None
    assert adapter.permits(sealed, GATE) is None


@pytest.mark.parametrize(("name", "project", "user"), HARNESSES)
def test_the_guard_of_a_sealed_verifier_gives_the_reason_for_a_command_that_it_denies(tmp_path, home, tiers, name, project, user):
    repo = tmp_path / "repo"
    write_pair(repo, name, project, tiers)
    adapter = adapters.get(name)
    sealed = adapter.seal("python-verifier", repo, tmp_path / "seal")

    assert sealed is not None
    reason = adapter.permits(sealed, "make links")
    assert isinstance(reason, str) and reason


def test_the_claude_code_reason_names_the_exit_status_and_the_message_of_the_hook(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_pair(repo, "claude-code", Path(".claude/agents"), tiers)
    adapter = adapters.get("claude-code")
    sealed = adapter.seal("python-verifier", repo, tmp_path / "seal")

    reason = adapter.permits(sealed, "make links")

    assert "exits 2" in reason


def test_the_opencode_reason_names_the_action_of_the_permission_map(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_pair(repo, "opencode", Path(".opencode/agents"), tiers)
    adapter = adapters.get("opencode")
    sealed = adapter.seal("python-verifier", repo, tmp_path / "seal")

    reason = adapter.permits(sealed, "make links")

    assert "deny" in reason


def test_the_guard_judges_the_sealed_copy_and_not_the_source(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    source = write_pair(repo, "claude-code", Path(".claude/agents"), tiers) / "python-verifier.md"
    adapter = adapters.get("claude-code")
    sealed = adapter.seal("python-verifier", repo, tmp_path / "seal")
    source.write_text("---\nname: python-verifier\n---\nno guard now\n")  # the source changes after the seal

    assert adapter.permits(sealed, GATE) is None
    assert adapter.permits(sealed, "make links") is not None
