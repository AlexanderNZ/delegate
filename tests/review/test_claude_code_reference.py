"""The Claude Code adapter reference states the command that the adapter starts, the recorded streams and the live smoke run (#355).

The page is outside the package source, as the README is, so a Nix build that copies only the package skips these cases with a reason.
"""

from __future__ import annotations

import json
import re

import pytest
from delegate import adapters
from delegate.ports.harness import AdapterRequest, McpServer, SealedAgent

from .. import claude_fake
from ..pages import ROOT, outside_the_package

REFERENCE = ROOT / "docs" / "reference" / "claude-code-adapter.md"


def command_section() -> str:
    text = REFERENCE.read_text()
    return text.split("## The command", 1)[1].split("\n## ", 1)[0]


def options_of_the_longest_command(tmp_path, monkeypatch) -> set[str]:
    """The options of the longest command that the adapter builds: a continuation with MCP servers, a proof copy and the two host settings."""
    settings = tmp_path / "host-settings.json"
    settings.write_text("{}")
    monkeypatch.setenv("DELEGATE_AGENT_SETTINGS", str(settings))
    monkeypatch.setenv("DELEGATE_PERMISSION_MODE", "acceptEdits")
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    proof = tmp_path / "proof"
    proof.mkdir()
    servers = (McpServer("tracker", url="https://mcp.example.invalid/mcp"),)

    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "streams").mkdir()
    agent = SealedAgent("python-specialist", "delegate-seal-python-specialist:python-specialist", tmp_path / "seal", ())
    adapters.get("claude-code").run(AdapterRequest(
        agent="python-specialist", model="opus", prompt="Ticket a\n", cwd=work, report_path=tmp_path / "a.json",
        event_stream=tmp_path / "streams" / "a.jsonl", sealed=agent, resume_session="session-9", mcp_servers=servers,
        added_directories=(proof,),
    ))

    (call,) = fake.calls()
    options = {arg for arg in call["argv"] if arg.startswith("--")}
    assert {"--resume", "--mcp-config", "--settings", "--permission-mode", "--add-dir"} <= options, "the command is not the longest one"
    return options


@outside_the_package
def test_the_page_names_each_option_that_the_adapter_puts_on_the_command(tmp_path, monkeypatch):
    section = command_section()
    assert sorted(option for option in options_of_the_longest_command(tmp_path, monkeypatch) if f"`{option}" not in section) == []


@outside_the_package
def test_the_page_names_no_option_that_the_adapter_does_not_put_on_the_command(tmp_path, monkeypatch):
    """A flag that the page keeps after the adapter dropped it."""
    named = set(re.findall(r"^\| `(--[A-Za-z-]+)", command_section(), flags=re.MULTILINE))
    assert named, "the table of the command names no option, so this check would check nothing"
    assert sorted(named - options_of_the_longest_command(tmp_path, monkeypatch)) == []


@outside_the_package
def test_the_page_names_the_harness_version_and_the_date_of_the_recorded_streams_and_of_the_live_smoke_run():
    manifest = json.loads((claude_fake.FIXTURES / "manifest.json").read_text())
    text = REFERENCE.read_text()
    streams, smoke = text.split("## The recorded streams", 1)[1].split("## The live smoke run")
    assert f"Claude Code {manifest['harness_version']}" in streams
    assert manifest["recorded"] in streams
    assert f"Claude Code {manifest['harness_version']}" in smoke
    assert manifest["recorded"] in smoke


@outside_the_package
@pytest.mark.parametrize("fixture", sorted(path.name for path in claude_fake.FIXTURES.glob("*.jsonl")))
def test_the_page_lists_each_recorded_stream(fixture):
    assert f"`{fixture}`" in REFERENCE.read_text()


@outside_the_package
def test_the_readme_links_the_page():
    assert "docs/reference/claude-code-adapter.md" in (ROOT / "README.md").read_text()
