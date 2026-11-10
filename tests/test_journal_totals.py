"""The journal holds the token totals of each agent that ended, and the views read an event stream only for an agent that runs (#183).

A case drives `delegate run`, `delegate status --json` or `delegate watch` against a real temporary git repository. The
adapter is the system boundary: a scripted adapter, or a counting adapter that reads the recorded streams of Claude Code.
The clock (the `clock` fixture) and the wait between two polls (the wait of the `clock` fixture) are faked.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from delegate import adapters
from delegate.adapters.claude_code import ClaudeCodeAdapter
from delegate.cli import delegate
from delegate.ports.harness import Activity, AdapterError

from . import claude_fake
from .support import (
    ScriptedAdapter, Terminal, append_events, bare_repo, fake_clock, frames, make_repo, read_journal, run_main,
    run_start, step_end, step_start, write_journal,
)

# The commands of each case use the fake clock of the case (#297): its helpers set it.
pytestmark = pytest.mark.usefixtures("clock")

START = datetime(2026, 10, 16, 3, 0, 0, tzinfo=timezone.utc)
LAST = datetime(2026, 10, 16, 3, 5, 0, tzinfo=timezone.utc)


@dataclass
class TotalsAdapter(ScriptedAdapter):
    """A scripted adapter whose `activity` gives fixed totals for a stream that exists, and raises for a missing stream."""

    def activity(self, stream: Path) -> Activity:
        if not stream.is_file():
            raise AdapterError(f"event stream {stream} not found")
        return Activity(1234, 5678, LAST)


@dataclass
class CountingClaude:
    """The Claude Code reader of streams, with a count of the parses of each stream."""

    tier_column: str = "claude-code"
    supports_resume: bool = True
    parsed: list[str] = field(default_factory=list)

    def activity(self, stream: Path) -> Activity:
        self.parsed.append(stream.name)
        return ClaudeCodeAdapter().activity(stream)


def status_json(repo: Path, capsys) -> dict:
    code = delegate.main(["status", "--repo", str(repo), "--json"])
    captured = capsys.readouterr()
    assert (code, captured.err) == (0, ""), captured.err
    return json.loads(captured.out)


def tokens_of(snap: dict) -> list[tuple[object, object]]:
    return [(t["tokens_new"], t["tokens_cache"]) for t in snap["tickets"]] + [(snap["tokens_new"], snap["tokens_cache"])]


def test_status_of_an_ended_run_gives_the_same_counts_after_the_event_streams_are_deleted(tmp_path, capsys):
    adapters.register("scripted", TotalsAdapter())
    try:
        repo = make_repo(tmp_path)
        code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
        assert (code, err) == (0, "")
        events = read_journal(out)
        before = status_json(repo, capsys)
        streams = {Path(str(e["event_stream"])) for e in events if "event_stream" in e}

        for stream in streams:
            stream.unlink()
        after = status_json(repo, capsys)
    finally:
        adapters.unregister("scripted")

    # The specialist and the verifier of the ticket each count 1234 new tokens and 5678 cache reads.
    assert tokens_of(before) == [(2468, 11356), (2468, 11356)]
    assert tokens_of(after) == tokens_of(before)
    results = [e for e in events if e["event"] in ("adapter-result", "verify-result")]
    assert [(e["tokens_new"], e["tokens_cache"], e["last_event"]) for e in results] == [(1234, 5678, LAST.isoformat())] * 2


def test_a_journal_with_no_totals_gives_the_counts_from_the_streams_and_one_with_totals_needs_no_stream(tmp_path, capsys):
    adapters.register("claude-totals", CountingClaude())
    try:
        repo = bare_repo(tmp_path)
        streams = tmp_path / "streams"
        streams.mkdir()
        old = streams / "old.stream.jsonl"
        old.write_text((claude_fake.FIXTURES / "finished.jsonl").read_text())
        start = run_start("r1", "old", "new")
        start[1]["adapter"] = "claude-totals"
        write_journal(
            repo, "r1", start,
            step_start("old"),
            ("adapter-result", {"ticket": "old", "exit_status": 0, "end_state": "finished", "session_id": None, "event_stream": str(old)}),
            step_end("old"),
            step_start("new"),
            ("adapter-result", {
                "ticket": "new", "exit_status": 0, "end_state": "finished", "session_id": None,
                "event_stream": str(streams / "gone.stream.jsonl"), "tokens_new": 40, "tokens_cache": 7, "last_event": None,
            }),
            step_end("new"),
        )

        snap = status_json(repo, capsys)
    finally:
        adapters.unregister("claude-totals")

    # `old` has no totals: its stream gives 19326 new tokens. `new` has totals: its stream is missing and does not matter.
    assert tokens_of(snap) == [(19326, 0), (40, 7), (19366, 7)]


def at(seconds: float) -> str:
    return (START + timedelta(seconds=seconds)).isoformat()


def test_the_live_view_parses_an_unchanged_stream_of_a_running_agent_once_over_many_frames(tmp_path, monkeypatch):
    reader = CountingClaude()
    adapters.register("claude-count", reader)
    try:
        repo = bare_repo(tmp_path)
        start = run_start("r1", "a")
        start[1]["adapter"] = "claude-count"
        journal = write_journal(repo, "r1", start, step_start("a"))
        lines = (claude_fake.FIXTURES / "capped.jsonl").read_text().splitlines(keepends=True)
        stream = journal.parent / "a.specialist.stream.jsonl"
        stream.write_text("".join(lines[:7]))  # the agent works
        append_events(journal, ("agent-start", {"ticket": "a", "role": "specialist", "round": 0, "event_stream": str(stream)}))
        out = Terminal()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setenv("COLUMNS", "120")
        fake_clock().at = (START + timedelta(seconds=120)).timestamp()
        waits: list[float] = []

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            if len(waits) == 3:
                append_events(journal, ("step-end", {"ticket": "a", "state": "built", "reason": None}), (
                    "run-end", {"result": "built", "built": ["a"], "failed": [], "skipped": []}))

        fake_clock().on_sleep = sleep

        code = delegate.main(["watch", "r1", "--repo", str(repo)])
    finally:
        adapters.unregister("claude-count")

    drawn = frames(out.getvalue())
    assert (code, len(waits)) == (0, 3)
    # Three polls with the agent running, and the last frame. The polls draw one frame, so the view writes it once (#282).
    assert len(drawn) == 2
    assert any("12k new" in line for line in drawn[0]), drawn[0]
    assert reader.parsed.count("a.specialist.stream.jsonl") == 1


def test_the_live_view_parses_a_stream_again_after_the_agent_wrote_to_it(tmp_path, monkeypatch):
    reader = CountingClaude()
    adapters.register("claude-count", reader)
    try:
        repo = bare_repo(tmp_path)
        start = run_start("r1", "a")
        start[1]["adapter"] = "claude-count"
        journal = write_journal(repo, "r1", start, step_start("a"))
        lines = (claude_fake.FIXTURES / "capped.jsonl").read_text().splitlines(keepends=True)
        stream = journal.parent / "a.specialist.stream.jsonl"
        stream.write_text("".join(lines[:7]))
        append_events(journal, ("agent-start", {"ticket": "a", "role": "specialist", "round": 0, "event_stream": str(stream)}))
        monkeypatch.setattr(sys, "stdout", Terminal())
        monkeypatch.setenv("COLUMNS", "120")
        fake_clock().at = (START + timedelta(seconds=120)).timestamp()
        waits: list[float] = []

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            if len(waits) == 1:
                with stream.open("a") as handle:
                    handle.write(lines[7])  # the agent writes its next event
            if len(waits) == 2:
                append_events(journal, ("step-end", {"ticket": "a", "state": "built", "reason": None}), (
                    "run-end", {"result": "built", "built": ["a"], "failed": [], "skipped": []}))

        fake_clock().on_sleep = sleep

        assert delegate.main(["watch", "r1", "--repo", str(repo)]) == 0
    finally:
        adapters.unregister("claude-count")

    assert reader.parsed.count("a.specialist.stream.jsonl") == 2


def test_an_agent_that_ended_with_totals_is_not_parsed_by_the_live_view(tmp_path, monkeypatch):
    reader = CountingClaude()
    adapters.register("claude-count", reader)
    try:
        repo = bare_repo(tmp_path)
        start = run_start("r1", "a")
        start[1]["adapter"] = "claude-count"
        journal = write_journal(repo, "r1", start, step_start("a"))
        stream = journal.parent / "a.specialist.stream.jsonl"
        stream.write_text((claude_fake.FIXTURES / "finished.jsonl").read_text())
        append_events(
            journal,
            ("agent-start", {"ticket": "a", "role": "specialist", "round": 0, "event_stream": str(stream)}),
            ("adapter-result", {
                "ticket": "a", "exit_status": 0, "end_state": "finished", "session_id": None, "event_stream": str(stream),
                "tokens_new": 19326, "tokens_cache": 0, "last_event": None,
            }),
        )
        out = Terminal()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setenv("COLUMNS", "120")
        fake_clock().at = (START + timedelta(seconds=120)).timestamp()

        def sleep(seconds: float) -> None:
            append_events(journal, ("step-end", {"ticket": "a", "state": "built", "reason": None}), (
                "run-end", {"result": "built", "built": ["a"], "failed": [], "skipped": []}))

        fake_clock().on_sleep = sleep

        assert delegate.main(["watch", "r1", "--repo", str(repo)]) == 0
    finally:
        adapters.unregister("claude-count")

    assert reader.parsed == []
    assert "19k new" in out.getvalue()
