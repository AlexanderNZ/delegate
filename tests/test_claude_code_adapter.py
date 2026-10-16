"""The Claude Code adapter.

Each case starts the adapter through the registry, as the engine does, and
replaces the `claude` process with a stand-in that replays a recorded stream
(see `tests/fixtures/claude-code/manifest.json` for the harness version and the
date of each recording).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from delegate import adapters
from delegate.ports.harness import Activity, AdapterError, AdapterRequest

from . import claude_fake


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


def test_a_finished_result_with_a_non_zero_exit_status_gives_the_end_state_failed(tmp_path, monkeypatch):
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl", exit_status=3)

    result = adapters.get("claude-code").run(request(tmp_path))

    assert (result.exit_status, result.end_state) == (3, "failed")


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


def test_the_activity_of_a_stream_with_no_timed_event_has_no_tokens_and_no_time(tmp_path):
    live = tmp_path / "live.jsonl"
    live.write_text("".join(capped_lines()[:3]))

    assert adapters.get("claude-code").activity(live) == Activity(0, None)


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
