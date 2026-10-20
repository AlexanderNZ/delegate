"""`delegate watch` goes on past a problem in the replay that the run already went past (#146).

The replay is the part of the journal that exists when `watch` starts. A problem there stops the watch only when no
later event of the run handles it: a `continuation` or a `retry` of its ticket, or a later `step-start`, `step-end` or
`run-end`. A problem that arrives after the watch starts stops the watch, as before.

A case writes the journal with the journal class of the engine in a real temporary git repository, holds the lock of
the run branch as the process of a run that goes on does, and calls `delegate.main(["watch", ...])` with no run id. The
wait between two polls (`time.sleep`) is the system boundary: it appends the next events of the live run.
"""

from __future__ import annotations

import time

import pytest

from delegate.cli import delegate

from .support import append_events, bare_repo, live_lock, run_end, run_start, step_end, step_start, write_journal

POLL = ["--poll-seconds", "0.02"]
PROBLEM = 3
SIGTERM_PROBLEM = "watch: problem: ticket a: the specialist ended failed with exit status 143"


def specialist_result(ticket: str, end_state: str, exit_status: int) -> tuple[str, dict[str, object]]:
    return "adapter-result", {
        "ticket": ticket, "exit_status": exit_status, "end_state": end_state, "session_id": "s1", "event_stream": f"/s/{ticket}",
    }


def continuation(ticket: str) -> tuple[str, dict[str, object]]:
    return "continuation", {
        "ticket": ticket, "count": 1, "limit": 2, "trigger": "failed", "mode": "resume", "resume_session": "s1",
        "commits": [], "reason": "the specialist ended failed with exit status 143",
    }


def valid_report_event(ticket: str) -> tuple[str, dict[str, object]]:
    return "report-validation", {
        "ticket": ticket, "valid": True, "path": f"/r/{ticket}.json", "reason": None, "status": "committed", "blocked_reason": None,
    }


def no_more_polls(seconds: float) -> None:
    pytest.fail("watch polled again after the problem that ends it")


def watch(repo, capsys, *extra):
    code = delegate.main(["watch", "--repo", str(repo), *POLL, *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("extra", [(), ("--raw",)], ids=["not-a-terminal", "raw"])
def test_watch_follows_the_run_past_a_specialist_failure_that_a_continuation_handled(tmp_path, capsys, monkeypatch, extra):
    # The specialist of ticket a ended with SIGTERM from outside the engine; the continuation built the ticket, and the
    # step of ticket b runs when watch starts.
    repo = bare_repo(tmp_path)
    path = write_journal(
        repo, "r1", run_start("r1", "a", "b"), step_start("a"), specialist_result("a", "failed", 143), continuation("a"),
        specialist_result("a", "finished", 0), valid_report_event("a"), step_end("a"), step_start("b"),
    )
    monkeypatch.setattr(time, "sleep", lambda seconds: append_events(path, step_end("b"), run_end("built", ["a", "b"])))

    with live_lock(path):
        code, out, err = watch(repo, capsys, *extra)

    assert (code, err) == (0, "")
    assert "watch: problem" not in out
    lines = out.splitlines()
    # The problem that the run went past is in the feed as history, with its raw line as before.
    assert lines[2] == f"3 {lines[2].split()[1]} adapter-result ticket=a exit_status=143 end_state=failed session_id=s1 event_stream=/s/a"
    assert [line.split()[2] for line in lines[:10]] == [
        "run-start", "step-start", "adapter-result", "continuation", "adapter-result", "report-validation",
        "step-end", "step-start", "step-end", "run-end",
    ]
    assert lines[-1] == "position 10"


@pytest.mark.parametrize("extra", [(), ("--raw",)], ids=["not-a-terminal", "raw"])
def test_watch_stops_at_a_problem_that_is_the_last_event_of_the_replay(tmp_path, capsys, monkeypatch, extra):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), specialist_result("a", "failed", 143))
    monkeypatch.setattr(time, "sleep", no_more_polls)

    with live_lock(path):
        code, out, err = watch(repo, capsys, *extra)

    assert (code, err) == (PROBLEM, "")
    assert out.splitlines()[-2:] == [SIGTERM_PROBLEM, "position 3"]


def test_watch_stops_at_a_problem_that_arrives_after_it_starts_also_when_a_continuation_follows_it(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"))
    # The problem and the continuation that handles it arrive in one read, after the replay.
    def append_once(seconds: float) -> None:
        monkeypatch.setattr(time, "sleep", no_more_polls)
        append_events(path, specialist_result("a", "failed", 143), continuation("a"))

    monkeypatch.setattr(time, "sleep", append_once)

    with live_lock(path):
        code, out, err = watch(repo, capsys)

    assert (code, err) == (PROBLEM, "")
    assert out.splitlines()[-2:] == [SIGTERM_PROBLEM, "position 3"]
    assert "continuation" not in out


def test_watch_follows_a_resumed_run_past_a_failed_step_before_the_crash_when_the_next_step_started(tmp_path, capsys, monkeypatch):
    # The run stopped with no `run-end` (a crash) after the step of ticket a failed. The resume went on with ticket b.
    repo = bare_repo(tmp_path)
    resume = ("resume", {"run_id": "r1", "built": [], "failed": ["a"], "skipped": [], "open": None})
    path = write_journal(repo, "r1", run_start("r1", "a", "b"), step_start("a"), step_end("a", "failed", "boom"), resume, step_start("b"))
    monkeypatch.setattr(time, "sleep", lambda seconds: append_events(path, step_end("b"), run_end("failed", ["b"], ["a"])))

    with live_lock(path):
        code, out, err = watch(repo, capsys)

    assert (code, err) == (1, "")  # the result of the run, not a problem
    assert "watch: problem" not in out
    assert "reason=boom" in out.splitlines()[2]


def ended_run_with_a_handled_problem(*end: tuple[str, dict[str, object]]) -> tuple[tuple[str, dict[str, object]], ...]:
    """A run of tickets a and b: the specialist of ticket a ended with SIGTERM, the continuation built the ticket, then `end`."""
    return (
        run_start("r1", "a", "b"), step_start("a"), specialist_result("a", "failed", 143), continuation("a"),
        specialist_result("a", "finished", 0), valid_report_event("a"), step_end("a"), *end,
    )


PAUSED = (("pause-requested", {"ticket": "b", "requested_at": None}), ("paused", {"ticket": "b", "before": "step"}))


@pytest.mark.parametrize("extra", [(), ("--raw",)], ids=["not-a-terminal", "raw"])
@pytest.mark.parametrize(
    ("end", "code_of_end"),
    [((step_start("b"), step_end("b"), run_end("built", ["a", "b"])), 0), (PAUSED, 7)],
    ids=["run-end-built", "paused"],
)
def test_watch_replays_an_ended_run_past_a_problem_that_the_run_went_past(tmp_path, capsys, monkeypatch, extra, end, code_of_end):
    # The run is at its end when watch starts (#151): no process holds the lock, and the last event is the end.
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", *ended_run_with_a_handled_problem(*end))
    monkeypatch.setattr(time, "sleep", no_more_polls)

    code, out, err = watch(repo, capsys, "r1", *extra)

    assert (code, err) == (code_of_end, "")
    assert "watch: problem" not in out
    lines = out.splitlines()
    assert lines[2] == f"3 {lines[2].split()[1]} adapter-result ticket=a exit_status=143 end_state=failed session_id=s1 event_stream=/s/a"
    count = 7 + len(end)
    assert [line.split()[0] for line in lines[:count]] == [str(seq) for seq in range(1, count + 1)]
    assert lines[count - 1].split()[2] == end[-1][0]
    assert lines[-1] == f"position {count}"


@pytest.mark.parametrize("extra", [(), ("--raw",)], ids=["not-a-terminal", "raw"])
@pytest.mark.parametrize("end", [(run_end("failed", [], ["a"]),), PAUSED], ids=["run-end-failed", "paused"])
def test_watch_stops_an_ended_run_at_its_last_problem_when_no_later_event_goes_past_it(tmp_path, capsys, monkeypatch, extra, end):
    # The step-end goes past the problem of the specialist, but only the end of the run follows the failed step.
    repo = bare_repo(tmp_path)
    write_journal(
        repo, "r1", run_start("r1", "a", "b"), step_start("a"), specialist_result("a", "failed", 143),
        step_end("a", "failed", "boom"), *end,
    )
    monkeypatch.setattr(time, "sleep", no_more_polls)

    code, out, err = watch(repo, capsys, "r1", *extra)

    assert (code, err) == (PROBLEM, "")
    assert out.splitlines()[-2:] == ["watch: problem: ticket a: the step failed: boom", "position 4"]
