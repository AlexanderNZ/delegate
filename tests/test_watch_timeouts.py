"""`delegate status` and `delegate watch` name a gate and an agent session that ran past their time limit (#179).

A case writes the journal of a run, as `test_watch_live` does, or runs the engine with a gate that hangs, and then reads
the run through `delegate.main`. The raw lines and the problem lines are the interface that a script reads.
"""

from __future__ import annotations

import json
import time

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, WORKFLOW, append_events, bare_repo, frames, make_repo, read_journal, write_journal
from .support import run_end, run_start, step_end, step_start
from .test_watch_live import PYTEST, Run, at, local_zone, watch  # noqa: F401 - a fixture, used by name

POLL = ["--poll-seconds", "0.02"]
PROBLEM = 3


def timeout_event(role: str = "specialist", seconds: float = 600.0) -> tuple[str, dict[str, object]]:
    return "agent-timeout", {"ticket": "a", "role": role, "round": 0, "seconds": seconds}


def agent_events(role: str = "specialist", seconds: float = 600.0) -> list[tuple[str, dict[str, object]]]:
    """The events of an agent that the adapter ended at the agent timeout: the start, the timeout, and a failed result."""
    return [
        ("agent-start", {"ticket": "a", "role": role, "round": 0, "event_stream": "/w/s.jsonl"}),
        timeout_event(role, seconds),
        ("adapter-result", {"ticket": "a", "exit_status": -15, "end_state": "failed", "session_id": None, "event_stream": "/w/s.jsonl"}),
    ]


def test_a_specialist_session_that_ran_past_the_agent_timeout_is_a_problem_that_names_the_time_limit(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), agent_events()[0])
    # The timeout and the failed result arrive after the watch starts (#222: a replay takes a problem that a later event follows as history).
    monkeypatch.setattr(time, "sleep", lambda seconds: append_events(path, *agent_events()[1:]))

    code = delegate.main(["watch", "r1", "--repo", str(repo), *POLL])
    out = capsys.readouterr().out

    assert code == PROBLEM
    problem = [line for line in out.splitlines() if line.startswith("watch: problem")]
    # The timeout is the first problem, and it names the limit. The failed result that follows adds no second problem.
    assert problem == ["watch: problem: ticket a: the specialist session ran past the agent-timeout of 10 minutes"]
    assert out.splitlines()[-1] == "position 4"


def test_the_agent_timeout_names_the_role_and_a_fraction_of_a_minute(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), *agent_events("verifier", 90.0)[:2])

    delegate.main(["watch", "r1", "--repo", str(repo), *POLL])

    assert "watch: problem: ticket a: the verifier session ran past the agent-timeout of 1.5 minutes" in capsys.readouterr().out


def test_a_timed_out_session_that_a_continuation_goes_past_is_history_and_the_run_exits_with_its_result(tmp_path, capsys):
    continuation = ("continuation", {
        "ticket": "a", "count": 1, "limit": 2, "trigger": "failed", "mode": "brief", "resume_session": None, "commits": [],
        "reason": "the specialist session ran past the agent-timeout of 10 minutes",
    })
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), *agent_events(), continuation, step_end("a"), run_end("built", ["a"]))

    code = delegate.main(["watch", "r1", "--repo", str(repo), *POLL])
    out = capsys.readouterr().out

    assert code == 0
    assert "watch: problem" not in out


def gate_events(timed_out: bool) -> list[tuple[str, dict[str, object]]]:
    return [
        ("gate-start", {"ticket": "a", "round": 0, "phase": "build", "command": "sleep 30", "gate_log": "/w/g.log"}),
        ("gate-result", {
            "ticket": "a", "round": 0, "phase": "build", "command": "sleep 30", "commit": "1" * 40, "exit_status": -15,
            "green": False, "timed_out": timed_out, "output_tail": "", "clean": True,
        }),
    ]


@pytest.mark.parametrize(("timed_out", "label"), [(True, "sleep 30 (timed out)"), (False, "sleep 30")])
def test_a_step_that_ends_with_a_timed_out_gate_names_the_gate_as_timed_out(tmp_path, capsys, timed_out, label):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), *gate_events(timed_out), step_end("a", "failed", "gates red"))

    code = delegate.main(["watch", "r1", "--repo", str(repo), *POLL])
    out = capsys.readouterr().out

    assert code == PROBLEM
    assert f"watch: problem: ticket a: the gates are not green: {label}" in out.splitlines()


def test_a_journal_of_an_earlier_version_with_no_timed_out_field_reads_as_before(tmp_path, capsys):
    events = gate_events(False)
    del events[1][1]["timed_out"]
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), *events, step_end("a", "failed", "gates red"))

    code = delegate.main(["watch", "r1", "--repo", str(repo), *POLL])

    assert code == PROBLEM
    assert "watch: problem: ticket a: the gates are not green: sleep 30\n" in capsys.readouterr().out


def add_timeout(run: Run, when: float, role: str, limit: float) -> None:
    """Write `agent-timeout` to the journal of `run`. `Run.add` takes `seconds` as the time, so the field needs a line of its own."""
    run.seq += 1
    event = {"seq": run.seq, "time": at(when), "event": "agent-timeout", "ticket": "a", "role": role, "round": 0, "seconds": limit}
    with run.path.open("a") as handle:
        handle.write(json.dumps(event) + "\n")


def test_the_live_feed_gives_the_problem_line_of_a_session_that_timed_out_and_names_the_limit(tmp_path, monkeypatch, local_zone):
    local_zone("NZT-13")
    repo = bare_repo(tmp_path)
    run = Run(repo, "a")
    run.step_start(0, "a")
    run.add(0, "agent-start", ticket="a", role="specialist", round=0, event_stream=run.stream("a.specialist.stream.jsonl"))
    add_timeout(run, 600, "specialist", 600.0)
    run.add(600, "adapter-result", ticket="a", exit_status=-15, end_state="failed", session_id="s", event_stream=run.stream("b.stream.jsonl"))

    # The problem does not end the live view (#222): the run ends, and the first frame has the warning line.
    code, out = watch(repo, monkeypatch, then=lambda: run.add(601, "run-end", result="failed", built=[], failed=["a"], skipped=[]))

    assert code == 1
    last = frames(out)[0]
    assert last[last.index("─" * 200) + 1:] == [
        "16:00  #a   started · python-specialist",
        "16:10  ✗ #a the specialist session ran past the agent-timeout of 10 minutes",
        "16:10  #a   specialist timed out · 10m · tokens 19k new, 0 cache",
    ]


def test_the_result_line_of_a_session_that_timed_out_says_timed_out_in_the_place_of_the_end_state(tmp_path, monkeypatch, local_zone):
    # A continuation goes past the problem, so the watch goes on to the end of the run and the feed keeps the result line.
    local_zone("NZT-13")
    repo = bare_repo(tmp_path)
    run = Run(repo, "a")
    run.step_start(0, "a")
    run.add(0, "agent-start", ticket="a", role="specialist", round=0, event_stream=run.stream("a.specialist.stream.jsonl"))
    add_timeout(run, 600, "specialist", 600.0)
    run.add(600, "adapter-result", ticket="a", exit_status=-15, end_state="failed", session_id="s", event_stream=run.stream("b.stream.jsonl"))
    run.add(600, "continuation", ticket="a", count=1, limit=2, trigger="failed", mode="resume", resume_session="s", commits=[], reason="r")
    run.add(600, "agent-start", ticket="a", role="specialist", round=0, event_stream=run.stream("c.stream.jsonl"))
    run.add(660, "adapter-result", ticket="a", exit_status=0, end_state="finished", session_id="s", event_stream=run.stream("d.stream.jsonl"))
    run.report(660, "a")
    run.add(660, "step-end", ticket="a", state="built", reason=None)
    run.add(660, "run-end", result="built", built=["a"], failed=[], skipped=[])

    code, out = watch(repo, monkeypatch)

    assert code == 0
    (last,) = frames(out)
    feed = last[last.index("─" * 200) + 1:]
    assert "16:10  #a   specialist timed out · 10m · tokens 19k new, 0 cache" in feed
    assert not any("specialist failed" in line for line in feed)


def test_the_live_feed_gives_a_timed_out_gate_the_words_timed_out_in_the_place_of_red(tmp_path, monkeypatch, local_zone):
    local_zone("NZT-13")
    repo = bare_repo(tmp_path)
    run = Run(repo, "a")
    run.step_start(0, "a")
    run.add(0, "gate-start", ticket="a", round=0, phase="build", command=PYTEST, gate_log=str(run.dir / "g.log"))
    run.add(180, "gate-result", ticket="a", round=0, phase="build", command=PYTEST, commit="1" * 40, exit_status=-15, green=False,
            timed_out=True, output_tail="", clean=True)
    run.add(180, "gate-start", ticket="a", round=0, phase="build", command="ruff check", gate_log=str(run.dir / "g.log"))
    run.add(182, "gate-result", ticket="a", round=0, phase="build", command="ruff check", commit="1" * 40, exit_status=1, green=False,
            timed_out=False, output_tail="", clean=True)
    run.add(182, "step-end", ticket="a", state="failed", reason=f"gates red: {PYTEST} (timed out); ruff check")

    code, out = watch(repo, monkeypatch, then=lambda: run.add(183, "run-end", result="failed", built=[], failed=["a"], skipped=[]))

    assert code == 1  # the problem does not end the live view (#222)
    last = frames(out)[0]
    assert last[last.index("─" * 200) + 1:] == [
        "16:00  #a   started · python-specialist",
        "16:03  #a   gates red (pytest 3m timed out, ruff 2s red)",
        f"16:03  ✗ #a the gates are not green: {PYTEST} (timed out); ruff check",
    ]


def test_status_names_the_timed_out_gate_in_the_reason_of_a_failed_ticket(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("LC_ALL", "en_NZ.UTF-8")
    monkeypatch.setenv("COLUMNS", "300")
    adapters.register("scripted", ScriptedAdapter())
    try:
        text = WORKFLOW.replace('gates = ["test -f feature.txt"]', 'gates = ["sleep 30"]\ngate-timeout = 0.01')
        repo = make_repo(tmp_path, text)
        delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
        events = read_journal(capsys.readouterr().out)
    finally:
        adapters.unregister("scripted")

    code = delegate.main(["status", "--repo", str(repo)])
    out = capsys.readouterr().out

    assert code == 0
    assert any(e["event"] == "gate-result" and e["timed_out"] for e in events)
    assert "gates red: sleep 30 (timed out)" in out
