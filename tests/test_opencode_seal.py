"""The OpenCode adapter seals an agent as an OpenCode configuration, and runs the session from it (#214).

The seal holds the agent file as an entry of `agent` in a JSON file of the seal directory, with `mode: all`: OpenCode
refuses an agent with `mode: subagent` for `opencode run`, and runs its default agent instead. The adapter gives the
configuration to the session in `OPENCODE_CONFIG_CONTENT`, and it fails a session when the harness falls back to
another agent. Each case drives the real adapter on files that the renderer made. The `opencode` process is the
system boundary: a stand-in replays a recorded stream.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from delegate import adapters
from delegate.definitions.declaration import Agent
from delegate.definitions.render import render
from delegate.definitions.validate import split_frontmatter
from delegate.ports.harness import AdapterError, AdapterRequest, McpServer, SealedAgent

from . import opencode_fake

GATE = "python3 -m pytest -rs"
PROJECT = Path(".opencode/agents")
USER = Path(".config/opencode/agents")

# The warning that OpenCode 1.15.10 writes to standard error for a subagent (measured on 2026-10-31, issue 214).
SUBAGENT_WARNING = '\x1b[93m\x1b[1m! \x1b[0magent "python-verifier" is a subagent, not a primary agent. Falling back to default agent\n'


def write_pair(root: Path, directory: Path, tiers, *, mode: str | None = None) -> Path:
    """Write the OpenCode files of the pair `python` under `root / directory`. `mode` replaces the `mode` line, to make an old render."""
    target = root / directory
    target.mkdir(parents=True, exist_ok=True)
    for name, content in render([Agent(key="python", description="The python stack.", gate_commands=[GATE])], tiers)["opencode"].items():
        if mode is not None:
            assert "\nmode: all\n" in content
            content = content.replace("\nmode: all\n", f"\nmode: {mode}\n", 1)
        (target / name).write_text(content)
    return target


def config_of(sealed: SealedAgent) -> dict:
    [sealed_file] = sealed.files
    return json.loads(sealed_file.path.read_text())


def request(tmp_path: Path, sealed: SealedAgent | None, **changes) -> AdapterRequest:
    cwd = tmp_path / "work"
    cwd.mkdir(exist_ok=True)
    (tmp_path / "streams").mkdir(exist_ok=True)
    fields = dict(
        agent="python-verifier", model="anthropic/claude-opus-5", prompt="## Task\nTicket a\n", cwd=cwd,
        report_path=tmp_path / "reports" / "a.verifier.json", event_stream=tmp_path / "streams" / "a.stream.jsonl", sealed=sealed,
    )
    fields.update(changes)
    return AdapterRequest(**fields)


def test_the_renderer_gives_each_opencode_agent_the_mode_all(tmp_path, tiers):
    files = render([Agent(key="python", description="The python stack.", gate_commands=[GATE])], tiers)["opencode"]

    assert sorted(files) == ["python-specialist.md", "python-verifier.md"]
    assert {name: split_frontmatter(content)[0]["mode"] for name, content in files.items()} == {name: "all" for name in files}


def test_an_agent_is_sealed_as_an_entry_of_agent_in_a_json_file_of_the_seal_directory(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    source = write_pair(repo, PROJECT, tiers) / "python-verifier.md"
    write_pair(home, USER, tiers)  # the user directory holds a file too, and the project file wins
    (home / USER / "python-verifier.md").write_text("the user file\n")
    frontmatter, body, _ = split_frontmatter(source.read_text())

    sealed = adapters.get("opencode").seal("python-verifier", repo, tmp_path / "seal")

    assert isinstance(sealed, SealedAgent)
    assert (sealed.agent, sealed.handle, sealed.directory, sealed.source) == ("python-verifier", "python-verifier", tmp_path / "seal", source)
    [sealed_file] = sealed.files
    assert sealed_file.path.is_relative_to(tmp_path / "seal")
    assert sealed_file.sha256 == hashlib.sha256(sealed_file.path.read_bytes()).hexdigest()
    assert config_of(sealed) == {"agent": {"python-verifier": {**frontmatter, "prompt": body.strip()}}}
    assert source.is_file()  # the seal reads: the source stays


def test_an_agent_in_the_user_directory_is_sealed_when_the_project_has_none(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_pair(home, USER, tiers)

    sealed = adapters.get("opencode").seal("python-verifier", repo, tmp_path / "seal")

    assert sealed is not None
    assert sealed.source == home / USER / "python-verifier.md"
    assert config_of(sealed)["agent"]["python-verifier"]["mode"] == "all"


def test_a_file_with_mode_subagent_is_sealed_with_mode_all(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_pair(repo, PROJECT, tiers, mode="subagent")

    sealed = adapters.get("opencode").seal("python-verifier", repo, tmp_path / "seal")

    assert sealed is not None
    assert config_of(sealed)["agent"]["python-verifier"]["mode"] == "all"


def test_a_file_with_no_usable_frontmatter_is_not_sealed_and_the_reason_names_the_file(tmp_path, home):
    repo = tmp_path / "repo"
    (repo / PROJECT).mkdir(parents=True)
    (repo / PROJECT / "python-verifier.md").write_text("no frontmatter here\n")

    with pytest.raises(AdapterError, match="python-verifier.md"):
        adapters.get("opencode").seal("python-verifier", repo, tmp_path / "seal")


def test_the_guard_of_the_seal_is_the_permission_map_of_the_configuration(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_pair(repo, PROJECT, tiers, mode="subagent")
    adapter = adapters.get("opencode")
    sealed = adapter.seal("python-verifier", repo, tmp_path / "seal")

    assert sealed is not None
    assert adapter.permits(sealed, GATE) is None
    assert "deny" in adapter.permits(sealed, "make links")


def test_the_session_gets_the_sealed_agent_in_the_inline_configuration_and_starts_it_by_its_handle(tmp_path, home, tiers, monkeypatch):
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    repo = tmp_path / "repo"
    write_pair(repo, PROJECT, tiers, mode="subagent")
    adapter = adapters.get("opencode")
    sealed = adapter.seal("python-verifier", repo, tmp_path / "seal")
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    result = adapter.run(request(tmp_path, sealed))

    (call,) = fake.calls()
    assert json.loads(call["env"]["OPENCODE_CONFIG_CONTENT"]) == config_of(sealed)
    assert call["argv"][call["argv"].index("--agent") + 1] == "python-verifier"
    assert result.end_state == "finished" and result.reason is None


def test_the_sealed_agent_comes_with_the_mcp_servers_and_the_inline_configuration_of_the_environment(tmp_path, home, tiers, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", json.dumps({
        "share": "disabled", "agent": {"other": {"mode": "all"}, "python-verifier": {"prompt": "the prompt of the environment"}},
    }))
    repo = tmp_path / "repo"
    write_pair(repo, PROJECT, tiers)
    adapter = adapters.get("opencode")
    sealed = adapter.seal("python-verifier", repo, tmp_path / "seal")
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    adapter.run(request(tmp_path, sealed, mcp_servers=(McpServer("browser", command=("npx", "@playwright/mcp@latest")),)))

    (call,) = fake.calls()
    configuration = json.loads(call["env"]["OPENCODE_CONFIG_CONTENT"])
    assert configuration["share"] == "disabled"
    assert configuration["mcp"] == {"browser": {"type": "local", "command": ["npx", "@playwright/mcp@latest"]}}
    assert configuration["agent"]["other"] == {"mode": "all"}
    assert configuration["agent"]["python-verifier"] == config_of(sealed)["agent"]["python-verifier"]


def test_a_sealed_configuration_with_an_inline_configuration_that_is_not_a_json_object_raises_and_names_the_variable(
    tmp_path, home, tiers, monkeypatch
):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "[1, 2]")
    repo = tmp_path / "repo"
    write_pair(repo, PROJECT, tiers)
    adapter = adapters.get("opencode")
    sealed = adapter.seal("python-verifier", repo, tmp_path / "seal")
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    with pytest.raises(AdapterError, match="OPENCODE_CONFIG_CONTENT"):
        adapter.run(request(tmp_path, sealed))

    assert fake.calls() == []


def test_a_session_whose_stderr_holds_the_fall_back_warning_fails_and_the_reason_names_the_agent(tmp_path, monkeypatch):
    opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl", stderr=SUBAGENT_WARNING)

    result = adapters.get("opencode").run(request(tmp_path, None))

    assert result.end_state == "failed"
    assert result.session_id is None  # a continuation must not resume the session of the default agent
    assert result.reason is not None
    assert "python-verifier" in result.reason and "default agent" in result.reason


def test_a_warning_about_another_agent_than_the_one_of_the_session_also_fails_the_session(tmp_path, monkeypatch):
    opencode_fake.install(
        tmp_path, monkeypatch, "finished.jsonl",
        stderr='! agent "python-specialist" is a subagent, not a primary agent. Falling back to default agent\n',
    )

    result = adapters.get("opencode").run(request(tmp_path, None))

    assert result.end_state == "failed"
    assert "python-specialist" in (result.reason or "")


def test_a_session_with_other_text_on_stderr_is_not_failed(tmp_path, monkeypatch):
    opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl", stderr="Performing one time database migration\n")

    result = adapters.get("opencode").run(request(tmp_path, None))

    assert (result.end_state, result.reason) == ("finished", None)
