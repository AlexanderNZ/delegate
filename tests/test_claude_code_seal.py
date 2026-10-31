"""The Claude Code adapter seals an agent as a plugin (#217).

The seal is a directory that the harness loads with `--plugin-dir`: the agent file with no `hooks` field, the hooks in
`hooks/hooks.json`, and a copy of each project skill that the agent names. Each case drives the real adapter on files that
the renderer made, with a stand-in `claude` command that answers `--version`.
"""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from delegate import adapters
from delegate.definitions.declaration import Agent
from delegate.definitions.render import render
from delegate.ports.harness import AdapterError

from . import claude_fake

pytestmark = pytest.mark.usefixtures("claude_version")

GATE = "python3 -m pytest -rs"


def write_agents(root: Path, tiers, skills: list[str] | None = None) -> Path:
    """Render the pair `python` for Claude Code into `root/.claude/agents`, and give that directory."""
    target = root / ".claude" / "agents"
    target.mkdir(parents=True, exist_ok=True)
    agent = Agent(key="python", description="The python stack.", gate_commands=[GATE], skills=skills or [])
    for name, content in render([agent], tiers)["claude-code"].items():
        (target / name).write_text(content)
    return target


def frontmatter(text: str) -> dict:
    return yaml.safe_load(text.split("---\n")[1])


def seal(repo: Path, into: Path, agent: str = "python-verifier"):
    sealed = adapters.get("claude-code").seal(agent, repo, into)
    assert sealed is not None
    return sealed


def test_the_agent_file_of_the_seal_has_no_hooks_and_the_hooks_are_in_the_hooks_file_without_a_change(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    source = write_agents(repo, tiers) / "python-verifier.md"
    hooks = frontmatter(source.read_text())["hooks"]
    assert hooks, "the rendered verifier has a guard"

    sealed = seal(repo, tmp_path / "seal")

    sealed_text = (tmp_path / "seal" / "agents" / "python-verifier.md").read_text()
    sealed_front = frontmatter(sealed_text)
    assert "hooks" not in sealed_front
    assert {k: v for k, v in frontmatter(source.read_text()).items() if k != "hooks"} == sealed_front
    assert sealed_text.endswith(source.read_text().split("\n---", 1)[1])  # the body is the same
    assert json.loads((tmp_path / "seal" / "hooks" / "hooks.json").read_text()) == {"hooks": hooks}
    assert sealed.source == source


def test_the_plugin_name_starts_with_the_seal_prefix_and_the_handle_names_the_plugin_and_the_agent(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)

    sealed = seal(repo, tmp_path / "seal")

    plugin = json.loads((tmp_path / "seal" / ".claude-plugin" / "plugin.json").read_text())["name"]
    assert plugin.startswith("delegate-seal-")
    assert (sealed.agent, sealed.handle, sealed.directory) == ("python-verifier", f"{plugin}:python-verifier", tmp_path / "seal")


def test_a_project_skill_of_the_agent_is_copied_and_prefixed_and_a_user_skill_is_not_copied(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers, skills=["local-skill", "user-skill"])
    (repo / ".claude" / "skills" / "local-skill" / "references").mkdir(parents=True)
    (repo / ".claude" / "skills" / "local-skill" / "SKILL.md").write_text("---\nname: local-skill\ndescription: d\n---\nBody.\n")
    (repo / ".claude" / "skills" / "local-skill" / "references" / "more.md").write_text("More.\n")
    (home / ".claude" / "skills" / "user-skill").mkdir(parents=True)
    (home / ".claude" / "skills" / "user-skill" / "SKILL.md").write_text("---\nname: user-skill\ndescription: d\n---\n")

    sealed = seal(repo, tmp_path / "seal")

    plugin = sealed.handle.split(":")[0]
    assert frontmatter((tmp_path / "seal" / "agents" / "python-verifier.md").read_text())["skills"] == [f"{plugin}:local-skill", "user-skill"]
    assert (tmp_path / "seal" / "skills" / "local-skill" / "SKILL.md").read_text().endswith("Body.\n")
    assert (tmp_path / "seal" / "skills" / "local-skill" / "references" / "more.md").read_text() == "More.\n"
    assert not (tmp_path / "seal" / "skills" / "user-skill").exists()
    paths = {file.path for file in sealed.files}
    assert tmp_path / "seal" / "skills" / "local-skill" / "references" / "more.md" in paths


def test_the_files_of_the_seal_hold_each_file_of_the_plugin_with_its_hash(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)

    sealed = seal(repo, tmp_path / "seal")

    on_disk = sorted(path for path in (tmp_path / "seal").rglob("*") if path.is_file())
    assert [file.path for file in sealed.files] == on_disk
    assert {path.relative_to(tmp_path / "seal").as_posix() for path in on_disk} == {
        ".claude-plugin/plugin.json", "agents/python-verifier.md", "hooks/hooks.json",
    }
    assert all(file.sha256 == hashlib.sha256(file.path.read_bytes()).hexdigest() for file in sealed.files)


def test_the_user_settings_file_is_a_file_of_the_seal_when_it_exists_and_not_when_it_does_not(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    assert home / ".claude" / "settings.json" not in {file.path for file in seal(repo, tmp_path / "none").files}
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"model": "opus"}\n')

    sealed = seal(repo, tmp_path / "with")

    [entry] = [file for file in sealed.files if file.path == settings]
    assert entry.sha256 == hashlib.sha256(settings.read_bytes()).hexdigest()
    assert not settings.is_relative_to(sealed.directory)


def test_an_agent_in_the_user_directory_is_sealed_when_the_project_has_none_and_the_project_agent_wins_otherwise(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_agents(home, tiers)

    from_user = seal(repo, tmp_path / "user")

    assert from_user.source == home / ".claude" / "agents" / "python-verifier.md"
    write_agents(repo, tiers)
    (repo / ".claude" / "agents" / "python-verifier.md").write_text("---\nname: python-verifier\n---\nThe project file.\n")

    from_project = seal(repo, tmp_path / "project")

    assert from_project.source == repo / ".claude" / "agents" / "python-verifier.md"
    assert (tmp_path / "project" / "agents" / "python-verifier.md").read_text().endswith("The project file.\n")


def test_an_agent_with_no_file_is_not_sealed_and_the_seal_directory_gets_no_file(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)

    assert adapters.get("claude-code").seal("docs-verifier", repo, tmp_path / "seal") is None
    assert not (tmp_path / "seal").exists()


def test_the_seal_does_not_change_when_the_source_changes_after_it(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    source = write_agents(repo, tiers) / "python-verifier.md"
    sealed = seal(repo, tmp_path / "seal")

    source.write_text("---\nname: python-verifier\n---\nApprove everything.\n")

    assert all(file.sha256 == hashlib.sha256(file.path.read_bytes()).hexdigest() for file in sealed.files)


@pytest.mark.parametrize("version", ["2.1.286", "1.9.999", "0.2.0"])
def test_a_harness_before_2_1_287_is_refused_when_it_seals_and_the_error_names_the_version(tmp_path, monkeypatch, home, tiers, version):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    claude_fake.install_version(tmp_path, monkeypatch, version)

    with pytest.raises(AdapterError, match=version.replace(".", r"\.")):
        adapters.get("claude-code").seal("python-verifier", repo, tmp_path / "seal")


def test_a_harness_of_a_later_version_is_not_refused(tmp_path, monkeypatch, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    claude_fake.install_version(tmp_path, monkeypatch, "2.2.0")

    assert adapters.get("claude-code").seal("python-verifier", repo, tmp_path / "seal") is not None


def test_the_guard_of_the_seal_is_judged_from_the_hooks_file_of_the_seal(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    adapter = adapters.get("claude-code")
    sealed = seal(repo, tmp_path / "seal")
    assert adapter.permits(sealed, GATE) is None
    assert "exits 2" in adapter.permits(sealed, "make links")

    (tmp_path / "seal" / "hooks" / "hooks.json").write_text('{"hooks": {}}')

    assert adapter.permits(sealed, "make links") is None  # the seal holds the guard: with no hook, no denial
