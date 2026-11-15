"""The helpers of the tests of the live view of `delegate watch`: the run that a journal builds step by step, and the call of the command (#354)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from delegate.cli import delegate
from pathlib import Path
import io
import json
import sys

from . import claude_fake
from .support import Terminal, fake_clock, hold_lock, journal_path


START = datetime(2026, 10, 16, 3, 0, 0, tzinfo=timezone.utc)


PYTEST = "nix develop -c python3 -m pytest -q"


DOCS = 'uvx "zensical==0.0.2" build --strict'


def at(seconds: float) -> str:
    return (START + timedelta(seconds=seconds)).isoformat()


class Run:
    """The journal of one run, written line by line with the time of each event, and the event streams of its agents."""

    def __init__(
        self, repo: Path, *tickets: str, mode: str = "assure", run_id: str = "r1", blocked_by: dict[str, tuple[str, ...]] | None = None
    ) -> None:
        """`blocked_by` gives the blockers of each ticket. With it, `run-start` records the plan, as the engine does (#107)."""
        self.path = journal_path(repo, run_id)
        self.path.parent.mkdir(parents=True)
        self.dir = self.path.parent
        self.seq = 0
        plan = {} if blocked_by is None else {"plan": [
            {"id": t, "stack": "python", "blocked_by": list(blocked_by.get(t, ())), "text_sha256": "0" * 64} for t in tickets
        ]}
        self.add(0, "run-start", run_id=run_id, workflow="/w/workflow.toml", mode=mode, adapter="claude-code",
                 base_branch="main", run_branch="run/demo", tickets=list(tickets), **plan)
        hold_lock(self.path)  # the run works now: a process holds its lock (#193)

    def add(self, seconds: float, event: str, **fields: object) -> None:
        self.seq += 1
        with self.path.open("a") as handle:
            handle.write(json.dumps({"seq": self.seq, "time": at(seconds), "event": event, **fields}) + "\n")

    def stream(self, name: str, fixture: str = "finished.jsonl", cut: bool = False) -> str:
        """An event stream in the run directory. `cut` keeps the first seven lines and a part of the eighth: an agent that runs."""
        text = (claude_fake.FIXTURES / fixture).read_text()
        if cut:
            lines = text.splitlines(keepends=True)
            text = "".join(lines[:7]) + lines[7][:120]
        path = self.dir / "reports" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        return str(path)

    def step_start(self, seconds: float, ticket: str) -> None:
        self.add(seconds, "step-start", ticket=ticket, stack="python", branch=f"run/demo-{ticket}", worktree=f"/w/{ticket}",
                 base_commit="0" * 40, agent="python-specialist", tier="strong", model="m")

    def agent(
        self, start: float, end: float, ticket: str, role: str, stream: str, result: str = "adapter-result", end_state: str = "finished"
    ) -> None:
        self.add(start, "agent-start", ticket=ticket, role=role, round=0, event_stream=stream)
        self.add(end, result, ticket=ticket, **({"round": 0} if result == "verify-result" else {}), exit_status=0,
                 end_state=end_state, session_id="s", event_stream=stream)

    def report(self, seconds: float, ticket: str, valid: bool = True, reason: str | None = None) -> None:
        self.add(seconds, "report-validation", ticket=ticket, valid=valid, path=str(self.dir / "reports" / f"{ticket}.specialist.json"),
                 reason=reason, status="committed" if valid else None, blocked_reason=None)

    def gate(self, start: float, end: float, ticket: str, command: str, green: bool = True) -> None:
        log = self.dir / "gates" / ticket / "build-0.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("")
        self.add(start, "gate-start", ticket=ticket, round=0, phase="build", command=command, gate_log=str(log))
        self.add(end, "gate-result", ticket=ticket, round=0, phase="build", command=command, commit="1" * 40,
                 exit_status=0 if green else 1, green=green, output_tail="ok")

    def verify(self, start: float, end: float, ticket: str, verdict: str = "ACCEPT", chain: list[str] | None = None) -> None:
        """A verifier of one ticket, or with `chain` the verifier of the chain of the stack `python` in `economy` mode."""
        of_chain: dict[str, object] = {} if chain is None else {"stack": "python", "tickets": chain}
        self.add(start, "verify-start", ticket=ticket, round=0, agent="python-verifier", tier="verifier", model="m", commit="1" * 40,
                 copy=str(self.dir / "copy"), **of_chain)
        stream = self.stream(f"{ticket}.verifier.stream.jsonl")
        self.agent(start, end, ticket, "verifier", stream, result="verify-result")
        self.add(end, "verify-report", ticket=ticket, round=0, valid=True, path=str(self.dir / "verdict.json"), reason=None)
        if chain is not None:
            of_chain.update(mapped={}, unmapped=[])
        self.add(end, "verdict", ticket=ticket, round=0, mode="full", verdict=verdict, findings=[] if verdict == "ACCEPT" else ["a: x"],
                 unverified=[], report=str(self.dir / "verdict.json"), **of_chain)


def ticket_a_is_built(run: Run) -> None:
    """Ticket a: the specialist runs 36 minutes, the gates 16 minutes and 1 second, the verifier 7 minutes 59 seconds."""
    run.step_start(0, "a")
    run.agent(0, 2160, "a", "specialist", run.stream("a.specialist.stream.jsonl"))
    run.report(2160, "a")
    run.gate(2160, 3120, "a", PYTEST)
    run.gate(3120, 3121, "a", DOCS)
    run.verify(3121, 3600, "a")
    run.add(3600, "run-branch-advance", ticket="a", run_branch="run/demo", from_commit="0" * 40, to_commit="1" * 40)
    run.add(3600, "step-end", ticket="a", state="built", reason=None)


def watch(
    repo: Path, monkeypatch, *extra: str, terminal: bool = True, columns: int = 200, rows: int = 50, then=None, sleep=None
) -> tuple[int, str]:
    """Run `delegate watch r1` with standard output to a terminal or to a pipe.

    The first wait calls `then`, and a later wait fails the case. A case that waits more than once gives its own `sleep`.
    """
    out = Terminal() if terminal else io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))
    waits: list[float] = []

    def wait_once(seconds: float) -> None:
        assert then is not None and not waits, "watch waited, but the run has no more events"
        waits.append(seconds)
        then()

    fake_clock().on_sleep = sleep or wait_once
    code = delegate.main(["watch", "r1", "--repo", str(repo), *extra])
    return code, out.getvalue()
