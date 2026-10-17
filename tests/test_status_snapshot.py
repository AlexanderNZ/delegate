"""`delegate status --json`, and the live block of `delegate watch` on a terminal: one snapshot of a run, with progress, ETA, tokens, and agent activity.

A case writes the journal of a run in the state directory of a real temporary
git repository, with the time of each event, as the engine writes it. The event
streams are the recorded fixtures of the harness adapters, or a part of one for
an agent that still runs. The clock (`time.time`) is the system boundary, so a
case fixes it. `watch` waits between two polls with `time.sleep`, which a case
replaces, and a case gives it a terminal: a standard output whose `isatty` is true.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from delegate import adapters
from delegate.cli import delegate

from . import claude_fake, opencode_fake
from .support import Terminal, bare_repo, frames, journal_path

START = datetime(2026, 10, 16, 3, 0, 0, tzinfo=timezone.utc)


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


def frozen(monkeypatch, seconds: float) -> None:
    monkeypatch.setattr(time, "time", lambda: (START + timedelta(seconds=seconds)).timestamp())


def snapshot(repo: Path, capsys, *extra: str) -> dict:
    code = delegate.main(["status", "--repo", str(repo), "--json", *extra])
    captured = capsys.readouterr()
    assert (code, captured.err) == (0, ""), captured.err
    return json.loads(captured.out)


def claude(name: str) -> str:
    return (claude_fake.FIXTURES / name).read_text()


def opencode(name: str) -> str:
    return (opencode_fake.FIXTURES / name).read_text()


def ticket_of(snap: dict, ticket_id: str) -> dict:
    (found,) = [t for t in snap["tickets"] if t["id"] == ticket_id]
    return found


def test_a_live_claude_code_stream_gives_the_tokens_and_the_seconds_since_the_last_activity_of_the_running_agent(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", "b")
    lines = claude("capped.jsonl").splitlines(keepends=True)
    live = run.stream("a.specialist.stream.jsonl", "".join(lines[:7]) + lines[7][:120])  # the agent writes its eighth line now
    run.step_start(0, "a")
    run.agent_start(0, "a", "specialist", live)
    last_line = datetime(2026, 10, 8, 19, 55, 53, 245000, tzinfo=timezone.utc)  # the time of the seventh line
    frozen(monkeypatch, 120)
    now = START + timedelta(seconds=120)

    snap = snapshot(repo, capsys)

    assert ticket_of(snap, "a") == {
        "id": "a", "state": "running", "phase": "specialist", "elapsed_seconds": 120, "tokens": 25794,
        "idle_seconds": int((now - last_line).total_seconds()), "gate": None, "gate_idle_seconds": None,
    }
    assert ticket_of(snap, "b") == {"id": "b", "state": "pending", "phase": None, "elapsed_seconds": 0, "tokens": 0, "idle_seconds": None, "gate": None, "gate_idle_seconds": None}
    assert (snap["run_id"], snap["state"], snap["elapsed_seconds"], snap["tokens"]) == ("r1", "running", 120, 25794)
    assert (snap["tickets_total"], snap["tickets_ended"], snap["percent"]) == (2, 0, 0)


def test_an_agent_whose_stream_ends_with_tool_heartbeats_is_idle_since_the_harness_last_wrote_to_its_stream(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a")
    heartbeats = (claude_fake.FIXTURES.parent / "claude-code-progress" / "heartbeats.jsonl").read_text()
    live = run.stream("a.specialist.stream.jsonl", "".join(claude("capped.jsonl").splitlines(keepends=True)[:7]) + heartbeats)
    written = (START + timedelta(seconds=100)).timestamp()
    os.utime(live, (written, written))  # the newest heartbeat; the heartbeats have no time of their own
    run.step_start(0, "a")
    run.agent_start(0, "a", "specialist", live)
    frozen(monkeypatch, 120)

    a = ticket_of(snapshot(repo, capsys), "a")

    assert (a["tokens"], a["idle_seconds"]) == (25794, 20)


def test_a_live_opencode_stream_gives_the_tokens_and_the_seconds_since_the_last_activity_of_the_running_agent(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", adapter="opencode")
    second = opencode("step-cap.jsonl").splitlines(keepends=True)
    live = run.stream("a.specialist.stream.jsonl", opencode("finished.jsonl") + "".join(second[:2]) + second[2][:60])
    run.step_start(0, "a")
    run.agent_start(0, "a", "specialist", live)
    # The newest complete line has the timestamp 1791490924475 (epoch milliseconds).
    last_line = datetime(2026, 10, 8, 20, 22, 4, 475000, tzinfo=timezone.utc)
    frozen(monkeypatch, 300)

    snap = snapshot(repo, capsys)

    assert ticket_of(snap, "a")["tokens"] == snap["tokens"] == 84197
    assert ticket_of(snap, "a")["idle_seconds"] == int((START + timedelta(seconds=300) - last_line).total_seconds())


def test_an_agent_whose_stream_has_no_event_yet_is_idle_since_the_journal_started_it(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a")
    run.step_start(0, "a")
    run.agent_start(10, "a", "specialist", str(run.streams / "a.specialist.stream.jsonl"))  # the harness has not opened it yet
    frozen(monkeypatch, 25)

    a = ticket_of(snapshot(repo, capsys), "a")

    assert (a["tokens"], a["idle_seconds"]) == (0, 15)


def test_the_eta_is_null_before_the_first_ticket_ends(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", "b")
    run.step_start(0, "a")
    run.agent_start(0, "a", "specialist", run.stream("a.stream.jsonl", claude("finished.jsonl")))
    run.add(500, "adapter-result", ticket="a", exit_status=0, end_state="finished", session_id=None, event_stream=str(run.streams / "a.stream.jsonl"))
    run.add(501, "report-validation", ticket="a", valid=True, path="/r", reason=None, status="committed", blocked_reason=None)
    frozen(monkeypatch, 3600)

    snap = snapshot(repo, capsys)

    assert snap["eta"] is None
    assert (ticket_of(snap, "a")["phase"], ticket_of(snap, "a")["idle_seconds"], snap["percent"]) == ("gates", None, 30)


def test_the_percentage_and_the_eta_follow_the_rules_of_the_reference_page(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", "b", "c", "d", "e")
    finished = claude("finished.jsonl")
    run.built(0, 600, "a", run.stream("a.stream.jsonl", finished))  # 600 seconds
    run.built(600, 900, "b", run.stream("b.stream.jsonl", finished))  # 300 seconds
    run.add(900, "skip", ticket="c", blockers=["x"], reason="blocked by x, which failed")  # ended, with no step
    run.step_start(900, "d")
    run.agent_start(950, "d", "verifier", run.stream("d.verifier.stream.jsonl", finished))
    frozen(monkeypatch, 1000)

    snap = snapshot(repo, capsys)

    # Ended: a, b, c count 1 each. d is in the verifier phase: 0.8. e is pending: 0. floor(3.8 / 5 * 100) = 76.
    assert (snap["tickets_ended"], snap["percent"]) == (3, 76)
    # The mean of the tickets that ended after a step is (600 + 300) / 2 = 450. Two tickets (d, e) did not end,
    # and d used 100 seconds: now + 450 * 2 - 100.
    assert snap["eta"] == at(1000 + 450 * 2 - 100)
    assert snap["tokens"] == 3 * 19326
    assert [t["state"] for t in snap["tickets"]] == ["built", "built", "skipped", "running", "pending"]
    assert snap["phase"] is None  # only the chain of `economy` mode is a phase of the run


def test_the_eta_is_never_earlier_than_now(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", "b")
    run.built(0, 60, "a", run.stream("a.stream.jsonl", claude("finished.jsonl")))
    run.step_start(60, "b")
    run.agent_start(60, "b", "specialist", run.stream("b.stream.jsonl", ""))
    frozen(monkeypatch, 4000)  # b used 3940 seconds, much more than the mean of 60

    assert snapshot(repo, capsys)["eta"] == at(4000)


@pytest.mark.parametrize(
    ("events", "phase", "percent"),
    [
        pytest.param([], "specialist", 0, id="specialist"),
        pytest.param([("report-validation", {"valid": True, "path": "/r", "reason": None, "status": "committed", "blocked_reason": None})], "gates", 60, id="gates"),
        pytest.param([("rebase", {"result": "rebased", "onto_commit": "1", "from_commit": "2", "to_commit": "3", "files": []})], "gates", 60, id="rebase"),
        pytest.param([("verify-start", {"round": 0, "agent": "v", "tier": "verifier", "model": "m", "commit": "3", "copy": "/c"})], "verifier", 80, id="verifier"),
        pytest.param([("fixup-start", {"round": 1, "rejected_commit": "3", "findings_file": "/f", "agent": "s", "tier": "strong", "model": "m"})], "fix-up", 80, id="fix-up"),
        pytest.param([
            ("fixup-start", {"round": 1, "rejected_commit": "3", "findings_file": "/f", "agent": "s", "tier": "strong", "model": "m"}),
            ("gate-result", {"round": 1, "phase": "fixup", "command": "t", "exit_status": 0, "green": True, "output_tail": ""}),
        ], "fix-up", 80, id="gates-of-a-fix-up"),
        pytest.param([
            ("gate-result", {"round": 0, "phase": "build", "command": "t", "exit_status": 1, "green": False, "output_tail": ""}),
            ("continuation", {"count": 1, "limit": 2, "trigger": "gates-red", "mode": "brief", "resume_session": None, "commits": [], "reason": "r"}),
        ], "specialist", 0, id="continuation"),
        pytest.param([
            ("verify-start", {"round": 0, "agent": "v", "tier": "verifier", "model": "m", "commit": "3", "copy": "/c"}),
            ("gate-skip", {"round": 0, "phase": "build", "command": "t", "commit": "3", "green_seq": 4, "reason": "r"}),
        ], "gates", 60, id="gate-skip-of-a-resume"),
        pytest.param([
            ("fixup-start", {"round": 1, "rejected_commit": "3", "findings_file": "/f", "agent": "s", "tier": "strong", "model": "m"}),
            ("gate-skip", {"round": 1, "phase": "fixup", "command": "t", "commit": "4", "green_seq": 9, "reason": "r"}),
        ], "fix-up", 80, id="gate-skip-of-a-fix-up"),
    ],
)
def test_the_phase_of_a_running_ticket_follows_its_newest_event(tmp_path, capsys, monkeypatch, events, phase, percent):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a")
    run.step_start(0, "a")
    for name, fields in events:
        run.add(1, name, ticket="a", **fields)
    frozen(monkeypatch, 2)

    snap = snapshot(repo, capsys)

    assert (ticket_of(snap, "a")["phase"], snap["percent"]) == (phase, percent)


def test_a_run_that_ended_stops_its_clock_at_the_end_and_has_no_running_agent(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a")
    run.built(0, 600, "a", run.stream("a.stream.jsonl", claude("finished.jsonl")))
    run.add(610, "run-end", result="built", built=["a"], failed=[], skipped=[])
    frozen(monkeypatch, 9000)

    snap = snapshot(repo, capsys)

    assert (snap["state"], snap["elapsed_seconds"], snap["percent"], snap["eta"], snap["time"]) == ("built", 610, 100, at(610), at(610))
    assert ticket_of(snap, "a") == {"id": "a", "state": "built", "phase": None, "elapsed_seconds": 600, "tokens": 19326, "idle_seconds": None, "gate": None, "gate_idle_seconds": None}


def test_the_tokens_of_a_ticket_add_every_agent_that_it_ran(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", adapter="opencode")
    run.step_start(0, "a")
    run.agent_start(0, "a", "specialist", run.stream("a.1.jsonl", opencode("finished.jsonl")))
    run.agent_start(10, "a", "verifier", run.stream("a.2.jsonl", opencode("step-cap.jsonl")))
    run.agent_start(20, "a", "fix-up", run.stream("a.3.jsonl", opencode("capped.jsonl")), 1)
    frozen(monkeypatch, 30)

    assert ticket_of(snapshot(repo, capsys), "a")["tokens"] == 84197 + 84637 + 83892


def test_the_verifier_of_an_economy_chain_shows_its_activity_after_its_ticket_ended(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a")
    run.built(0, 60, "a", run.stream("a.stream.jsonl", claude("finished.jsonl")))
    run.add(61, "verify-start", ticket="a", round=0, agent="v", tier="verifier", model="m", commit="3", copy="/c", stack="python", tickets=["a"])
    run.agent_start(61, "a", "verifier", str(run.streams / "chain.stream.jsonl"))  # the harness has not opened it yet
    frozen(monkeypatch, 70)

    a = ticket_of(snapshot(repo, capsys), "a")

    assert (a["state"], a["phase"], a["tokens"], a["idle_seconds"]) == ("built", None, 19326, 9)


def an_economy_chain_after_its_last_ticket(repo: Path) -> Run:
    """An `economy` run whose tickets a (0 to 600) and b (600 to 900) are built. The verification of the chain comes next."""
    run = Run(repo, "r1", "a", "b", mode="economy")
    finished = claude("finished.jsonl")
    run.built(0, 600, "a", run.stream("a.stream.jsonl", finished))
    run.built(600, 900, "b", run.stream("b.stream.jsonl", finished))
    return run


def chain_verifier_starts(run: Run, seconds: float, round_number: int) -> None:
    """The verifier of the chain starts at `seconds`. The journal names it for b, the last ticket of the stack python."""
    run.add(seconds, "verify-start", ticket="b", round=round_number, agent="v", tier="verifier", model="m", commit="3", copy="/c",
            stack="python", tickets=["a", "b"])
    run.agent_start(seconds, "b", "verifier", run.stream(f"chain.{round_number}.stream.jsonl", ""), round_number)


def chain_fixup_starts(run: Run, seconds: float) -> None:
    """The verifier of the chain rejects at `seconds`, and the fix-up specialist starts."""
    run.add(seconds, "verify-result", ticket="b", round=0, exit_status=0, end_state="finished", session_id=None,
            event_stream=str(run.streams / "chain.0.stream.jsonl"))
    run.add(seconds, "verdict", ticket="b", round=0, mode="full", verdict="REJECT", findings=["a: x"], unverified=[], report="/r",
            stack="python", tickets=["a", "b"], mapped={"a": ["a: x"]}, unmapped=[])
    run.add(seconds, "fixup-start", ticket="b", round=1, rejected_commit="3", findings_file="/f", agent="s", tier="standard", model="m")
    run.agent_start(seconds, "b", "fix-up", run.stream("chain.fixup.stream.jsonl", ""), 1)


def chain_gates_start(run: Run, seconds: float, round_number: int) -> None:
    """The gates of the chain start on its tip at `seconds`. The journal names them for b, the last ticket of the stack python."""
    run.add(seconds, "gate-start", ticket="b", round=round_number, phase="chain", command="pytest", gate_log=str(run.streams / "gate.log"))


def block_of(frame: list[str]) -> list[str]:
    """The live block of a frame of `watch`: the lines above the first blank line."""
    return frame[: frame.index("")] if "" in frame else frame


def follow_lines(repo: Path, monkeypatch, run: Run) -> list[str]:
    """The live block of the first frame of `watch` on a terminal. The first wait ends the run."""
    out = Terminal()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(time, "sleep", lambda seconds: run.add(2000, "run-end", result="built", built=["a", "b"], failed=[], skipped=[]))
    assert delegate.main(["watch", "--repo", str(repo)]) == 0
    return block_of(frames(out.getvalue())[0])


# Two tickets ended (2 of 3 units, with the chain), and no ticket runs: floor(2 / 3 * 100) = 66. The mean of the
# tickets is (600 + 300) / 2 = 450, and one unit, the chain, did not end. The tokens are 19326 for each ticket, and 0 for
# a chain agent whose stream has no event yet.
@pytest.mark.parametrize(
    ("chain", "phase", "eta"),
    [
        pytest.param(lambda run: chain_gates_start(run, 901, 0), "gates", 450 - 99, id="gates"),
        pytest.param(
            lambda run: (chain_gates_start(run, 901, 0), chain_verifier_starts(run, 950, 0)), "verifier", 450 - 99, id="verifier-after-the-gates",
        ),
        pytest.param(
            lambda run: (chain_gates_start(run, 901, 0), chain_verifier_starts(run, 920, 0), chain_fixup_starts(run, 950), chain_gates_start(run, 980, 1)),
            "gates", 450 - 99, id="gates-after-a-fix-up",
        ),
        pytest.param(lambda run: chain_verifier_starts(run, 901, 0), "verifier", 450 - 99, id="verifier"),
        pytest.param(lambda run: (chain_verifier_starts(run, 901, 0), chain_fixup_starts(run, 950)), "fix-up", 450 - 99, id="fix-up"),
        pytest.param(
            lambda run: (chain_verifier_starts(run, 901, 0), chain_fixup_starts(run, 950), chain_verifier_starts(run, 990, 1)),
            "verifier", 450 - 99, id="scoped-verifier",
        ),
        pytest.param(lambda run: None, None, 450, id="before-the-verifier-starts"),
    ],
)
def test_the_verification_of_an_economy_chain_is_a_phase_of_the_run_and_keeps_the_percentage_below_100_in_each_form(
    tmp_path, capsys, monkeypatch, chain, phase, eta
):
    repo = bare_repo(tmp_path)
    run = an_economy_chain_after_its_last_ticket(repo)
    chain(run)
    frozen(monkeypatch, 1000)

    snap = snapshot(repo, capsys)
    lines = plain_status(repo, capsys)
    view = follow_lines(repo, monkeypatch, run)

    assert (snap["state"], snap["phase"], snap["percent"], snap["tickets_ended"], snap["eta"]) == ("running", phase, 66, 2, at(1000 + eta))
    assert [(t["state"], t["phase"]) for t in snap["tickets"]] == [("built", None), ("built", None)]
    shown = f"{phase}, " if phase else ""
    eta_text = "5m51s" if eta == 450 - 99 else "7m30s"
    assert lines[1] == f"progress: {shown}66%, 2 of 2 ended, 16m40s used, ETA {eta_text}, 39k tokens"
    assert view[0] == f"run r1 running {shown}66%, 2 of 2 ended, 16m40s used, ETA {eta_text}, 39k tokens"


def test_in_economy_mode_the_chain_counts_as_one_more_unit_while_a_ticket_runs(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = a_run_of_two_tickets(repo)
    run.path.write_text(run.path.read_text().replace('"mode": "assure"', '"mode": "economy"', 1))
    frozen(monkeypatch, 650)

    snap = snapshot(repo, capsys)

    # a ended, b is in the specialist phase, and the chain did not start: floor(1 / 3 * 100) = 33. The mean is 600, two
    # units (b and the chain) did not end, and b used 50 seconds: now + 600 * 2 - 50.
    assert (snap["phase"], snap["percent"], snap["eta"]) == (None, 33, at(650 + 600 * 2 - 50))


def test_an_economy_run_is_complete_only_at_its_end(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_chain_after_its_last_ticket(repo)
    chain_verifier_starts(run, 901, 0)
    run.add(1500, "verdict", ticket="b", round=0, mode="full", verdict="ACCEPT", findings=[], unverified=[], report="/r",
            stack="python", tickets=["a", "b"], mapped={}, unmapped=[])
    run.add(1501, "run-end", result="built", built=["a", "b"], failed=[], skipped=[])
    frozen(monkeypatch, 9000)

    snap = snapshot(repo, capsys)

    assert (snap["state"], snap["phase"], snap["percent"], snap["eta"]) == ("built", None, 100, at(1501))
    assert plain_status(repo, capsys)[1] == "progress: 100%, 2 of 2 ended, 25m01s used, 39k tokens"


def test_an_agent_that_a_crash_stopped_shows_no_activity(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a")
    run.step_start(0, "a")
    run.agent_start(0, "a", "specialist", run.stream("a.stream.jsonl", claude("finished.jsonl")))
    run.add(5, "step-end", ticket="a", state="failed", reason="crashed: AdapterError: no login")
    frozen(monkeypatch, 70)

    assert ticket_of(snapshot(repo, capsys), "a")["idle_seconds"] is None


def a_run_of_two_tickets(repo: Path, adapter: str = "claude-code") -> Run:
    """Ticket a ended built after 600 seconds. The specialist of ticket b runs now, from 600, and writes its eighth line."""
    run = Run(repo, "r1", "a", "b", adapter=adapter)
    run.built(0, 600, "a", run.stream("a.stream.jsonl", claude("finished.jsonl")))
    lines = claude("capped.jsonl").splitlines(keepends=True)
    run.step_start(600, "b")
    run.agent_start(600, "b", "specialist", run.stream("b.stream.jsonl", "".join(lines[:7]) + lines[7][:120]))
    return run


def plain_status(repo: Path, capsys) -> list[str]:
    code = delegate.main(["status", "--repo", str(repo)])
    captured = capsys.readouterr()
    assert (code, captured.err) == (0, ""), captured.err
    return captured.out.splitlines()


def test_plain_status_shows_the_percentage_the_eta_the_tokens_and_the_last_activity_of_each_running_agent(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = a_run_of_two_tickets(repo)
    frozen(monkeypatch, 650)

    lines = plain_status(repo, capsys)

    # The mean of the ended tickets is 600, and b used 50 seconds: the ETA is 550 seconds from now.
    # The tokens: 19326 for a, and 25794 for the one turn of b so far. b had its last activity on 2026-10-08.
    assert lines == [
        "run r1: running (mode assure, adapter claude-code)",
        "progress: 50%, 1 of 2 ended, 10m50s used, ETA 9m10s, 45k tokens",
        f"journal {run.path}",
        f"ticket a: state built, verdict -, branch run/demo-a, last event {at(600)}",
        "  progress: 10m00s used, 19k tokens",
        f"ticket b: state running, verdict -, branch run/demo-b, last event {at(600)}",
        "  progress: specialist, 50s used, 26k tokens, last activity 7d07h ago",
    ]


def test_plain_status_of_a_run_that_ended_shows_its_progress_with_no_eta(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", "b")
    run.built(0, 600, "a", run.stream("a.stream.jsonl", claude("finished.jsonl")))
    run.add(601, "skip", ticket="b", blockers=["x"], reason="blocked by x")
    run.add(610, "run-end", result="built", built=["a"], failed=[], skipped=["b"])
    frozen(monkeypatch, 9000)

    lines = plain_status(repo, capsys)

    assert lines[:2] == ["run r1: built (mode assure, adapter claude-code)", "progress: 100%, 2 of 2 ended, 10m10s used, 19k tokens"]
    assert lines[3:] == [
        f"ticket a: state built, verdict -, branch run/demo-a, last event {at(600)}",
        "  progress: 10m00s used, 19k tokens",
        f"ticket b: state skipped, verdict -, branch -, last event {at(601)}",
        "  reason: blocked by x",
    ]


class AdapterOfAnEarlierVersion:
    """An adapter that a script registered in its own process, before adapters had a stream reader."""

    tier_column = "claude-code"
    supports_resume = False

    def run(self, request):
        raise AssertionError("delegate status must not run an agent")


@pytest.fixture
def earlier_adapter():
    adapters.register("earlier", AdapterOfAnEarlierVersion())
    yield "earlier"
    adapters.unregister("earlier")


@pytest.fixture(params=["not-known", "no-reader"])
def adapter_with_no_reader(request) -> str:
    """The name of an adapter whose streams the status process cannot read."""
    return request.getfixturevalue("earlier_adapter") if request.param == "no-reader" else "no-such-harness"


def test_json_on_a_run_whose_adapter_has_no_reader_shows_the_progress_and_the_eta_and_no_tokens_and_no_activity(
    tmp_path, capsys, monkeypatch, adapter_with_no_reader
):
    repo = bare_repo(tmp_path)
    a_run_of_two_tickets(repo, adapter_with_no_reader)
    frozen(monkeypatch, 650)

    snap = snapshot(repo, capsys)

    assert (snap["percent"], snap["tickets_ended"], snap["eta"], snap["tokens"]) == (50, 1, at(650 + 550), None)
    assert ticket_of(snap, "a") == {"id": "a", "state": "built", "phase": None, "elapsed_seconds": 600, "tokens": None, "idle_seconds": None, "gate": None, "gate_idle_seconds": None}
    assert ticket_of(snap, "b") == {"id": "b", "state": "running", "phase": "specialist", "elapsed_seconds": 50, "tokens": None, "idle_seconds": None, "gate": None, "gate_idle_seconds": None}


def test_plain_status_on_a_run_whose_adapter_has_no_reader_shows_the_progress_and_the_eta_and_no_tokens_and_no_activity(
    tmp_path, capsys, monkeypatch, adapter_with_no_reader
):
    repo = bare_repo(tmp_path)
    a_run_of_two_tickets(repo, adapter_with_no_reader)
    frozen(monkeypatch, 650)

    lines = plain_status(repo, capsys)

    assert lines[1] == "progress: 50%, 1 of 2 ended, 10m50s used, ETA 9m10s"
    assert [line for line in lines if line.startswith("  progress: ")] == ["  progress: 10m00s used", "  progress: specialist, 50s used"]


def test_the_live_block_on_a_run_whose_adapter_has_no_reader_shows_the_progress_and_the_eta_and_no_tokens_and_no_activity(
    tmp_path, monkeypatch, adapter_with_no_reader
):
    repo = bare_repo(tmp_path)
    run = a_run_of_two_tickets(repo, adapter_with_no_reader)

    code, drawn, _ = follow(repo, monkeypatch, run, columns=200)

    assert code == 0
    assert block_of(drawn[0]) == [
        "run r1 running 50%, 1 of 2 ended, 10m50s used, ETA 9m10s",
        "  a built, 10m00s used",
        "  b running specialist, 50s used",
    ]


def a_live_run(repo: Path) -> Run:
    run = Run(repo, "20261016T030000Z-abcd", "first-ticket-with-a-long-id", "b")
    capped = claude("capped.jsonl")  # it holds tool calls, which the view must not show
    run.built(0, 600, "first-ticket-with-a-long-id", run.stream("a.stream.jsonl", capped))
    run.step_start(600, "b")
    run.agent_start(600, "b", "specialist", run.stream("b.stream.jsonl", capped))
    return run


def follow(repo: Path, monkeypatch, run: Run, *extra: str, columns: int = 60) -> tuple[int, list[list[str]], list[float]]:
    """Run `watch` on a terminal while the run goes on, and return its exit code, its frames, and its waits.

    The first wait ends the ticket b and the run; a later wait fails the case.
    """
    out = Terminal()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", str(columns))
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        assert not waits, "watch did not exit when the run ended"
        waits.append(seconds)
        run.add(700, "step-end", ticket="b", state="built", reason=None)
        run.add(701, "run-end", result="built", built=["first-ticket-with-a-long-id", "b"], failed=[], skipped=[])

    monkeypatch.setattr(time, "sleep", sleep)
    frozen(monkeypatch, 650)
    code = delegate.main(["watch", "--repo", str(repo), *extra])
    return code, frames(out.getvalue()), waits


TOOL_NAMES = ("Bash", "tool_use", "echo one")


def test_the_live_block_has_a_line_for_the_run_and_one_for_each_ticket_and_watch_draws_it_again_at_each_poll(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = a_live_run(repo)

    code, drawn, waits = follow(repo, monkeypatch, run, "--poll-seconds", "3")

    assert (code, waits) == (0, [3.0])
    first, second = (block_of(frame) for frame in drawn)
    assert len(first) == len(second) == 3
    assert first[0].startswith("run 20261016T030000Z-abcd running 50%")
    assert second[0].startswith("run 20261016T030000Z-abcd built 100%")
    assert "b" in second[2] and "built" in second[2]


@pytest.mark.parametrize("columns", [30, 60, 200])
def test_the_live_view_has_no_line_wider_than_the_terminal_no_tool_call_and_no_absolute_path(tmp_path, monkeypatch, columns):
    repo = bare_repo(tmp_path)
    run = a_live_run(repo)

    code, drawn, _ = follow(repo, monkeypatch, run, columns=columns)

    assert code == 0
    for line in (line for frame in drawn for line in frame):
        assert len(line) <= columns, line
        assert not any(name in line for name in TOOL_NAMES), line
        assert not re.search(r"(^|\s)/", line), line
        assert str(tmp_path) not in line


def test_watch_of_a_run_that_ended_draws_it_once_and_exits_with_no_wait(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = a_live_run(repo)
    run.add(640, "step-end", ticket="b", state="built", reason=None)
    run.add(641, "run-end", result="built", built=["first-ticket-with-a-long-id", "b"], failed=[], skipped=[])

    code, drawn, waits = follow(repo, monkeypatch, run)

    assert (code, waits, len(drawn)) == (0, [], 1)
    assert drawn[0][0].startswith("run 20261016T030000Z-abcd built 100%")


@pytest.mark.parametrize(
    ("option", "refused"),
    [(["--follow"], "--follow"), (["--interval", "3"], "--interval"), (["--json", "--follow"], "--follow")],
)
def test_status_has_no_follow_and_no_interval_any_more(tmp_path, capsys, option, refused):
    repo = bare_repo(tmp_path)

    with pytest.raises(SystemExit) as stopped:
        delegate.main(["status", "--repo", str(repo), *option])

    assert stopped.value.code == 2
    assert f"unrecognized arguments: {refused}" in capsys.readouterr().err


# The streams that `delegate status` cannot read. Each is the stream of one agent; the kind names why it cannot be read.
OPENCODE_STEP_WITH_NO_TOKENS = '{"type":"step_finish","timestamp":1791490924475,"sessionID":"s","part":{"reason":"stop"}}\n'
# A line that is JSON but that has a bad event shape makes the reader of the adapter raise an error that is not an
# AdapterError: ValueError, TypeError, or AttributeError.
BAD_SHAPES = {
    "opencode-tokens-total-not-a-number": '{"type":"step_finish","timestamp":1791490924475,"part":{"tokens":{"total":"abc"}}}\n',
    "opencode-timestamp-not-a-number": '{"type":"text","timestamp":"x"}\n',
    "opencode-line-is-a-number": "5\n",
    "claude-code-timestamp-not-a-time": '{"type":"assistant","timestamp":"not a time","message":{"id":"m","usage":{"output_tokens":1}}}\n',
    "claude-code-usage-not-a-number": '{"type":"result","subtype":"success","usage":{"input_tokens":"many"}}\n',
    "claude-code-line-is-a-list": "[1, 2]\n",
}
BAD_STREAMS = [
    "missing", "unreadable", "parent-not-searchable", "directory", "first-line-not-json",
    "opencode-step-finish-with-no-tokens", "not-utf-8", *BAD_SHAPES,
]


@pytest.fixture
def bad_stream(request, tmp_path):
    """Make a stream that cannot be read, of the kind that the case names. Give its path and the adapter of its harness."""
    made: list[tuple[Path, int]] = []  # each path that has no permission, and the mode that it gets back

    def make(run: Run, kind: str) -> str:
        path = run.streams / f"{kind}.stream.jsonl"
        if kind in ("unreadable", "parent-not-searchable") and os.geteuid() == 0:
            pytest.skip("the root user can read a file that has no read permission")
        if kind == "unreadable":
            path.write_text(claude("finished.jsonl"))
            path.chmod(0)
            made.append((path, 0o600))
        elif kind == "parent-not-searchable":
            parent = run.streams / "locked"
            parent.mkdir()
            path = parent / f"{kind}.stream.jsonl"
            path.write_text(claude("finished.jsonl"))
            parent.chmod(0)
            made.append((parent, 0o700))
        elif kind in BAD_SHAPES:
            path.write_text(BAD_SHAPES[kind])
        elif kind == "directory":
            path.mkdir()
        elif kind == "first-line-not-json":
            path.write_text("this is not JSON\n" + claude("finished.jsonl"))
        elif kind == "opencode-step-finish-with-no-tokens":
            path.write_text(OPENCODE_STEP_WITH_NO_TOKENS)
        elif kind == "not-utf-8":
            path.write_bytes(b"\xff\xfe\x00 not text\n")
        return str(path)

    yield make
    for path, mode in made:
        path.chmod(mode)


def adapter_of(kind: str) -> str:
    return "opencode" if kind.startswith("opencode") else "claude-code"


# The tokens of the one good stream of the run (the stream of ticket a), in full and in the short form of the lines.
GOOD_TOKENS = {"claude-code": (19326, "19k"), "opencode": (84197, "84k")}


def a_run_with_a_bad_stream(repo: Path, make, kind: str, where: str) -> tuple[Run, str]:
    """Ticket a ended built after 600 seconds with a good stream. Ticket b has the bad stream. Ticket c is pending.

    `where` is `ended`: the agent of b ended, and b ended built at 630. `where` is `live`: the agent of b runs now, from 600.
    """
    adapter = adapter_of(kind)
    run = Run(repo, "r1", "a", "b", "c", adapter=adapter)
    good = claude("finished.jsonl") if adapter == "claude-code" else opencode("finished.jsonl")
    run.built(0, 600, "a", run.stream("a.stream.jsonl", good))
    stream = make(run, kind)
    if where == "ended":
        run.built(600, 630, "b", stream)
    else:
        run.step_start(600, "b")
        run.agent_start(600, "b", "specialist", stream)
    return run, adapter


# The stream of a live agent that is missing is a stream that the harness has not opened yet: the existing case
# `test_an_agent_whose_stream_has_no_event_yet_is_idle_since_the_journal_started_it` covers it.
BAD_STREAM_CASES = [
    pytest.param(kind, where, id=f"{where}-{kind}")
    for where in ("ended", "live") for kind in BAD_STREAMS if not (where == "live" and kind == "missing")
]


@pytest.mark.parametrize(("kind", "where"), BAD_STREAM_CASES)
def test_json_on_a_run_with_a_stream_that_cannot_be_read_shows_no_tokens_and_no_activity_for_that_agent(
    tmp_path, capsys, monkeypatch, bad_stream, kind, where
):
    repo = bare_repo(tmp_path)
    _, adapter = a_run_with_a_bad_stream(repo, bad_stream, kind, where)
    frozen(monkeypatch, 650)

    snap = snapshot(repo, capsys)

    good = GOOD_TOKENS[adapter][0]
    b = {"ended": ("built", None, 30), "live": ("running", "specialist", 50)}[where]
    assert ticket_of(snap, "b") == {"id": "b", "state": b[0], "phase": b[1], "elapsed_seconds": b[2], "tokens": None, "idle_seconds": None, "gate": None, "gate_idle_seconds": None}
    assert (ticket_of(snap, "a")["tokens"], snap["tokens"]) == (good, good)


@pytest.mark.parametrize(("kind", "where"), BAD_STREAM_CASES)
def test_plain_status_on_a_run_with_a_stream_that_cannot_be_read_shows_no_tokens_and_no_activity_for_that_agent(
    tmp_path, capsys, monkeypatch, bad_stream, kind, where
):
    repo = bare_repo(tmp_path)
    _, adapter = a_run_with_a_bad_stream(repo, bad_stream, kind, where)
    frozen(monkeypatch, 650)

    lines = plain_status(repo, capsys)

    good = GOOD_TOKENS[adapter][1]
    # Ended: a (600 s) and b (30 s) ended, so the ETA of c is their mean, 315 s. Live: a ended (600 s), and b used 50 s.
    expected = {
        "ended": (f"progress: 66%, 2 of 3 ended, 10m50s used, ETA 5m15s, {good} tokens", "  progress: 30s used"),
        "live": (f"progress: 33%, 1 of 3 ended, 10m50s used, ETA 19m10s, {good} tokens", "  progress: specialist, 50s used"),
    }[where]
    progress = [line for line in lines if line.startswith("  progress: ")]
    assert (lines[1], progress) == (expected[0], [f"  progress: 10m00s used, {good} tokens", expected[1]])


@pytest.mark.parametrize(("kind", "where"), BAD_STREAM_CASES)
def test_the_live_block_on_a_run_with_a_stream_that_cannot_be_read_shows_no_tokens_and_no_activity_for_that_agent(
    tmp_path, capsys, monkeypatch, bad_stream, kind, where
):
    repo = bare_repo(tmp_path)
    run, adapter = a_run_with_a_bad_stream(repo, bad_stream, kind, where)

    code, drawn, _ = follow(repo, monkeypatch, run, columns=200)

    good = GOOD_TOKENS[adapter][1]
    expected = {
        "ended": (f"run r1 running 66%, 2 of 3 ended, 10m50s used, ETA 5m15s, {good} tokens", "  b built, 30s used"),
        "live": (f"run r1 running 33%, 1 of 3 ended, 10m50s used, ETA 19m10s, {good} tokens", "  b running specialist, 50s used"),
    }[where]
    assert (code, capsys.readouterr().err) == (0, "")
    assert block_of(drawn[0])[:3] == [expected[0], f"  a built, 10m00s used, {good} tokens", expected[1]]


# The running gate. The engine writes `gate-start` with the log of the gate, and the gate runner appends to the log.
# A case sets the modification time of the log with `os.utime`, which is the real boundary of the clock for a file.

GATE = "nix develop -c python3 -m pytest -q"


def a_run_with_a_running_gate(repo: Path, phase: str = "build", log_changed: float | None = 200) -> tuple[Run, Path]:
    """The specialist of ticket a reported at 100, and its gate started at 110. Its log last changed at `log_changed`.

    With `log_changed` None, the log file is missing. Ticket b is pending.
    """
    run = Run(repo, "r1", "a", "b")
    run.step_start(0, "a")
    stream = run.stream("a.stream.jsonl", claude("finished.jsonl"))
    run.agent_start(0, "a", "specialist", stream)
    run.add(100, "adapter-result", ticket="a", exit_status=0, end_state="finished", session_id=None, event_stream=stream)
    run.add(100, "report-validation", ticket="a", valid=True, path="/r", reason=None, status="committed", blocked_reason=None)
    round_number = 0
    if phase == "fixup":
        round_number = 1
        run.add(105, "fixup-start", ticket="a", round=1, rejected_commit="3", findings_file="/f", agent="s", tier="strong", model="m")
    log = run.path.parent / "gates" / "a" / f"{phase}-{round_number}.log"
    if log_changed is not None:
        log.parent.mkdir(parents=True)
        log.write_text("....\n")
        changed = (START + timedelta(seconds=log_changed)).timestamp()
        os.utime(log, (changed, changed))
    run.add(110, "gate-start", ticket="a", round=round_number, phase=phase, command=GATE, gate_log=str(log))
    return run, log


@pytest.mark.parametrize(("phase", "shown"), [pytest.param("build", "gates", id="gates"), pytest.param("fixup", "fix-up", id="gates-of-a-fix-up")])
def test_json_gives_the_running_gate_and_the_seconds_since_its_log_changed(tmp_path, capsys, monkeypatch, phase, shown):
    repo = bare_repo(tmp_path)
    a_run_with_a_running_gate(repo, phase)
    frozen(monkeypatch, 230)

    snap = snapshot(repo, capsys)

    assert ticket_of(snap, "a") == {
        "id": "a", "state": "running", "phase": shown, "elapsed_seconds": 230, "tokens": 19326, "idle_seconds": None,
        "gate": GATE, "gate_idle_seconds": 30,
    }
    assert (ticket_of(snap, "b")["gate"], ticket_of(snap, "b")["gate_idle_seconds"]) == (None, None)


@pytest.mark.parametrize(
    "end",
    [
        pytest.param(("gate-result", {"round": 0, "phase": "build", "command": GATE, "commit": "3", "exit_status": 0, "green": True, "output_tail": ""}), id="gate-result"),
        pytest.param(("step-end", {"state": "failed", "reason": "crashed: OSError: no space"}), id="step-end"),
    ],
)
def test_a_gate_that_ended_is_not_the_running_gate(tmp_path, capsys, monkeypatch, end):
    repo = bare_repo(tmp_path)
    run, _ = a_run_with_a_running_gate(repo)
    name, fields = end
    run.add(220, name, ticket="a", **fields)
    frozen(monkeypatch, 230)

    a = ticket_of(snapshot(repo, capsys), "a")

    assert (a["gate"], a["gate_idle_seconds"]) == (None, None)


def test_a_gate_whose_log_is_missing_shows_the_gate_and_no_idle_seconds(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    a_run_with_a_running_gate(repo, log_changed=None)
    frozen(monkeypatch, 230)

    a = ticket_of(snapshot(repo, capsys), "a")

    assert (a["phase"], a["gate"], a["gate_idle_seconds"]) == ("gates", GATE, None)


def test_a_run_that_ended_has_no_running_gate(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run, _ = a_run_with_a_running_gate(repo)
    run.add(220, "run-end", result="failed", built=[], failed=[], skipped=[])
    frozen(monkeypatch, 9000)

    a = ticket_of(snapshot(repo, capsys), "a")

    assert (a["gate"], a["gate_idle_seconds"]) == (None, None)


def test_plain_status_shows_the_running_gate_and_the_time_since_its_log_changed(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    a_run_with_a_running_gate(repo)
    frozen(monkeypatch, 230)

    lines = plain_status(repo, capsys)

    assert [line for line in lines if line.startswith("  progress: ")] == [
        f"  progress: gates, 3m50s used, 19k tokens, gate {GATE}, gate log changed 30s ago",
    ]


def test_the_live_block_shows_the_running_gate_and_the_time_since_its_log_changed(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run, log = a_run_with_a_running_gate(repo)
    changed = (START + timedelta(seconds=590)).timestamp()
    os.utime(log, (changed, changed))

    code, drawn, _ = follow(repo, monkeypatch, run, columns=200)

    assert code == 0
    assert block_of(drawn[0])[1] == f"  a running gates, 10m50s used, 19k tokens, gate {GATE}, gate log changed 1m00s ago"


def test_the_running_gate_shows_for_a_run_whose_adapter_has_no_reader(tmp_path, capsys, monkeypatch, adapter_with_no_reader):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", adapter=adapter_with_no_reader)
    run.step_start(0, "a")
    log = run.path.parent / "gates" / "a" / "build-0.log"
    log.parent.mkdir(parents=True)
    log.write_text("")
    changed = (START + timedelta(seconds=20)).timestamp()
    os.utime(log, (changed, changed))
    run.add(10, "gate-start", ticket="a", round=0, phase="build", command=GATE, gate_log=str(log))
    frozen(monkeypatch, 50)

    a = ticket_of(snapshot(repo, capsys), "a")

    assert (a["phase"], a["tokens"], a["gate"], a["gate_idle_seconds"]) == ("gates", None, GATE, 30)
