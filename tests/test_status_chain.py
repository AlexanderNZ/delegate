"""The chain row of an `economy` run in `delegate status` and in the live view of `delegate watch` (#196).

A case writes the journal of an `economy` run in the state directory of a real temporary git repository: five tickets
that are built, and the verification of the chain that follows. The clock (the `clock` fixture) is the system boundary, so a
case fixes it.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from delegate.cli import delegate

from .support import Terminal, bare_repo, fake_clock, frames
from .test_status_snapshot import START, Run, claude, frozen, snapshot, utf8_locale  # noqa: F401 - the fixture is autouse

# The commands of each case use the fake clock of the case (#297): its helpers set it.
pytestmark = pytest.mark.usefixtures("clock")

TICKETS = ("a", "b", "c", "d", "e")
CHAIN_GATE = "nix develop -c python3 -m pytest -q"


def an_economy_run_of_five_built_tickets(repo: Path, ticket_seconds: int = 600) -> Run:
    """Five tickets, each built after `ticket_seconds`, one after the other. The verification of the chain follows."""
    run = Run(repo, "r1", *TICKETS, mode="economy")
    finished = claude("finished.jsonl")
    for position, ticket in enumerate(TICKETS):
        run.built(position * ticket_seconds, (position + 1) * ticket_seconds, ticket, run.stream(f"{ticket}.stream.jsonl", finished))
    return run


def chain_gate_starts(run: Run, seconds: float, ticket: str = "e", round_number: int = 0) -> None:
    run.add(seconds, "gate-start", ticket=ticket, round=round_number, phase="chain", command=CHAIN_GATE, gate_log=str(run.streams / "chain-gate.log"))


def chain_verifier_starts(run: Run, seconds: float, ticket: str = "e") -> None:
    run.add(seconds, "verify-start", ticket=ticket, round=0, agent="v", tier="verifier", model="m", commit="3", copy="/c",
            stack="python", tickets=list(TICKETS))
    run.agent_start(seconds, ticket, "verifier", run.stream("chain.0.stream.jsonl", claude("finished.jsonl")))


def chain_is_accepted(run: Run, seconds: float) -> None:
    run.add(seconds, "verdict", ticket="e", round=0, mode="full", verdict="ACCEPT", findings=[], unverified=[], report="/r",
            stack="python", tickets=list(TICKETS), mapped={}, unmapped=[])


def status_lines(repo: Path, capsys, *extra: str) -> list[str]:
    code = delegate.main(["status", "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    assert (code, captured.err) == (0, ""), captured.err
    return captured.out.splitlines()


def watch_block(repo: Path, monkeypatch, run: Run) -> list[str]:
    """The live block of the first frame of `watch` on a terminal. The first wait ends the run."""
    out = Terminal()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", "200")
    fake_clock().on_sleep = lambda seconds: run.add(99999, "run-end", result="built", built=list(TICKETS), failed=[], skipped=[])
    assert delegate.main(["watch", run.path.parent.name, "--repo", str(repo)]) == 0
    frame = frames(out.getvalue())[0]
    above = next((frame[:index] for index, line in enumerate(frame) if line and set(line) == {"─"}), frame)
    return [line for line in above if line]


def chain_row(lines: list[str]) -> str:
    """The one row of the stack python in a table: the ticket rows hold no stack name."""
    (row,) = [line for line in lines if "python" in line and "run/demo" not in line]
    return row


def test_a_chain_gate_that_runs_for_30_minutes_shows_as_a_chain_row_with_its_phase_its_time_and_its_command(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_run_of_five_built_tickets(repo)
    chain_gate_starts(run, 3000)
    (run.streams / "chain-gate.log").write_text("collected 1 item\n")
    frozen(monkeypatch, 4800)
    os.utime(run.streams / "chain-gate.log", ((START + timedelta(seconds=4740)).timestamp(),) * 2)

    snap = snapshot(repo, capsys)
    status = status_lines(repo, capsys)
    watch = watch_block(repo, monkeypatch, run)

    assert snap["chains"] == [{
        "stack": "python", "phase": "gates", "state": "running", "elapsed_seconds": 1800, "gate_command": CHAIN_GATE,
        "gate_idle_seconds": 60, "tokens_new": 0, "tokens_cache": 0, "verdict": None, "fixup_round": 0,
    }]
    # The row goes after the last ticket row of its stack. It shows the stack, the phase and the time used.
    row = chain_row(status)
    assert status.index(row) == len(status) - 1
    assert "gates ●" in row and "30m00s" in row and "running" in row
    live = chain_row(watch)
    assert "gates ●" in live and "30m00s" in live
    assert "gate pytest" in live and "log 1m00s ago" in live
    assert watch.index(live) == len(watch) - 1


def test_the_eta_of_a_run_whose_chain_ran_past_the_mean_ticket_is_a_dash_and_not_0s(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_run_of_five_built_tickets(repo)
    chain_gate_starts(run, 3000)
    frozen(monkeypatch, 4800)

    snap = snapshot(repo, capsys)
    status = status_lines(repo, capsys)
    watch = watch_block(repo, monkeypatch, run)

    assert snap["eta"] is None
    assert ", ETA -," in status[1] and "ETA 0s" not in status[1]
    assert "ETA -" in watch[0] and "ETA 0s" not in watch[0]


def test_a_running_chain_counts_by_its_phase_in_the_percentage(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_run_of_five_built_tickets(repo)
    frozen(monkeypatch, 3010)
    before = snapshot(repo, capsys)["percent"]
    chain_gate_starts(run, 3000)

    after = snapshot(repo, capsys)["percent"]

    # 5 of 6 units ended: 83. The phase gates counts 6 tenths of the sixth unit: 5.6 of 6 units, 93.
    assert (before, after) == (83, 93)


def test_the_verifier_of_the_chain_shows_its_phase_and_the_tokens_of_its_agent(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_run_of_five_built_tickets(repo)
    chain_gate_starts(run, 3000)
    run.add(3050, "gate-result", ticket="e", round=0, phase="chain", command=CHAIN_GATE, commit="3", exit_status=0, green=True, timed_out=False,
            output_tail="", clean=True)
    chain_verifier_starts(run, 3100)
    frozen(monkeypatch, 3200)

    (chain,) = snapshot(repo, capsys)["chains"]

    assert (chain["phase"], chain["state"], chain["elapsed_seconds"], chain["tokens_new"], chain["tokens_cache"]) == ("verifier", "running", 200, 19326, 0)
    assert chain["gate_command"] is None and chain["gate_idle_seconds"] is None
    assert "verifier ●" in chain_row(status_lines(repo, capsys))
    live = chain_row(watch_block(repo, monkeypatch, run))
    assert "verifier ●" in live and "19k" in live


def test_an_accepted_chain_shows_its_state_and_its_verdict(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_run_of_five_built_tickets(repo)
    chain_gate_starts(run, 3000)
    chain_verifier_starts(run, 3100)
    chain_is_accepted(run, 3400)
    frozen(monkeypatch, 3500)

    (chain,) = snapshot(repo, capsys)["chains"]
    row = chain_row(status_lines(repo, capsys))

    assert (chain["state"], chain["verdict"], chain["phase"], chain["elapsed_seconds"]) == ("accepted", "ACCEPT", None, 400)
    assert "accepted" in row and "ACCEPT" in row


def test_a_chain_that_a_reject_ended_shows_the_state_rejected_and_the_verdict(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_run_of_five_built_tickets(repo)
    chain_gate_starts(run, 3000)
    chain_verifier_starts(run, 3100)
    run.add(3400, "verdict", ticket="e", round=0, mode="full", verdict="REJECT", findings=["a: x"], unverified=[], report="/r",
            stack="python", tickets=list(TICKETS), mapped={"a": ["a: x"]}, unmapped=[])
    run.add(3401, "step-end", ticket="a", state="failed", reason="rejected: a: x")
    frozen(monkeypatch, 3500)

    (chain,) = snapshot(repo, capsys)["chains"]

    assert (chain["state"], chain["verdict"]) == ("rejected", "REJECT")


def test_a_reject_that_a_fix_up_follows_keeps_the_chain_running_in_the_phase_fix_up(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_run_of_five_built_tickets(repo)
    chain_gate_starts(run, 3000)
    chain_verifier_starts(run, 3100)
    run.add(3400, "verdict", ticket="e", round=0, mode="full", verdict="REJECT", findings=["a: x"], unverified=[], report="/r",
            stack="python", tickets=list(TICKETS), mapped={"a": ["a: x"]}, unmapped=[])
    run.add(3401, "fixup-start", ticket="e", round=1, stack="python", rejected_commit="3", findings_file="/f", agent="s", tier="standard", model="m")
    frozen(monkeypatch, 3500)

    (chain,) = snapshot(repo, capsys)["chains"]

    assert (chain["state"], chain["phase"], chain["verdict"]) == ("running", "fix-up", None)


def test_a_run_in_assure_mode_has_no_chain_row(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a")
    run.built(0, 600, "a", run.stream("a.stream.jsonl", claude("finished.jsonl")))
    run.add(601, "gate-start", ticket="a", round=0, phase="chain", command=CHAIN_GATE, gate_log=str(run.streams / "g.log"))
    frozen(monkeypatch, 700)

    snap = snapshot(repo, capsys)
    status = status_lines(repo, capsys)

    assert snap["chains"] == []
    assert [line for line in status if "python" in line] == []


def test_a_stack_whose_verification_did_not_start_has_no_chain_row(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    an_economy_run_of_five_built_tickets(repo)
    frozen(monkeypatch, 3010)

    assert snapshot(repo, capsys)["chains"] == []


def test_the_chain_row_of_each_stack_goes_after_the_last_ticket_of_its_stack(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", "b", "c", mode="economy")
    for position, (ticket, stack) in enumerate((("a", "python"), ("b", "docs"), ("c", "python"))):
        run.add(position * 100, "step-start", ticket=ticket, stack=stack, branch=f"run/demo-{ticket}", worktree=f"/w/{ticket}",
                base_commit="0" * 40, agent="x", tier="strong", model="m")
        run.add(position * 100 + 90, "step-end", ticket=ticket, state="built", reason=None)
    run.add(300, "gate-start", ticket="c", round=0, phase="chain", command=CHAIN_GATE, gate_log=str(run.streams / "g.log"))
    frozen(monkeypatch, 400)

    snap = snapshot(repo, capsys)
    status = status_lines(repo, capsys)

    assert [c["stack"] for c in snap["chains"]] == ["python"]
    # Ticket c is the last ticket of the stack python, so the row goes under it and not under b.
    assert status[-1] == chain_row(status), "\n".join(status)
    tickets = [line.split()[2] for line in status if line.split()[:1] in (["1"], ["2"], ["3"])]
    assert tickets == ["a", "b", "c"]
    assert status.index(chain_row(status)) == status.index(next(line for line in status if line.split()[:1] == ["3"])) + 1


def test_the_chain_row_stays_under_the_last_ticket_when_the_rows_in_the_middle_fold(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    ids = [f"t{number:02d}" for number in range(1, 13)]
    run = Run(repo, "r1", *ids, mode="economy")
    for position, ticket in enumerate(ids):
        run.built(position * 10, position * 10 + 9, ticket, run.stream(f"{ticket}.stream.jsonl", claude("finished.jsonl")))
    run.add(200, "gate-start", ticket=ids[-1], round=0, phase="chain", command=CHAIN_GATE, gate_log=str(run.streams / "chain-gate.log"))
    frozen(monkeypatch, 300)
    out = Terminal()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("LINES", "20")
    fake_clock().on_sleep = lambda seconds: run.add(99999, "run-end", result="built", built=ids, failed=[], skipped=[])

    assert delegate.main(["watch", run.path.parent.name, "--repo", str(repo)]) == 0

    block = frames(out.getvalue())[0]
    rows = [line for line in block if line.strip() and "─" not in line]
    assert any("t12" in line for line in rows), "\n".join(block)
    last_ticket = next(index for index, line in enumerate(rows) if "t12" in line)
    assert "gates ●" in rows[last_ticket + 1] and "python" in rows[last_ticket + 1]
    assert any("folded" in line for line in rows)


def test_the_json_of_a_chain_holds_exactly_the_fields_of_the_row(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_run_of_five_built_tickets(repo)
    chain_gate_starts(run, 3000)
    frozen(monkeypatch, 3100)

    (chain,) = json.loads(json.dumps(snapshot(repo, capsys)["chains"]))

    assert sorted(chain) == sorted([
        "stack", "phase", "state", "elapsed_seconds", "gate_command", "gate_idle_seconds", "tokens_new", "tokens_cache", "verdict",
        "fixup_round",
    ])


def a_chain_that_a_fix_up_round_stopped(run: Run) -> None:
    """The chain gates, the verifier, a REJECT and the fix-up of round 1, which the agent check stopped (#209).

    The stop fails the ticket that ends the stack. The coordinator then retries it at the phase `chain-fixup`.
    """
    chain_gate_starts(run, 3000)
    chain_verifier_starts(run, 3100)
    run.add(3400, "verdict", ticket="e", round=0, mode="full", verdict="REJECT", findings=["a: x"], unverified=[], report="/r",
            stack="python", tickets=list(TICKETS), mapped={"a": ["a: x"]}, unmapped=[])
    run.add(3401, "fixup-start", ticket="e", round=1, stack="python", rejected_commit="3", findings_file="/f", agent="s", tier="standard", model="m")
    run.add(3402, "step-end", ticket="e", state="failed", reason="the agents changed")


def the_retry(run: Run, seconds: float, phase: str) -> None:
    run.add(seconds, "retry", ticket="e", phase=phase, round=1, reason="the agents changed", revived=[])


def test_after_a_retry_at_the_chain_fix_up_the_chain_keeps_its_round_its_phase_and_its_time(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_run_of_five_built_tickets(repo)
    a_chain_that_a_fix_up_round_stopped(run)
    the_retry(run, 3500, "chain-fixup")
    chain_gate_starts(run, 3600, round_number=1)
    frozen(monkeypatch, 3700)

    (chain,) = snapshot(repo, capsys)["chains"]
    row = chain_row(status_lines(repo, capsys))
    live = chain_row(watch_block(repo, monkeypatch, run))

    # The engine counts the fix-up of round 1 since the last retry that is no `chain-fixup`: the retry does not end the chain.
    assert (chain["state"], chain["phase"], chain["fixup_round"], chain["elapsed_seconds"]) == ("running", "gates", 1, 700)
    assert "gates ●" in row and "1/2" in row and "11m40s" in row
    assert "gates ●" in live and "1/2" in live and "11m40s" in live


def test_right_after_a_retry_at_the_chain_fix_up_the_chain_runs_in_the_phase_fix_up_and_not_as_rejected(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_run_of_five_built_tickets(repo)
    a_chain_that_a_fix_up_round_stopped(run)
    the_retry(run, 3500, "chain-fixup")
    frozen(monkeypatch, 3700)

    (chain,) = snapshot(repo, capsys)["chains"]

    assert (chain["state"], chain["phase"], chain["fixup_round"], chain["verdict"], chain["elapsed_seconds"]) == ("running", "fix-up", 1, None, 700)


def test_after_a_retry_at_the_chain_the_chain_starts_again_with_round_0(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_run_of_five_built_tickets(repo)
    a_chain_that_a_fix_up_round_stopped(run)
    the_retry(run, 3500, "chain")
    chain_gate_starts(run, 3600)
    frozen(monkeypatch, 3700)

    (chain,) = snapshot(repo, capsys)["chains"]

    assert (chain["state"], chain["phase"], chain["fixup_round"], chain["elapsed_seconds"]) == ("running", "gates", 0, 100)


def test_the_table_of_a_chain_with_a_fix_up_round_and_no_retry_shows_the_round(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = an_economy_run_of_five_built_tickets(repo)
    chain_gate_starts(run, 3000)
    chain_verifier_starts(run, 3100)
    run.add(3400, "verdict", ticket="e", round=0, mode="full", verdict="REJECT", findings=["a: x"], unverified=[], report="/r",
            stack="python", tickets=list(TICKETS), mapped={"a": ["a: x"]}, unmapped=[])
    run.add(3401, "fixup-start", ticket="e", round=1, stack="python", rejected_commit="3", findings_file="/f", agent="s", tier="standard", model="m")
    chain_gate_starts(run, 3500, round_number=1)
    frozen(monkeypatch, 3700)

    (chain,) = snapshot(repo, capsys)["chains"]
    row = chain_row(status_lines(repo, capsys))

    assert (chain["state"], chain["phase"], chain["fixup_round"], chain["elapsed_seconds"]) == ("running", "gates", 1, 700)
    assert "gates ●" in row and "1/2" in row and "11m40s" in row


def a_run_whose_first_pass_of_the_chain_was_rejected_and_whose_retry_runs_the_verifier(run: Run) -> None:
    """Two stacks: ticket a is the only ticket of docs, ticket b is the only ticket of python and the tip of the chain.

    The chain verifier rejected the first stack and the run ended failed. A resume and a retry of the tip with `--stack-changed`
    revived the two tickets. The chain gates ran green, then the verifier of the new pass started on ticket a, the last ticket
    of the first stack.
    """
    for ticket, stack, start in (("a", "docs", 0), ("b", "python", 100)):
        run.add(start, "step-start", ticket=ticket, stack=stack, branch=f"run/demo-{ticket}", worktree=f"/w/{ticket}",
                base_commit="0" * 40, agent="x", tier="strong", model="m")
        run.add(start + 100, "step-end", ticket=ticket, state="built", reason=None)
    chain_gate_starts(run, 300, ticket="a")
    run.add(310, "gate-result", ticket="a", round=0, phase="chain", command=CHAIN_GATE, commit="3", exit_status=0, green=True, output_tail="")
    run.add(320, "verify-start", ticket="a", round=0, agent="v", tier="verifier", model="m", commit="3", copy="/c", stack="docs", tickets=["a"])
    run.agent_start(320, "a", "verifier", run.stream("first.stream.jsonl", claude("finished.jsonl")))
    run.add(400, "adapter-result", ticket="a", exit_status=0, end_state="finished", session_id=None, event_stream=str(run.streams / "first.stream.jsonl"))
    run.add(401, "verdict", ticket="a", round=0, mode="full", verdict="REJECT", findings=["a: x"], unverified=[], report="/r",
            stack="docs", tickets=["a"], mapped={"a": ["a: x"]}, unmapped=[])
    run.add(402, "step-end", ticket="a", state="failed", reason="rejected: a: x")
    run.add(403, "run-end", result="failed", built=["b"], failed=["a"], skipped=[])
    run.add(1000, "resume", run_id="r1", built=["b"], failed=["a"], skipped={}, open="a")
    run.add(1001, "retry", ticket="b", phase="chain", round=0, reason="the stack changed", revived=["a", "b"])
    run.add(1001, "step-end", ticket="a", state="built", reason=None)
    run.add(1001, "step-end", ticket="b", state="built", reason=None)
    chain_gate_starts(run, 1100, ticket="a")
    run.add(1110, "gate-result", ticket="a", round=0, phase="chain", command=CHAIN_GATE, commit="3", exit_status=0, green=True, output_tail="")
    run.add(1120, "verify-start", ticket="a", round=0, agent="v", tier="verifier", model="m", commit="3", copy="/c", stack="docs", tickets=["a"])
    run.agent_start(1120, "a", "verifier", run.stream("second.stream.jsonl", ""))


def test_after_a_retry_of_the_tip_the_chain_row_shows_the_new_pass_and_not_the_rejected_first_pass(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", "b", mode="economy")
    a_run_whose_first_pass_of_the_chain_was_rejected_and_whose_retry_runs_the_verifier(run)
    frozen(monkeypatch, 1300)

    (chain,) = snapshot(repo, capsys)["chains"]
    row = chain_row_of(status_lines(repo, capsys), "docs")

    # The new pass started at the first chain gate after the retry (1100 s), 200 s before the clock.
    assert (chain["stack"], chain["state"], chain["phase"], chain["verdict"], chain["elapsed_seconds"]) == ("docs", "running", "verifier", None, 200)
    assert (chain["tokens_new"], chain["tokens_cache"]) == (0, 0)
    assert "rejected" not in row and "verifier" in row


def test_after_a_retry_of_the_tip_the_eta_counts_the_chain_from_its_first_event_after_the_retry(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", "b", mode="economy")
    a_run_whose_first_pass_of_the_chain_was_rejected_and_whose_retry_runs_the_verifier(run)
    frozen(monkeypatch, 1300)

    snap = snapshot(repo, capsys)

    # The steps of the tickets took 100 s (a) and 901 s (b), so the mean is 500.5 s. One unit is left, the chain, which ran 200 s.
    assert snap["eta"] is not None
    assert datetime.fromisoformat(snap["eta"]) == START + timedelta(seconds=1300 + 300)


def test_after_a_retry_of_the_tip_no_ticket_shows_the_verdict_of_the_first_pass(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", "b", mode="economy")
    a_run_whose_first_pass_of_the_chain_was_rejected_and_whose_retry_runs_the_verifier(run)
    frozen(monkeypatch, 1300)

    status = status_lines(repo, capsys)
    rows = [line for line in status if line.split()[:1] in (["1"], ["2"])]

    assert [line.split()[2] for line in rows] == ["a", "b"]
    assert "REJECT" not in "\n".join(status)


def chain_row_of(lines: list[str], stack: str) -> str:
    (row,) = [line for line in lines if stack in line and "run/demo" not in line and line.split()[:1] not in (["1"], ["2"])]
    return row
