"""The OpenCode adapter.

Each case starts the adapter through the registry, as the engine does, and
replaces the `opencode` process with a stand-in that replays a recorded stream
(see `tests/fixtures/opencode/manifest.json` for the harness version and the
date of each recording).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from delegate import adapters
from delegate.ports.harness import Activity, AdapterError, AdapterRequest

from . import opencode_fake
from .harness_fake import wrote


def request(tmp_path: Path, **changes) -> AdapterRequest:
    cwd = tmp_path / "work"
    cwd.mkdir(exist_ok=True)
    (tmp_path / "streams").mkdir(exist_ok=True)  # the engine names a stream in a directory that exists
    fields = dict(
        agent="python-specialist", model="anthropic/claude-opus-5", prompt="## Task\nTicket a\n", cwd=cwd,
        report_path=tmp_path / "reports" / "a.specialist.json", event_stream=tmp_path / "streams" / "a.stream.jsonl",
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


def test_a_finished_stream_with_the_exit_status_of_sigterm_keeps_the_end_state_finished_and_the_exit_status(tmp_path, monkeypatch):
    opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl", exit_status=143)

    result = adapters.get("opencode").run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.ended_idle) == (143, "finished", False)


def test_after_a_valid_report_a_session_that_writes_no_event_for_the_idle_timeout_ends(tmp_path, monkeypatch):
    # The session starts later than the idle timeout, as on a machine with a high load, and stays open after its result.
    opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl", start_seconds=1.0, hold_seconds=120)
    started = time.monotonic()
    stream = tmp_path / "streams" / "a.stream.jsonl"
    req = request(tmp_path, idle_timeout=0.5, event_stream=stream, report_valid=wrote(stream, (opencode_fake.FIXTURES / "finished.jsonl").read_text()))

    result = adapters.get("opencode").run(req)

    assert time.monotonic() - started < 60
    assert (result.end_state, result.ended_idle, result.session_id) == ("finished", True, "ses_ee2d28a59ffevBJM22I2ZGtbFd")
    assert result.exit_status != 0


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


def test_the_adapter_writes_the_stream_to_the_path_that_the_request_names(tmp_path, monkeypatch):
    opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    stream = tmp_path / "chosen" / "x.jsonl"
    stream.parent.mkdir()

    result = adapters.get("opencode").run(request(tmp_path, event_stream=stream))

    assert result.event_stream == stream
    assert stream.read_text() == (opencode_fake.FIXTURES / "finished.jsonl").read_text()


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


def fixture_lines(name: str) -> list[str]:
    return (opencode_fake.FIXTURES / name).read_text().splitlines(keepends=True)


def test_the_activity_of_a_finished_stream_gives_the_tokens_of_its_step_and_the_time_of_the_last_event():
    activity = adapters.get("opencode").activity(opencode_fake.FIXTURES / "finished.jsonl")

    # The timestamp is in epoch milliseconds: 1791490898675.
    assert activity == Activity(84197, datetime(2026, 10, 8, 20, 21, 38, 675000, tzinfo=timezone.utc))


def test_the_activity_of_a_stream_with_cache_reads_gives_the_total_that_the_step_reports():
    # total 85755 = input 22342 + output 10 + reasoning 43 + cache read 63360
    assert adapters.get("opencode").activity(opencode_fake.FIXTURES / "unknown-agent.jsonl").tokens == 85755


def test_the_activity_of_a_live_stream_that_ends_in_half_a_line_adds_the_steps_that_finished_and_reads_with_no_error(tmp_path):
    first, second = fixture_lines("finished.jsonl"), fixture_lines("step-cap.jsonl")
    live = tmp_path / "live.jsonl"
    live.write_text("".join(first) + "".join(second[:2]) + second[2][:60])

    activity = adapters.get("opencode").activity(live)

    # The step of the first part finished (84197 tokens). The second step has no complete step_finish yet.
    assert activity == Activity(84197, datetime(2026, 10, 8, 20, 22, 4, 475000, tzinfo=timezone.utc))


def test_the_activity_of_two_finished_steps_adds_the_tokens_of_both(tmp_path):
    both = tmp_path / "both.jsonl"
    both.write_text("".join(fixture_lines("finished.jsonl") + fixture_lines("step-cap.jsonl")))

    assert adapters.get("opencode").activity(both).tokens == 84197 + 84637


def test_the_activity_of_an_empty_stream_has_no_tokens_and_no_time(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")

    assert adapters.get("opencode").activity(empty) == Activity(0, None)


def test_the_activity_of_a_stream_with_a_bad_line_before_its_end_raises_and_names_the_line(tmp_path):
    lines = fixture_lines("finished.jsonl")
    broken = tmp_path / "broken.jsonl"
    broken.write_text(lines[0] + "this is not json\n" + "".join(lines[1:]))

    with pytest.raises(AdapterError, match="line 2 is not JSON"):
        adapters.get("opencode").activity(broken)


def test_the_activity_of_a_stream_that_is_not_there_raises_and_names_the_path(tmp_path):
    with pytest.raises(AdapterError, match="no-such.jsonl"):
        adapters.get("opencode").activity(tmp_path / "no-such.jsonl")


@pytest.mark.parametrize("variable", ["DELEGATE_AGENT_SETTINGS", "DELEGATE_PERMISSION_MODE"])
def test_a_host_setting_of_the_claude_code_adapter_gives_a_warning_and_changes_no_command(tmp_path, monkeypatch, capsys, variable):
    # OpenCode has no option that takes a settings file or a permission mode, so the run says that the value has no effect.
    monkeypatch.setenv(variable, str(tmp_path / "x"))
    fake = opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    adapters.get("opencode").run(request(tmp_path))

    (call,) = fake.calls()
    assert not any(arg.startswith("--settings") or arg.startswith("--permission") for arg in call["argv"])
    err = capsys.readouterr().err
    assert variable in err and "no effect" in err and "OPENCODE_CONFIG" in err


def test_the_last_text_part_of_the_session_is_the_final_text_of_the_result(tmp_path, monkeypatch):
    opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    texts = [event["part"]["text"] for event in map(json.loads, (opencode_fake.FIXTURES / "finished.jsonl").read_text().splitlines()) if event.get("type") == "text"]

    result = adapters.get("opencode").run(request(tmp_path))

    assert texts and result.final_text == texts[-1]
