"""`delegate run` with the `claude-code` adapter.

Each case runs `main(argv)` against a real temporary git repository. The
`claude` process is the system boundary, so a stand-in command replays a recorded
stream. The stand-in does no work, so the step fails after the adapter result.
The cases check what the engine gave the harness.
"""

from __future__ import annotations

from importlib import resources

import pytest

from delegate.cli.run_command import EXIT_HALTED

from . import claude_fake
from .support import (
    WORKFLOW, event_names, make_repo, read_journal, read_journal_file, resume_main, run_main, state_dir, write_project_agents,
)

CLAUDE_CODE = WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"')

# The user directory holds nothing, and the repository holds the agent files that the engine seals.
pytestmark = pytest.mark.usefixtures("home")


@pytest.mark.parametrize("mode, model", [("assure", "opus"), ("economy", "sonnet")])
def test_the_model_on_the_command_comes_from_the_claude_code_tier_column_of_the_mode(tmp_path, monkeypatch, capsys, mode, model):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, CLAUDE_CODE.replace('mode = "assure"', f'mode = "{mode}"'))
    write_project_agents(repo)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    (call,) = fake.calls()
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == model
    assert argv[argv.index("--agent") + 1] == "delegate-seal-python-specialist:python-specialist"
    assert argv[argv.index("--setting-sources") + 1] == "user"
    assert argv[argv.index("--plugin-dir") + 1].endswith("/agents/python-specialist")
    assert "The export command writes a CSV file." in call["stdin"]
    assert not any("The export command" in arg for arg in argv)
    result = next(e for e in read_journal(out) if e["event"] == "adapter-result")
    assert (result["end_state"], result["session_id"]) == ("finished", "af2a3741-3f76-4944-91f5-3d5102c79b6b")


def test_a_tier_file_of_the_user_changes_the_model_on_the_command(tmp_path, monkeypatch, capsys):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    bundled = resources.files("delegate.shared").joinpath("tiers.toml").read_text()
    tiers = tmp_path / "gateway-tiers.toml"
    tiers.write_text(bundled.replace('[tiers.strong]\nclaude-code = "opus"', '[tiers.strong]\nclaude-code = "claude-opus-5"'))
    repo = make_repo(tmp_path, CLAUDE_CODE)
    write_project_agents(repo)

    run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--tiers", str(tiers))

    (call,) = fake.calls()
    assert call["argv"][call["argv"].index("--model") + 1] == "claude-opus-5"


def test_a_harness_that_cannot_start_the_agent_ends_the_run_with_its_message_and_no_traceback(tmp_path, monkeypatch, capsys):
    claude_fake.install(tmp_path, monkeypatch, "", exit_status=1, stderr="--agent 'python-specialist' not found. Available agents: Plan\n")
    repo = make_repo(tmp_path, CLAUDE_CODE)
    write_project_agents(repo)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == EXIT_HALTED
    assert "--agent 'python-specialist' not found" in err
    assert "Traceback" not in err


def test_a_usage_limit_of_the_specialist_halts_the_run_with_no_continuation_and_a_resume_goes_on_with_the_step(tmp_path, monkeypatch, capsys):
    # The recorded stream of a real session that hit the usage limit (#271). Before, the session ended `failed`, the
    # engine started a continuation that hit the same limit, and the step failed with "ended failed with exit status 1".
    fake = claude_fake.install(tmp_path, monkeypatch, "usage-limit.jsonl", exit_status=1)
    repo = make_repo(tmp_path, CLAUDE_CODE)
    write_project_agents(repo)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == EXIT_HALTED, err
    assert len(fake.calls()) == 1
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    events = read_journal_file(repo, run_dir.name)
    names = event_names(events)
    assert names[-1] == "halted"
    assert (events[-1]["ticket"], events[-1]["role"]) == ("a", "specialist")
    assert "You've hit your session limit · resets 4pm (Pacific/Auckland)" in events[-1]["message"]
    assert not {"continuation", "step-end", "run-end"} & set(names)
    assert "You've hit your session limit" in err

    # The limit is gone: the resume starts the specialist of the open step again, with no `--retry`.
    monkeypatch.setenv("FAKE_HARNESS_STREAM", str(claude_fake.FIXTURES / "finished.jsonl"))
    monkeypatch.setenv("FAKE_HARNESS_EXIT", "0")
    code, out, err = resume_main(repo, capsys, run_dir.name)

    assert len(fake.calls()) == 2
    events = read_journal_file(repo, run_dir.name)
    after = events[event_names(events).index("resume"):]
    assert [(e["ticket"], e["end_state"]) for e in after if e["event"] == "adapter-result"][:1] == [("a", "finished")]
    assert "halted" not in event_names(after)


def test_an_error_result_that_is_no_failure_of_the_harness_fails_the_step_with_the_text_of_the_result(tmp_path, monkeypatch, capsys):
    # The 404 of a model that does not exist is an error of the request, so the session ends `failed`, as before (#271).
    claude_fake.install(tmp_path, monkeypatch, "failed.jsonl", exit_status=1)
    repo = make_repo(tmp_path, CLAUDE_CODE)
    write_project_agents(repo)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1, err
    events = read_journal(out)
    assert "halted" not in event_names(events)
    step_end = next(e for e in events if e["event"] == "step-end" and e["ticket"] == "a")
    assert step_end["state"] == "failed"
    assert "the specialist session failed: There's an issue with the selected model (no-such-model-xyz)." in step_end["reason"]
