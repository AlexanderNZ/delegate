"""The Claude Code adapter.

Each case starts the adapter through the registry, as the engine does, and
replaces the `claude` process with a stand-in that replays a recorded stream
(see `tests/fixtures/claude-code/manifest.json` for the harness version and the
date of each recording).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from delegate import adapters
from delegate.ports.harness import Activity, AdapterError, AdapterRequest

from . import claude_fake, harness_fake
from .harness_fake import wrote


def request(tmp_path: Path, **changes) -> AdapterRequest:
    cwd = tmp_path / "work"
    cwd.mkdir(exist_ok=True)
    (tmp_path / "streams").mkdir(exist_ok=True)  # the engine names a stream in a directory that exists
    fields = dict(
        agent="python-specialist", model="opus", prompt="## Task\nTicket a\n", cwd=cwd,
        report_path=tmp_path / "reports" / "a.specialist.json", event_stream=tmp_path / "streams" / "a.stream.jsonl",
    )
    fields.update(changes)
    return AdapterRequest(**fields)


def test_a_finished_stream_gives_the_end_state_finished_the_session_id_and_the_captured_stream(tmp_path, monkeypatch):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    result = adapters.get("claude-code").run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.session_id) == (0, "finished", "af2a3741-3f76-4944-91f5-3d5102c79b6b")
    assert result.event_stream.read_text() == (claude_fake.FIXTURES / "finished.jsonl").read_text()
    (call,) = fake.calls()
    assert call["cwd"] == str((tmp_path / "work").resolve())


def test_the_command_holds_the_model_and_the_agent_and_the_prompt_goes_on_stdin_not_in_argv(tmp_path, monkeypatch):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    adapters.get("claude-code").run(request(tmp_path, model="sonnet", agent="python-verifier", prompt="SECRET-BRIEF-TEXT\n"))

    (call,) = fake.calls()
    argv = call["argv"]
    assert call["stdin"] == "SECRET-BRIEF-TEXT\n"
    assert not any("SECRET-BRIEF-TEXT" in arg for arg in argv)
    assert argv[:1] == ["--print"]
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--agent") + 1] == "python-verifier"
    assert "--resume" not in argv


def test_a_stream_that_ends_at_the_turn_cap_gives_the_end_state_capped_and_keeps_the_session_id(tmp_path, monkeypatch):
    claude_fake.install(tmp_path, monkeypatch, "capped.jsonl", exit_status=1)

    result = adapters.get("claude-code").run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.session_id) == (1, "capped", "4dc2a147-0a8b-4903-b312-2dfce923382e")


def test_a_stream_whose_result_is_an_api_error_gives_the_end_state_failed(tmp_path, monkeypatch):
    claude_fake.install(tmp_path, monkeypatch, "failed.jsonl", exit_status=1)

    result = adapters.get("claude-code").run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.session_id) == (1, "failed", "2c4ee840-1107-4928-874f-fde608ad16bb")


def test_a_finished_result_with_the_exit_status_of_sigterm_keeps_the_end_state_finished_and_the_exit_status(tmp_path, monkeypatch):
    # A coordinator stopped an idle session with SIGTERM after its last `result` event, a success.
    # The end state comes from the stream; the engine weighs the exit status itself.
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl", exit_status=143)

    result = adapters.get("claude-code").run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.ended_idle) == (143, "finished", False)
    assert result.session_id == "af2a3741-3f76-4944-91f5-3d5102c79b6b"


def test_after_a_valid_report_a_session_that_writes_no_event_for_the_idle_timeout_ends_and_keeps_its_stream(tmp_path, monkeypatch):
    # The session starts later than the idle timeout, as on a machine with a high load, and stays open after its result.
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl", start_seconds=1.0, hold_seconds=120)
    started = time.monotonic()
    stream = tmp_path / "streams" / "a.stream.jsonl"
    req = request(tmp_path, idle_timeout=0.5, event_stream=stream, report_valid=wrote(stream, (claude_fake.FIXTURES / "finished.jsonl").read_text()))

    result = adapters.get("claude-code").run(req)

    assert time.monotonic() - started < 60
    assert (result.end_state, result.ended_idle) == ("finished", True)
    assert result.exit_status != 0
    assert result.session_id == "af2a3741-3f76-4944-91f5-3d5102c79b6b"
    assert result.event_stream.read_text() == (claude_fake.FIXTURES / "finished.jsonl").read_text()


def test_a_session_whose_report_is_not_valid_is_not_ended_at_the_idle_timeout(tmp_path, monkeypatch):
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl", hold_seconds=1.5)
    started = time.monotonic()

    result = adapters.get("claude-code").run(request(tmp_path, idle_timeout=0.3, report_valid=lambda: False))

    assert time.monotonic() - started >= 1.5
    assert (result.exit_status, result.end_state, result.ended_idle) == (0, "finished", False)


def test_a_session_that_writes_events_more_often_than_the_idle_timeout_is_not_ended(tmp_path, monkeypatch):
    # Seven events, one each 0.8 seconds. From the first event to the last is 4.8 seconds, longer than the idle
    # timeout, so only the events keep the session open. Each gap is 2.2 seconds shorter than the idle timeout,
    # so a fake that a high load slows down still writes in time.
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl", drip_seconds=0.8)

    first_event = (claude_fake.FIXTURES / "finished.jsonl").read_text().splitlines(keepends=True)[0]
    stream = tmp_path / "streams" / "a.stream.jsonl"
    req = request(tmp_path, idle_timeout=3.0, event_stream=stream, report_valid=wrote(stream, first_event))  # valid from the first event on

    result = adapters.get("claude-code").run(req)

    assert (result.exit_status, result.end_state, result.ended_idle) == (0, "finished", False)
    assert result.event_stream.read_text() == (claude_fake.FIXTURES / "finished.jsonl").read_text()


def test_a_stream_cut_off_before_its_result_gives_the_end_state_failed_and_no_session_id(tmp_path, monkeypatch):
    first_two_events = "".join((claude_fake.FIXTURES / "finished.jsonl").read_text().splitlines(keepends=True)[:2])
    cut = tmp_path / "cut.jsonl"
    cut.write_text(first_two_events)
    claude_fake.install(tmp_path, monkeypatch, str(cut), exit_status=137)

    result = adapters.get("claude-code").run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.session_id) == (137, "failed", None)


def test_a_harness_that_starts_no_session_raises_with_its_own_message(tmp_path, monkeypatch):
    claude_fake.install(tmp_path, monkeypatch, "", exit_status=1, stderr="--agent 'no-such-agent' not found. Available agents: python-specialist\n")

    with pytest.raises(AdapterError, match="no-such-agent.*not found"):
        adapters.get("claude-code").run(request(tmp_path, agent="no-such-agent"))


def test_a_continuation_resumes_the_session_of_the_last_run(tmp_path, monkeypatch):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    adapter = adapters.get("claude-code")

    result = adapter.run(request(tmp_path, resume_session="af2a3741-3f76-4944-91f5-3d5102c79b6b"))

    assert adapter.supports_resume is True
    (call,) = fake.calls()
    argv = call["argv"]
    assert argv[argv.index("--resume") + 1] == "af2a3741-3f76-4944-91f5-3d5102c79b6b"
    assert result.end_state == "finished"


@pytest.mark.parametrize("agent, resume_session", [
    ("python-specialist", None),
    ("python-verifier", None),
    ("python-specialist", "af2a3741-3f76-4944-91f5-3d5102c79b6b"),
])
def test_every_run_disallows_the_monitor_and_the_background_tasks(tmp_path, monkeypatch, agent, resume_session):
    # Under `claude --print` the notification of a background task or a monitor never reaches the agent.
    # An agent that waits for one polls until its turns end, and an expired monitor keeps the session alive.
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", raising=False)
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    adapters.get("claude-code").run(request(tmp_path, agent=agent, resume_session=resume_session))

    (call,) = fake.calls()
    argv = call["argv"]
    assert argv.count("--disallowedTools") == 1
    disallowed = set(re.split(r"[,\s]+", argv[argv.index("--disallowedTools") + 1]))
    assert {"Monitor", "TaskStop"} <= disallowed
    # The `run_in_background` parameter of the Bash and subagent tools is no tool name, so only this variable removes it.
    assert call["env"].get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") == "1"
    assert argv[argv.index("--agent") + 1] == agent


def test_the_adapter_writes_the_stream_to_the_path_that_the_request_names(tmp_path, monkeypatch):
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    stream = tmp_path / "chosen" / "x.jsonl"
    stream.parent.mkdir()

    result = adapters.get("claude-code").run(request(tmp_path, event_stream=stream))

    assert result.event_stream == stream
    assert stream.read_text() == (claude_fake.FIXTURES / "finished.jsonl").read_text()


def test_a_stream_cut_in_the_middle_of_a_line_gives_the_end_state_failed(tmp_path, monkeypatch):
    first_line, second_line = (claude_fake.FIXTURES / "finished.jsonl").read_text().splitlines()[:2]
    cut = tmp_path / "cut.jsonl"
    cut.write_text(first_line + "\n" + second_line[:40])
    claude_fake.install(tmp_path, monkeypatch, str(cut), exit_status=137)

    result = adapters.get("claude-code").run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.session_id) == (137, "failed", None)


def test_a_line_that_is_not_json_before_the_end_of_the_stream_raises_and_names_the_line(tmp_path, monkeypatch):
    lines = (claude_fake.FIXTURES / "finished.jsonl").read_text().splitlines()
    broken = tmp_path / "broken.jsonl"
    broken.write_text("\n".join([lines[0], "this is not json", *lines[1:]]) + "\n")
    claude_fake.install(tmp_path, monkeypatch, str(broken))

    with pytest.raises(AdapterError, match="line 2 is not JSON"):
        adapters.get("claude-code").run(request(tmp_path))


def test_each_recorded_stream_names_the_harness_version_and_the_date_and_gives_the_end_state_that_the_manifest_states(tmp_path, monkeypatch):
    manifest = json.loads((claude_fake.FIXTURES / "manifest.json").read_text())
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", manifest["recorded"])
    assert set(manifest["fixtures"]) == {path.name for path in claude_fake.FIXTURES.glob("*.jsonl")}
    for name, facts in manifest["fixtures"].items():
        init = json.loads((claude_fake.FIXTURES / name).read_text().splitlines()[0])
        assert init["claude_code_version"] == manifest["harness_version"]
        scenario = tmp_path / name
        scenario.mkdir()
        claude_fake.install(scenario, monkeypatch, name, exit_status=facts["exit_status"])
        result = adapters.get("claude-code").run(request(scenario))
        assert result.end_state == facts["end_state"]


# The recorded events of work that have no time: tool heartbeats and the token counts of a thought.
PROGRESS: Path = claude_fake.FIXTURES.parent / "claude-code-progress"

# A time after the newest timed event of each recorded stream, for the time that the harness last wrote to a stream.
CHANGED: datetime = datetime(2026, 10, 8, 19, 58, 30, tzinfo=timezone.utc)


def written_at(stream: Path, when: datetime) -> None:
    os.utime(stream, (when.timestamp(), when.timestamp()))


def capped_lines() -> list[str]:
    return (claude_fake.FIXTURES / "capped.jsonl").read_text().splitlines(keepends=True)


def test_the_activity_of_a_finished_stream_gives_the_tokens_of_its_result_event_and_the_time_of_the_last_event():
    activity = adapters.get("claude-code").activity(claude_fake.FIXTURES / "finished.jsonl")

    # The `result` event: input 10 + cache creation 19176 + cache read 0 + output 140. The assistant events of the
    # message give only a partial output count (6), so their sum (19192) is not the count.
    assert activity == Activity(19326, datetime(2026, 10, 8, 19, 55, 11, 654000, tzinfo=timezone.utc))


def test_the_activity_of_a_live_stream_that_ends_in_half_a_line_reads_the_complete_lines_with_no_error(tmp_path):
    lines = capped_lines()
    live = tmp_path / "live.jsonl"
    live.write_text("".join(lines[:7]) + lines[7][:120])

    activity = adapters.get("claude-code").activity(live)

    # One message (input 10 + cache creation 11808 + cache read 13970 + output 6), and the time of the seventh line.
    assert activity == Activity(25794, datetime(2026, 10, 8, 19, 55, 53, 245000, tzinfo=timezone.utc))


def test_the_activity_of_a_stream_with_only_its_init_event_has_no_tokens_and_no_time(tmp_path):
    live = tmp_path / "live.jsonl"
    live.write_text(capped_lines()[0])
    written_at(live, CHANGED)

    assert adapters.get("claude-code").activity(live) == Activity(0, None)


@pytest.mark.parametrize("excerpt", [pytest.param("heartbeats.jsonl", id="tool-heartbeats"), pytest.param("thinking.jsonl", id="thinking-tokens")])
def test_the_activity_of_a_live_stream_that_ends_with_events_of_work_with_no_time_has_the_time_that_the_stream_changed(tmp_path, excerpt):
    live = tmp_path / "live.jsonl"
    live.write_text("".join(capped_lines()[:7]) + (PROGRESS / excerpt).read_text())
    written_at(live, CHANGED)

    # The seventh line, at 19:55:53.245, is the newest event with a time. The events after it have none, so the time
    # is the time that the harness last wrote to the stream.
    assert adapters.get("claude-code").activity(live) == Activity(25794, CHANGED)


def test_the_activity_of_a_stream_whose_newest_event_has_a_time_after_heartbeats_has_the_time_of_that_event(tmp_path):
    lines = capped_lines()
    live = tmp_path / "live.jsonl"
    live.write_text("".join(lines[:7]) + (PROGRESS / "heartbeats.jsonl").read_text() + lines[7])
    written_at(live, CHANGED)

    # The eighth line (the result of the tool) has the time 19:55:53.255.
    assert adapters.get("claude-code").activity(live) == Activity(25794, datetime(2026, 10, 8, 19, 55, 53, 255000, tzinfo=timezone.utc))


@pytest.mark.parametrize("fixture", ["finished.jsonl", "capped.jsonl"])
def test_the_activity_of_a_stream_that_ends_with_its_result_has_the_time_of_the_last_timed_event_not_the_time_of_the_file(tmp_path, fixture):
    stream = tmp_path / fixture
    stream.write_text((claude_fake.FIXTURES / fixture).read_text())
    written_at(stream, CHANGED)

    # The `rate_limit_event` and `result` events at the end have no time, and they are not work that goes on.
    expected = {"finished.jsonl": datetime(2026, 10, 8, 19, 55, 11, 654000, tzinfo=timezone.utc),
                "capped.jsonl": datetime(2026, 10, 8, 19, 55, 53, 255000, tzinfo=timezone.utc)}
    assert adapters.get("claude-code").activity(stream).last_event == expected[fixture]


def test_the_activity_of_a_resumed_session_adds_the_result_event_of_each_part(tmp_path):
    finished, capped = (claude_fake.FIXTURES / "finished.jsonl").read_text(), "".join(capped_lines())
    both = tmp_path / "both.jsonl"
    both.write_text(finished + capped)

    # The result of the first part (19326), and the result of the second part: 10 + 11808 + 13970 + 234 = 26022.
    assert adapters.get("claude-code").activity(both).tokens == 19326 + 26022


def test_the_activity_of_a_resumed_session_that_runs_now_adds_the_turns_after_the_last_result_event(tmp_path):
    lines = capped_lines()
    live = tmp_path / "live.jsonl"
    live.write_text((claude_fake.FIXTURES / "finished.jsonl").read_text() + "".join(lines[:7]) + lines[7][:120])

    # The result of the first part (19326), and one turn of the part that has no result yet (25794).
    assert adapters.get("claude-code").activity(live).tokens == 19326 + 25794


def test_the_activity_of_a_stream_with_a_bad_line_before_its_end_raises_and_names_the_line(tmp_path):
    lines = capped_lines()
    broken = tmp_path / "broken.jsonl"
    broken.write_text(lines[0] + "this is not json\n" + "".join(lines[1:]))

    with pytest.raises(AdapterError, match="line 2 is not JSON"):
        adapters.get("claude-code").activity(broken)


def test_the_activity_of_a_stream_that_is_not_there_raises_and_names_the_path(tmp_path):
    with pytest.raises(AdapterError, match="no-such.jsonl"):
        adapters.get("claude-code").activity(tmp_path / "no-such.jsonl")


# The settings of the agents of a host (#96). The agent host has `defaultMode` "dontAsk" with an empty allow list in
# its user settings, so an agent that gets only those settings can run no tool that is not read-only.
HOST_SETTINGS: str = "DELEGATE_AGENT_SETTINGS"
HOST_MODE: str = "DELEGATE_PERMISSION_MODE"


@pytest.mark.parametrize("agent", ["python-specialist", "python-verifier"])
def test_the_settings_file_and_the_permission_mode_of_the_host_reach_each_agent(tmp_path, monkeypatch, agent):
    settings = tmp_path / "agent-settings.json"
    settings.write_text('{"permissions": {"allow": ["Bash"]}}\n')
    monkeypatch.setenv(HOST_SETTINGS, str(settings))
    monkeypatch.setenv(HOST_MODE, "acceptEdits")
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    adapters.get("claude-code").run(request(tmp_path, agent=agent))

    (call,) = fake.calls()
    argv = call["argv"]
    assert argv.count("--settings") == 1
    assert argv[argv.index("--settings") + 1] == str(settings)
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[argv.index("--agent") + 1] == agent


def test_with_no_setting_of_the_host_the_command_has_no_settings_file_and_no_permission_mode(tmp_path, monkeypatch):
    monkeypatch.delenv(HOST_SETTINGS, raising=False)
    monkeypatch.delenv(HOST_MODE, raising=False)
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    adapters.get("claude-code").run(request(tmp_path))

    (call,) = fake.calls()
    assert "--settings" not in call["argv"]
    assert "--permission-mode" not in call["argv"]


def test_a_settings_file_of_the_host_that_does_not_exist_stops_the_adapter_and_starts_no_agent(tmp_path, monkeypatch):
    missing = tmp_path / "no-such-settings.json"
    monkeypatch.setenv(HOST_SETTINGS, str(missing))
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    with pytest.raises(AdapterError, match=f"{HOST_SETTINGS}.*{re.escape(str(missing))}"):
        adapters.get("claude-code").run(request(tmp_path))

    assert fake.calls() == []


def test_a_tool_that_a_permission_mode_refused_is_in_the_result(tmp_path, monkeypatch):
    # The text of the refusal is the text that Claude Code wrote on the agent host on 2026-10-18 (#96).
    lines = (claude_fake.FIXTURES / "finished.jsonl").read_text().splitlines()
    refusal = {
        "type": "user",
        "message": {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "toolu_1", "is_error": True,
            "content": "Permission to use Bash has been denied because Claude Code is running in don't ask mode.",
        }]},
    }
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "refused.jsonl").write_text("\n".join([*lines[:-1], json.dumps(refusal), lines[-1]]) + "\n")
    harness_fake.install(tmp_path, monkeypatch, "claude", fixtures, "refused.jsonl")

    result = adapters.get("claude-code").run(request(tmp_path))

    assert result.refused == (("Bash", "don't ask"),)


def test_a_stream_with_no_refusal_gives_no_refused_tool(tmp_path, monkeypatch):
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    result = adapters.get("claude-code").run(request(tmp_path))

    assert result.refused == ()


def test_the_final_text_of_the_session_is_in_the_result(tmp_path, monkeypatch):
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    result = adapters.get("claude-code").run(request(tmp_path))

    assert result.final_text == "SMOKE-AGENT-ACTIVE\n\nI'm ready to help with your project at /work."


def _stream_with(tmp_path: Path, monkeypatch, block: dict[str, object]) -> None:
    """Install a `claude` that replays the finished stream with one user event that holds `block` before its result."""
    lines = (claude_fake.FIXTURES / "finished.jsonl").read_text().splitlines()
    event = {"type": "user", "message": {"role": "user", "content": [block]}}
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "stream.jsonl").write_text("\n".join([*lines[:-1], json.dumps(event), lines[-1]]) + "\n")
    harness_fake.install(tmp_path, monkeypatch, "claude", fixtures, "stream.jsonl")


def test_a_tool_result_that_only_quotes_the_refusal_text_is_no_refusal(tmp_path, monkeypatch):
    # A Read or a grep of a file that holds the text, such as the reference page of this adapter, gives a result
    # that is no error. It must not count as a refused tool.
    _stream_with(tmp_path, monkeypatch, {
        "type": "tool_result", "tool_use_id": "toolu_2", "is_error": False,
        "content": "49\tthe tool result Permission to use Bash has been denied because Claude Code is running in don't ask mode.",
    })

    result = adapters.get("claude-code").run(request(tmp_path))

    assert result.refused == ()


def test_a_refusal_whose_content_is_a_list_of_text_parts_is_in_the_result(tmp_path, monkeypatch):
    _stream_with(tmp_path, monkeypatch, {
        "type": "tool_result", "tool_use_id": "toolu_3", "is_error": True,
        "content": [{"type": "text", "text": "Permission to use Write has been denied because Claude Code is running in don't ask mode."}],
    })

    result = adapters.get("claude-code").run(request(tmp_path))

    assert result.refused == (("Write", "don't ask"),)


def test_a_settings_file_of_the_host_that_is_a_relative_path_stops_the_adapter(tmp_path, monkeypatch):
    # The agent runs in its worktree, not in the directory of the engine, so a relative path can name another file.
    (tmp_path / "agent-settings.json").write_text("{}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(HOST_SETTINGS, "agent-settings.json")
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")

    with pytest.raises(AdapterError, match=f"{HOST_SETTINGS}.*absolute"):
        adapters.get("claude-code").run(request(tmp_path))

    assert fake.calls() == []


def test_the_refusal_block_that_claude_code_wrote_on_the_agent_host_is_found(tmp_path, monkeypatch):
    # The block as Claude Code 2.1.283 wrote it in the stream of a verifier on the agent host on 2026-10-18
    # (run 20261017T212607Z-26c5): `is_error` true, and the content a plain string with more text after the mode.
    _stream_with(tmp_path, monkeypatch, {
        "type": "tool_result",
        "content": (
            "Permission to use Bash has been denied because Claude Code is running in don't ask mode. IMPORTANT: You "
            "*may* attempt to accomplish this action using other tools that might naturally be used to accomplish this "
            "goal, e.g. using head instead of cat."
        ),
        "is_error": True,
        "tool_use_id": "toolu_012BYPEhdPGkc1zUfZVCBZ55",
    })

    result = adapters.get("claude-code").run(request(tmp_path))

    assert result.refused == (("Bash", "don't ask"),)
