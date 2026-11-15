"""The helpers of the tests of the snapshot of `delegate status`: the run that a journal builds step by step (#354)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from delegate.cli import delegate

from . import claude_fake
from .support import START, hold_lock, journal_path


def at(seconds: float) -> str:
    return (START + timedelta(seconds=seconds)).isoformat()


class Run:
    """The journal of one run, written line by line with the time of each event."""

    def __init__(self, repo: Path, run_id: str, *tickets: str, adapter: str = "claude-code", mode: str = "assure") -> None:
        self.path = journal_path(repo, run_id)
        self.path.parent.mkdir(parents=True)
        self.streams = self.path.parent / "reports"
        self.streams.mkdir()
        self.seq = 0
        self.add(0, "run-start", run_id=run_id, workflow="/w/workflow.toml", mode=mode, adapter=adapter,
                 base_branch="main", run_branch="run/demo", tickets=list(tickets))
        hold_lock(self.path)  # the run works now: a process holds its lock (#193)

    def add(self, seconds: float, event: str, **fields: object) -> None:
        self.seq += 1
        with self.path.open("a") as handle:
            handle.write(json.dumps({"seq": self.seq, "time": at(seconds), "event": event, **fields}) + "\n")

    def stream(self, name: str, text: str) -> str:
        path = self.streams / name
        path.write_text(text)
        return str(path)

    def step_start(self, seconds: float, ticket: str) -> None:
        self.add(seconds, "step-start", ticket=ticket, stack="python", branch=f"run/demo-{ticket}", worktree=f"/w/{ticket}",
                 base_commit="0" * 40, agent="python-specialist", tier="strong", model="m")

    def agent_start(self, seconds: float, ticket: str, role: str, stream: str, round_number: int = 0) -> None:
        self.add(seconds, "agent-start", ticket=ticket, role=role, round=round_number, event_stream=stream)

    def built(self, start: float, end: float, ticket: str, stream: str) -> None:
        """A ticket that ran its specialist and its verifier from `start` and ended built at `end`."""
        self.step_start(start, ticket)
        self.agent_start(start, ticket, "specialist", stream)
        self.add(start + 1, "adapter-result", ticket=ticket, exit_status=0, end_state="finished", session_id=None, event_stream=stream)
        self.add(end, "step-end", ticket=ticket, state="built", reason=None)


@pytest.fixture(autouse=True)
def utf8_locale(monkeypatch):
    """A UTF-8 locale, so the frames of `watch` have the Unicode glyph set (`delegate.run.output.glyphs`)."""
    monkeypatch.setenv("LC_ALL", "en_NZ.UTF-8")


def snapshot(repo: Path, capsys, *extra: str) -> dict:
    code = delegate.main(["status", "--repo", str(repo), "--json", *extra])
    captured = capsys.readouterr()
    assert (code, captured.err) == (0, ""), captured.err
    return json.loads(captured.out)


def claude(name: str) -> str:
    return (claude_fake.FIXTURES / name).read_text()
