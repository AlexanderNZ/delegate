"""`delegate status --json` and `delegate status --follow`: one snapshot of a run, with progress, ETA, tokens, and agent activity.

A case writes the journal of a run in the state directory of a real temporary
git repository, with the time of each event, as the engine writes it. The event
streams are the recorded fixtures of the harness adapters, or a part of one for
an agent that still runs. The clock (`time.time`) is the system boundary, so a
case fixes it, and `--follow` waits with `time.sleep`, which a case replaces.
"""

from __future__ import annotations

import io
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
from .support import bare_repo, journal_path

START = datetime(2026, 10, 16, 3, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> str:
    return (START + timedelta(seconds=seconds)).isoformat()


class Run:
    """The journal of one run, written line by line with the time of each event."""

    def __init__(self, repo: Path, run_id: str, *tickets: str, adapter: str = "claude-code") -> None:
        self.path = journal_path(repo, run_id)
        self.path.parent.mkdir(parents=True)
        self.streams = self.path.parent / "reports"
        self.streams.mkdir()
        self.seq = 0
        self.add(0, "run-start", run_id=run_id, workflow="/w/workflow.toml", mode="assure", adapter=adapter,
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
        "idle_seconds": int((now - last_line).total_seconds()),
    }
    assert ticket_of(snap, "b") == {"id": "b", "state": "pending", "phase": None, "elapsed_seconds": 0, "tokens": 0, "idle_seconds": None}
    assert (snap["run_id"], snap["state"], snap["elapsed_seconds"], snap["tokens"]) == ("r1", "running", 120, 25794)
    assert (snap["tickets_total"], snap["tickets_ended"], snap["percent"]) == (2, 0, 0)


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
    assert ticket_of(snap, "a") == {"id": "a", "state": "built", "phase": None, "elapsed_seconds": 600, "tokens": 19326, "idle_seconds": None}


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
    assert ticket_of(snap, "a") == {"id": "a", "state": "built", "phase": None, "elapsed_seconds": 600, "tokens": None, "idle_seconds": None}
    assert ticket_of(snap, "b") == {"id": "b", "state": "running", "phase": "specialist", "elapsed_seconds": 50, "tokens": None, "idle_seconds": None}


def test_plain_status_on_a_run_whose_adapter_has_no_reader_shows_the_progress_and_the_eta_and_no_tokens_and_no_activity(
    tmp_path, capsys, monkeypatch, adapter_with_no_reader
):
    repo = bare_repo(tmp_path)
    a_run_of_two_tickets(repo, adapter_with_no_reader)
    frozen(monkeypatch, 650)

    lines = plain_status(repo, capsys)

    assert lines[1] == "progress: 50%, 1 of 2 ended, 10m50s used, ETA 9m10s"
    assert [line for line in lines if line.startswith("  progress: ")] == ["  progress: 10m00s used", "  progress: specialist, 50s used"]


def test_follow_on_a_run_whose_adapter_has_no_reader_shows_the_progress_and_the_eta_and_no_tokens_and_no_activity(
    tmp_path, monkeypatch, adapter_with_no_reader
):
    repo = bare_repo(tmp_path)
    run = a_run_of_two_tickets(repo, adapter_with_no_reader)

    code, out, _ = follow(repo, monkeypatch, run, terminal=False, columns=200)

    assert code == 0
    assert out.splitlines()[:3] == [
        "run r1 running 50%, 1 of 2 ended, 10m50s used, ETA 9m10s",
        "  a built, 10m00s used",
        "  b running specialist, 50s used",
    ]


class Terminal(io.StringIO):
    """The terminal of a person: the system boundary of `--follow`."""

    def isatty(self) -> bool:
        return True


def a_live_run(repo: Path) -> Run:
    run = Run(repo, "20261016T030000Z-abcd", "first-ticket-with-a-long-id", "b")
    capped = claude("capped.jsonl")  # it holds tool calls, which the view must not show
    run.built(0, 600, "first-ticket-with-a-long-id", run.stream("a.stream.jsonl", capped))
    run.step_start(600, "b")
    run.agent_start(600, "b", "specialist", run.stream("b.stream.jsonl", capped))
    return run


def follow(repo: Path, monkeypatch, run: Run, *extra: str, terminal: bool, columns: int = 60) -> tuple[int, str, list[float]]:
    """Run `status --follow` while the run goes on. The first wait ends the ticket b and the run; a later wait fails the case."""
    out = Terminal() if terminal else io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", str(columns))
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        assert not waits, "status --follow did not exit when the run ended"
        waits.append(seconds)
        run.add(700, "step-end", ticket="b", state="built", reason=None)
        run.add(701, "run-end", result="built", built=["first-ticket-with-a-long-id", "b"], failed=[], skipped=[])

    monkeypatch.setattr(time, "sleep", sleep)
    frozen(monkeypatch, 650)
    code = delegate.main(["status", "--repo", str(repo), "--follow", *extra])
    return code, out.getvalue(), waits


TOOL_NAMES = ("Bash", "tool_use", "echo one")
CURSOR = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def test_follow_on_a_terminal_draws_a_line_for_the_run_and_one_for_each_ticket_and_redraws_them_in_place(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = a_live_run(repo)

    code, out, waits = follow(repo, monkeypatch, run, "--interval", "3", terminal=True)

    assert (code, waits) == (0, [3.0])
    first, second = out.split("\x1b[3A")  # the cursor goes up the three lines of the first snapshot
    first_lines, second_lines = first.splitlines(), CURSOR.sub("", second).splitlines()
    assert len(first_lines) == len(second_lines) == 3
    assert first_lines[0].startswith("run 20261016T030000Z-abcd running 50%")
    assert second_lines[0].startswith("run 20261016T030000Z-abcd built 100%")
    assert "b" in second_lines[2] and "built" in second_lines[2]


@pytest.mark.parametrize("columns", [30, 60, 200])
def test_follow_draws_no_line_wider_than_the_terminal_no_tool_call_and_no_absolute_path(tmp_path, monkeypatch, columns):
    repo = bare_repo(tmp_path)
    run = a_live_run(repo)

    code, out, _ = follow(repo, monkeypatch, run, terminal=True, columns=columns)

    assert code == 0
    for line in CURSOR.sub("", out).splitlines():
        assert len(line) <= columns, line
        assert not any(name in line for name in TOOL_NAMES), line
        assert not re.search(r"(^|\s)/", line), line
    assert str(tmp_path) not in out


def test_follow_to_a_pipe_prints_each_snapshot_as_plain_lines_with_no_cursor_control(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = a_live_run(repo)

    code, out, _ = follow(repo, monkeypatch, run, terminal=False)

    assert code == 0
    assert "\x1b" not in out
    lines = out.splitlines()
    assert [line.split()[2] for line in lines if line.startswith("run ")] == ["running", "built"]
    assert len(lines) == 6


def test_follow_of_a_run_that_ended_draws_it_once_and_exits_with_no_wait(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = a_live_run(repo)
    run.add(640, "step-end", ticket="b", state="failed", reason="r")
    run.add(641, "run-end", result="failed", built=["first-ticket-with-a-long-id"], failed=["b"], skipped=[])

    code, out, waits = follow(repo, monkeypatch, run, terminal=False)

    assert (code, waits) == (0, [])
    assert out.splitlines()[0].startswith("run 20261016T030000Z-abcd failed 100%")


def test_json_and_follow_together_are_a_usage_error(tmp_path, capsys):
    repo = bare_repo(tmp_path)

    with pytest.raises(SystemExit) as stopped:
        delegate.main(["status", "--repo", str(repo), "--json", "--follow"])

    assert stopped.value.code == 2
    assert "--follow" in capsys.readouterr().err


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
    assert ticket_of(snap, "b") == {"id": "b", "state": b[0], "phase": b[1], "elapsed_seconds": b[2], "tokens": None, "idle_seconds": None}
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
def test_follow_on_a_run_with_a_stream_that_cannot_be_read_shows_no_tokens_and_no_activity_for_that_agent(
    tmp_path, capsys, monkeypatch, bad_stream, kind, where
):
    repo = bare_repo(tmp_path)
    run, adapter = a_run_with_a_bad_stream(repo, bad_stream, kind, where)

    code, out, _ = follow(repo, monkeypatch, run, terminal=False, columns=200)

    good = GOOD_TOKENS[adapter][1]
    expected = {
        "ended": (f"run r1 running 66%, 2 of 3 ended, 10m50s used, ETA 5m15s, {good} tokens", "  b built, 30s used"),
        "live": (f"run r1 running 33%, 1 of 3 ended, 10m50s used, ETA 19m10s, {good} tokens", "  b running specialist, 50s used"),
    }[where]
    assert (code, capsys.readouterr().err) == (0, "")
    assert out.splitlines()[:3] == [expected[0], f"  a built, 10m00s used, {good} tokens", expected[1]]
