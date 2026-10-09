"""`delegate run` with the `opencode` adapter.

Each case runs `main(argv)` against a real temporary git repository. The
`opencode` process is the system boundary, so a stand-in command replays a
recorded stream. The stand-in does no work, so the step fails after the adapter
result. The cases check what the engine gave the harness.
"""

from __future__ import annotations

from importlib import resources

import pytest

from . import opencode_fake
from .support import WORKFLOW, make_repo, read_journal, run_main

OPENCODE = WORKFLOW.replace('adapter = "scripted"', 'adapter = "opencode"')


def model_of(call: dict[str, object]) -> str:
    argv = call["argv"]
    return argv[argv.index("--model") + 1]


@pytest.mark.parametrize("mode, model", [("assure", "anthropic/claude-opus-5"), ("economy", "anthropic/claude-sonnet-5")])
def test_the_model_on_the_command_comes_from_the_opencode_tier_column_of_the_mode(tmp_path, monkeypatch, capsys, mode, model):
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, OPENCODE.replace('mode = "assure"', f'mode = "{mode}"'))

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    (call,) = fake.calls()
    argv = call["argv"]
    assert model_of(call) == model
    assert argv[argv.index("--agent") + 1] == "python-specialist"
    assert "The export command writes a CSV file." in call["stdin"]
    assert not any("The export command" in arg for arg in argv)
    result = next(e for e in read_journal(out) if e["event"] == "adapter-result")
    assert (result["end_state"], result["session_id"]) == ("finished", "ses_ee2d28a59ffevBJM22I2ZGtbFd")


def test_a_tier_file_of_the_user_changes_the_model_on_the_command(tmp_path, monkeypatch, capsys):
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    bundled = resources.files("delegate").joinpath("tiers.toml").read_text()
    tiers = tmp_path / "gateway-tiers.toml"
    tiers.write_text(bundled.replace('opencode = "anthropic/claude-opus-5"\n\n[tiers.standard]', 'opencode = "gateway/claude-opus-5"\n\n[tiers.standard]'))
    repo = make_repo(tmp_path, OPENCODE)

    run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--tiers", str(tiers))

    (call,) = fake.calls()
    assert model_of(call) == "gateway/claude-opus-5"


def test_the_opencode_override_flags_change_the_model_on_the_command(tmp_path, monkeypatch, capsys):
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, OPENCODE)

    run_main(
        capsys, str(repo / "workflow.toml"), "--repo", str(repo),
        "--opencode-model", "strong=gateway/claude-opus-5", "--opencode-allow", "gateway/claude-opus-5",
    )

    (call,) = fake.calls()
    assert model_of(call) == "gateway/claude-opus-5"


def test_an_override_model_that_the_allow_flag_does_not_name_exits_1_names_the_model_and_starts_nothing(tmp_path, monkeypatch, capsys):
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, OPENCODE)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--opencode-model", "strong=gateway/claude-opus-5")

    assert (code, out) == (1, "")
    assert "gateway/claude-opus-5" in err
    assert "Traceback" not in err
    assert fake.calls() == []


def test_an_override_for_a_tier_that_does_not_exist_exits_1_and_names_the_tier(tmp_path, monkeypatch, capsys):
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, OPENCODE)

    code, out, err = run_main(
        capsys, str(repo / "workflow.toml"), "--repo", str(repo),
        "--opencode-model", "mighty=gateway/claude-opus-5", "--opencode-allow", "gateway/claude-opus-5",
    )

    assert (code, out) == (1, "")
    assert "mighty" in err
    assert "Traceback" not in err
    assert fake.calls() == []


def test_a_harness_that_does_not_find_the_agent_ends_the_run_with_its_warning_and_no_traceback(tmp_path, monkeypatch, capsys):
    opencode_fake.install(
        tmp_path, monkeypatch, "unknown-agent.jsonl",
        stderr='! agent "python-specialist" not found. Falling back to default agent\n',
    )
    repo = make_repo(tmp_path, OPENCODE)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1
    assert 'agent "python-specialist" not found' in err
    assert "Traceback" not in err
