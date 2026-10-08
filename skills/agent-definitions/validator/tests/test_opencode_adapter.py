"""The OpenCode adapter.

Each case starts the adapter through the registry, as the engine does, and
replaces the `opencode` process with a stand-in that replays a recorded stream
(see `tests/fixtures/opencode/manifest.json` for the harness version and the
date of each recording).
"""

from __future__ import annotations

from pathlib import Path

from agent_definitions import adapters
from agent_definitions.adapters import AdapterRequest

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
