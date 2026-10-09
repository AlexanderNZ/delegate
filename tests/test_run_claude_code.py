"""`delegate run` with the `claude-code` adapter.

Each case runs `main(argv)` against a real temporary git repository. The
`claude` process is the system boundary, so a stand-in command replays a recorded
stream. The stand-in does no work, so the step fails after the adapter result.
The cases check what the engine gave the harness.
"""

from __future__ import annotations

from importlib import resources

import pytest

from . import claude_fake
from .support import WORKFLOW, make_repo, read_journal, run_main

CLAUDE_CODE = WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"')


@pytest.mark.parametrize("mode, model", [("assure", "opus"), ("economy", "sonnet")])
def test_the_model_on_the_command_comes_from_the_claude_code_tier_column_of_the_mode(tmp_path, monkeypatch, capsys, mode, model):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, CLAUDE_CODE.replace('mode = "assure"', f'mode = "{mode}"'))

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    (call,) = fake.calls()
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == model
    assert argv[argv.index("--agent") + 1] == "python-specialist"
    assert "The export command writes a CSV file." in call["stdin"]
    assert not any("The export command" in arg for arg in argv)
    result = next(e for e in read_journal(out) if e["event"] == "adapter-result")
    assert (result["end_state"], result["session_id"]) == ("finished", "af2a3741-3f76-4944-91f5-3d5102c79b6b")


def test_a_tier_file_of_the_user_changes_the_model_on_the_command(tmp_path, monkeypatch, capsys):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    bundled = resources.files("delegate").joinpath("tiers.toml").read_text()
    tiers = tmp_path / "gateway-tiers.toml"
    tiers.write_text(bundled.replace('[tiers.strong]\nclaude-code = "opus"', '[tiers.strong]\nclaude-code = "claude-opus-5"'))
    repo = make_repo(tmp_path, CLAUDE_CODE)

    run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--tiers", str(tiers))

    (call,) = fake.calls()
    assert call["argv"][call["argv"].index("--model") + 1] == "claude-opus-5"


def test_a_harness_that_cannot_start_the_agent_ends_the_run_with_its_message_and_no_traceback(tmp_path, monkeypatch, capsys):
    claude_fake.install(tmp_path, monkeypatch, "", exit_status=1, stderr="--agent 'python-specialist' not found. Available agents: Plan\n")
    repo = make_repo(tmp_path, CLAUDE_CODE)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1
    assert "--agent 'python-specialist' not found" in err
    assert "Traceback" not in err
