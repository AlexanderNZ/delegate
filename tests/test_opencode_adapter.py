"""The OpenCode adapter.

Each case starts the adapter through the registry, as the engine does, and
replaces the `opencode` process with a stand-in that replays a recorded stream
(see `tests/fixtures/opencode/manifest.json` for the harness version and the
date of each recording).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from delegate import adapters
from delegate.ports.harness import AdapterError, AdapterRequest

from . import opencode_fake


def request(tmp_path: Path, **changes) -> AdapterRequest:
    cwd = tmp_path / "work"
    cwd.mkdir(exist_ok=True)
    fields = dict(
        agent="python-specialist", model="anthropic/claude-opus-5", prompt="## Task\nTicket a\n", cwd=cwd,
        report_path=tmp_path / "reports" / "a.specialist.json",
    )
    fields.update(changes)
    return AdapterRequest(**fields)


def test_a_finished_stream_gives_the_end_state_finished_the_session_id_and_the_captured_stream(tmp_path, monkeypatch):
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    result = adapters.get("opencode").run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.session_id) == (0, "finished", "ses_ee2d28a59ffevBJM22I2ZGtbFd")
    assert result.event_stream.read_text() == (opencode_fake.FIXTURES / "finished.jsonl").read_text()
    (call,) = fake.calls()
    assert call["cwd"] == str((tmp_path / "work").resolve())


def test_the_command_holds_the_json_format_the_model_and_the_agent_and_the_prompt_goes_on_stdin_not_in_argv(tmp_path, monkeypatch):
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    adapters.get("opencode").run(
        request(tmp_path, model="anthropic/claude-sonnet-5", agent="python-verifier", prompt="SECRET-BRIEF-TEXT\n")
    )

    (call,) = fake.calls()
    argv = call["argv"]
    assert call["stdin"] == "SECRET-BRIEF-TEXT\n"
    assert not any("SECRET-BRIEF-TEXT" in arg for arg in argv)
    assert argv[:1] == ["run"]
    assert argv[argv.index("--format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "anthropic/claude-sonnet-5"
    assert argv[argv.index("--agent") + 1] == "python-verifier"
    assert "--session" not in argv


def test_a_stream_whose_last_step_stops_at_the_output_limit_gives_the_end_state_capped_and_keeps_the_session_id(tmp_path, monkeypatch):
    opencode_fake.install(tmp_path, monkeypatch, "capped.jsonl")

    result = adapters.get("opencode").run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.session_id) == (0, "capped", "ses_ee2d17177ffeYs27m2gtEAdMET")


def test_a_stream_with_an_error_event_gives_the_end_state_failed_although_the_exit_status_is_0(tmp_path, monkeypatch):
    opencode_fake.install(tmp_path, monkeypatch, "failed.jsonl")

    result = adapters.get("opencode").run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.session_id) == (0, "failed", "ses_ee2d2a428ffefX3Jkh7WsGRki5")


def test_a_finished_stream_with_a_non_zero_exit_status_gives_the_end_state_failed(tmp_path, monkeypatch):
    opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl", exit_status=3)

    result = adapters.get("opencode").run(request(tmp_path))

    assert (result.exit_status, result.end_state) == (3, "failed")


def test_an_error_event_after_a_finished_step_gives_the_end_state_failed(tmp_path, monkeypatch):
    finished = (opencode_fake.FIXTURES / "finished.jsonl").read_text()
    error = (opencode_fake.FIXTURES / "failed.jsonl").read_text()
    both = tmp_path / "both.jsonl"
    both.write_text(finished + error)
    opencode_fake.install(tmp_path, monkeypatch, str(both))

    result = adapters.get("opencode").run(request(tmp_path))

    assert result.end_state == "failed"


def test_a_stream_cut_off_before_its_last_step_ends_gives_the_end_state_failed_and_keeps_the_session_id(tmp_path, monkeypatch):
    first_two_events = "".join((opencode_fake.FIXTURES / "finished.jsonl").read_text().splitlines(keepends=True)[:2])
    cut = tmp_path / "cut.jsonl"
    cut.write_text(first_two_events)
    opencode_fake.install(tmp_path, monkeypatch, str(cut), exit_status=137)

    result = adapters.get("opencode").run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.session_id) == (137, "failed", "ses_ee2d28a59ffevBJM22I2ZGtbFd")


def test_a_stream_cut_in_the_middle_of_a_line_gives_the_end_state_failed(tmp_path, monkeypatch):
    first_line, second_line = (opencode_fake.FIXTURES / "finished.jsonl").read_text().splitlines()[:2]
    cut = tmp_path / "cut.jsonl"
    cut.write_text(first_line + "\n" + second_line[:40])
    opencode_fake.install(tmp_path, monkeypatch, str(cut), exit_status=137)

    result = adapters.get("opencode").run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.session_id) == (137, "failed", "ses_ee2d28a59ffevBJM22I2ZGtbFd")


def test_a_stream_that_does_not_mark_the_step_cap_gives_the_end_state_finished(tmp_path, monkeypatch):
    # Measured: the harness answers the cap with one more text-only step that ends with reason `stop`.
    # The engine then finds no valid report and decides what to do.
    opencode_fake.install(tmp_path, monkeypatch, "step-cap.jsonl")

    result = adapters.get("opencode").run(request(tmp_path))

    assert result.end_state == "finished"


def test_a_harness_that_writes_no_event_raises_with_its_own_message(tmp_path, monkeypatch):
    opencode_fake.install(
        tmp_path, monkeypatch, "", exit_status=1,
        stderr="Error: Configuration is invalid at /work/opencode.json\n  Missing key provider.gateway.models.qwen.limit.context\n",
    )

    with pytest.raises(AdapterError, match="status 1.*Configuration is invalid"):
        adapters.get("opencode").run(request(tmp_path))


def test_an_agent_that_the_harness_does_not_find_raises_because_the_harness_runs_its_default_agent_instead(tmp_path, monkeypatch):
    # Measured: the harness writes this warning, runs its default agent, and exits 0.
    opencode_fake.install(
        tmp_path, monkeypatch, "unknown-agent.jsonl",
        stderr='\x1b[93m\x1b[1m! \x1b[0magent "no-such-agent" not found. Falling back to default agent\n',
    )

    with pytest.raises(AdapterError, match="no-such-agent.*not found"):
        adapters.get("opencode").run(request(tmp_path, agent="no-such-agent"))


def test_a_continuation_resumes_the_session_of_the_last_run(tmp_path, monkeypatch):
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    adapter = adapters.get("opencode")

    result = adapter.run(request(tmp_path, resume_session="ses_ee2d28a59ffevBJM22I2ZGtbFd"))

    assert adapter.supports_resume is True
    (call,) = fake.calls()
    argv = call["argv"]
    assert argv[argv.index("--session") + 1] == "ses_ee2d28a59ffevBJM22I2ZGtbFd"
    assert result.end_state == "finished"


def test_a_second_run_with_the_same_report_path_keeps_the_stream_of_the_first(tmp_path, monkeypatch):
    opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    adapter = adapters.get("opencode")

    first = adapter.run(request(tmp_path))
    second = adapter.run(request(tmp_path))

    assert first.event_stream != second.event_stream
    assert first.event_stream.read_text() == second.event_stream.read_text() != ""


def test_a_line_that_is_not_json_before_the_end_of_the_stream_raises_and_names_the_line(tmp_path, monkeypatch):
    lines = (opencode_fake.FIXTURES / "finished.jsonl").read_text().splitlines()
    broken = tmp_path / "broken.jsonl"
    broken.write_text("\n".join([lines[0], "this is not json", *lines[1:]]) + "\n")
    opencode_fake.install(tmp_path, monkeypatch, str(broken))

    with pytest.raises(AdapterError, match="line 2 is not JSON"):
        adapters.get("opencode").run(request(tmp_path))


def test_a_step_finish_event_with_no_reason_raises_and_names_the_stream(tmp_path, monkeypatch):
    lines = (opencode_fake.FIXTURES / "finished.jsonl").read_text().splitlines()
    odd = tmp_path / "odd.jsonl"
    odd.write_text(lines[0] + "\n" + '{"type":"step_finish","sessionID":"ses_x","part":{"type":"step-finish"}}\n')
    opencode_fake.install(tmp_path, monkeypatch, str(odd))

    with pytest.raises(AdapterError, match="step_finish.*reason"):
        adapters.get("opencode").run(request(tmp_path))


def test_each_recorded_stream_is_in_the_manifest_with_the_harness_version_and_the_date_and_gives_the_outcome_that_the_manifest_states(tmp_path, monkeypatch):
    manifest = json.loads((opencode_fake.FIXTURES / "manifest.json").read_text())
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["harness_version"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", manifest["recorded"])
    assert set(manifest["fixtures"]) == {path.name for path in opencode_fake.FIXTURES.glob("*.jsonl")}
    for name, facts in manifest["fixtures"].items():
        scenario = tmp_path / name
        scenario.mkdir()
        opencode_fake.install(scenario, monkeypatch, name, exit_status=facts["exit_status"], stderr=facts.get("stderr", ""))
        if facts["end_state"] is None:
            with pytest.raises(AdapterError):
                adapters.get("opencode").run(request(scenario, agent=facts["agent"]))
        else:
            assert adapters.get("opencode").run(request(scenario)).end_state == facts["end_state"]


def test_the_command_names_the_working_directory_because_the_harness_takes_its_project_from_the_inherited_PWD_variable(tmp_path, monkeypatch):
    # Measured: with the working directory set by the caller and PWD left over from the caller's shell,
    # the harness searched the agents of the caller's directory and ran its tools there.
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    monkeypatch.setenv("PWD", str(tmp_path))
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    adapters.get("opencode").run(request(tmp_path, cwd=worktree))

    (call,) = fake.calls()
    argv = call["argv"]
    assert argv[argv.index("--dir") + 1] == str(worktree)
    assert call["cwd"] == str(worktree.resolve())
