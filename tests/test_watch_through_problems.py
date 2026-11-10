"""`delegate watch` follows an active run to its end, and a replay does not stop at a problem that the run went past (#222).

The live view (standard output is a terminal, no `--raw`, no `--json`) never ends at a problem: the feed shows it as a
warning line, in yellow, and the row of its ticket keeps a warning mark until the next event of that ticket. It ends at
`run-end`, `paused` and `halted`, and at the limits that a person sets. The raw and JSON forms end with 3 at a new problem,
but the replay takes a problem as history when any later event follows it. `--follow` makes them behave like the live view.

A case writes the journal with the journal class of the engine in a real temporary git repository, holds the lock of the
run branch as the process of a run that goes on does, and calls `delegate.main(["watch", ...])`. The terminal (a standard
output whose `isatty` is true) and the wait between two polls (the wait of the `clock` fixture), which appends the next events of the live
run, are the system boundaries.
"""

from __future__ import annotations

import json
import sys

import pytest

from delegate.cli import delegate

from .support import (
    CONTROL, Terminal, append_events, bare_repo, fake_clock, frames, live_lock, run_end, run_start, step_end,
    step_start, write_journal,
)

# The commands of each case use the fake clock of the case (#297): its helpers set it.
pytestmark = pytest.mark.usefixtures("clock")

POLL = ["--poll-seconds", "0.02"]
PROBLEM = 3
SECOND_CONTINUATION = "the step continued 2 times (limit 2, trigger gates-red)"
YELLOW = "\x1b[33m"


def continuation(ticket: str, count: int) -> tuple[str, dict[str, object]]:
    return "continuation", {
        "ticket": ticket, "count": count, "limit": 2, "trigger": "gates-red", "mode": "resume", "resume_session": "s1",
        "commits": [], "reason": "red",
    }


def agent_start(ticket: str) -> tuple[str, dict[str, object]]:
    return "agent-start", {"ticket": ticket, "role": "specialist", "round": 0, "event_stream": f"/s/{ticket}"}


def gate_result(ticket: str, green: bool = True) -> tuple[str, dict[str, object]]:
    return "gate-result", {
        "ticket": ticket, "round": 0, "phase": "build", "command": "pytest", "commit": "1" * 40,
        "exit_status": 0 if green else 1, "green": green, "output_tail": "ok",
    }


def verdict(ticket: str) -> tuple[str, dict[str, object]]:
    return "verdict", {
        "ticket": ticket, "round": 0, "mode": "full", "verdict": "REJECT", "findings": ["a: x"], "unverified": [], "report": "/r.json",
    }


def paused(ticket: str) -> tuple[str, dict[str, object]]:
    return "paused", {"ticket": ticket, "before": "step"}


def halted(ticket: str) -> tuple[str, dict[str, object]]:
    return "halted", {"ticket": ticket, "role": "specialist", "message": "the harness failed"}


def crashed(ticket: str) -> tuple[str, dict[str, object]]:
    return "crashed", {
        "ticket": ticket, "phase": "gates", "error": "EngineError", "message": "git: disk full", "traceback": "/state/runs/r1/crash-9.txt",
    }


# The journal of the report of the owner: a second continuation, then the session, the gate and the verdict of the same ticket.
def run_past_a_second_continuation(*after: tuple[str, dict[str, object]]) -> tuple[tuple[str, dict[str, object]], ...]:
    return (
        run_start("r1", "R5"), step_start("R5"), continuation("R5", 1), continuation("R5", 2),
        agent_start("R5"), gate_result("R5"), verdict("R5"), *after,
    )


def no_more_polls(seconds: float) -> None:
    pytest.fail("watch polled again after the reason that ends it")


def watch(repo, capsys, monkeypatch, *extra, terminal=False):
    out = Terminal() if terminal else None
    if out is not None:
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setenv("COLUMNS", "200")
        monkeypatch.setenv("LINES", "50")
    code = delegate.main(["watch", "--repo", str(repo), *POLL, *extra])
    if out is not None:
        return code, out.getvalue(), capsys.readouterr().err
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def ends_with(monkeypatch, path, *events):
    """A wait between two polls that appends `events` once, and then fails a case that waits again."""

    def sleep(seconds: float) -> None:
        fake_clock().on_sleep = no_more_polls
        append_events(path, *events)

    fake_clock().on_sleep = sleep


# The live view.


def test_the_live_view_does_not_exit_at_a_second_continuation_followed_by_a_session_a_gate_and_a_verdict(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", *run_past_a_second_continuation())
    ends_with(monkeypatch, path, step_end("R5"), run_end("built", ["R5"]))

    with live_lock(path):
        code, out, err = watch(repo, capsys, monkeypatch, terminal=True)

    assert (code, err) == (0, "")  # the end of the run, not the problem
    warning = next(line for line in frames(out)[-1] if SECOND_CONTINUATION in line)
    assert warning.endswith(f"#R5 {SECOND_CONTINUATION}")


def test_the_live_view_shows_the_problem_as_a_warning_line_in_yellow(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "R5"), step_start("R5"), continuation("R5", 2))
    ends_with(monkeypatch, path, run_end("failed", [], ["R5"]))

    with live_lock(path):
        code, out, _ = watch(repo, capsys, monkeypatch, terminal=True)

    assert code == 1
    painted = next(line for line in out.splitlines() if SECOND_CONTINUATION in line)
    assert YELLOW in painted
    assert "\x1b[31m" not in painted  # not red: the run goes on


def test_the_row_of_the_ticket_keeps_a_warning_mark_until_the_next_event_of_that_ticket(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "R5", "R6"), step_start("R5"), continuation("R5", 2))
    ends_with(monkeypatch, path, agent_start("R5"), run_end("failed", [], ["R5"]))

    with live_lock(path):
        code, out, _ = watch(repo, capsys, monkeypatch, terminal=True)

    assert code == 1
    first, *_, last = frames(out)

    def row(frame: list[str], ticket: str) -> str:
        return next(line for line in frame if line.split()[:2] and line.split()[0].isdigit() and ticket in line.split())

    assert "⚠" in row(first, "R5")
    assert "⚠" not in row(first, "R6")
    assert "⚠" not in row(last, "R5")


@pytest.mark.parametrize(
    ("end", "code"),
    [
        (run_end("built", ["R5"]), 0),
        (run_end("failed", [], ["R5"]), 1),
        (paused("R5"), 7),
        (halted("R5"), 8),
        (crashed("R5"), 8),
    ],
    ids=["built", "failed", "paused", "halted", "crashed"],
)
def test_the_live_view_exits_at_the_end_of_the_run_after_a_problem(tmp_path, capsys, monkeypatch, end, code):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", *run_past_a_second_continuation())
    ends_with(monkeypatch, path, end)

    with live_lock(path):
        result, _, err = watch(repo, capsys, monkeypatch, terminal=True)

    assert (result, err) == (code, "")


def test_the_live_view_ends_at_a_crash_with_a_line_that_names_the_file_of_the_traceback_and_the_resume_command(tmp_path, capsys, monkeypatch):
    # A crash leaves the step open, and the run process ends (#281): the live view stops there, as at a halt.
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "R5"), step_start("R5"))
    ends_with(monkeypatch, path, crashed("R5"))

    with live_lock(path):
        code, out, err = watch(repo, capsys, monkeypatch, terminal=True)

    assert (code, err) == (8, "")
    line = next(line for line in frames(out)[-1] if "crashed" in line)
    assert line.endswith(
        "crashed · the gates of ticket R5 · EngineError: git: disk full · traceback crash-9.txt · go on with delegate run --resume r1"
    )


def test_the_live_view_goes_on_at_a_problem_that_is_the_newest_event_of_the_replay(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "R5"), step_start("R5"), continuation("R5", 2))
    ends_with(monkeypatch, path, run_end("built", ["R5"]))

    with live_lock(path):
        code, _, _ = watch(repo, capsys, monkeypatch, terminal=True)

    assert code == 0


def test_a_limit_that_a_person_sets_still_ends_the_live_view_after_a_problem(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", *run_past_a_second_continuation())
    fake_clock().on_sleep = lambda seconds: None

    with live_lock(path):
        code, _, _ = watch(repo, capsys, monkeypatch, "--max-minutes", "0.0001", terminal=True)

    assert code == 6


# The raw and JSON forms.


def test_a_raw_watch_with_no_from_follows_a_replay_past_a_problem_that_later_events_follow(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", *run_past_a_second_continuation())
    ends_with(monkeypatch, path, step_end("R5"), run_end("built", ["R5"]))

    with live_lock(path):
        code, out, err = watch(repo, capsys, monkeypatch, "--raw")

    assert (code, err) == (0, "")
    assert "watch: problem" not in out
    assert out.splitlines()[-1] == "position 9"  # the whole journal, to the end of the run


@pytest.mark.parametrize("extra", [(), ("--raw",)], ids=["not-a-terminal", "raw"])
def test_a_raw_watch_exits_3_at_a_problem_that_is_the_newest_event_of_the_replay(tmp_path, capsys, monkeypatch, extra):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "R5"), step_start("R5"), continuation("R5", 2))
    fake_clock().on_sleep = no_more_polls

    with live_lock(path):
        code, out, _ = watch(repo, capsys, monkeypatch, *extra)

    assert code == PROBLEM
    assert out.splitlines()[-2:] == [f"watch: problem: ticket R5: {SECOND_CONTINUATION}", "position 3"]


@pytest.mark.parametrize("extra", [(), ("--raw",), ("--json",)], ids=["not-a-terminal", "raw", "json"])
def test_a_raw_watch_exits_3_at_a_problem_that_arrives_after_it_starts(tmp_path, capsys, monkeypatch, extra):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "R5"), step_start("R5"))
    ends_with(monkeypatch, path, continuation("R5", 2), agent_start("R5"))

    with live_lock(path):
        code, out, _ = watch(repo, capsys, monkeypatch, *extra)

    assert code == PROBLEM
    assert "agent-start" not in out


@pytest.mark.parametrize("extra", [("--raw",), ("--json",)], ids=["raw", "json"])
def test_follow_makes_the_raw_and_json_forms_go_on_at_a_problem(tmp_path, capsys, monkeypatch, extra):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "R5"), step_start("R5"))
    ends_with(monkeypatch, path, continuation("R5", 2), agent_start("R5"), run_end("built", ["R5"]))

    with live_lock(path):
        code, out, err = watch(repo, capsys, monkeypatch, "--follow", *extra)

    assert (code, err) == (0, "")
    assert "agent-start" in out
    assert "watch-end" not in out  # the watch ended with the run, not for a reason of its own


def test_follow_with_json_still_ends_at_a_limit_with_its_last_object(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "R5"), step_start("R5"), continuation("R5", 2))
    fake_clock().on_sleep = lambda seconds: None

    with live_lock(path):
        code, out, _ = watch(repo, capsys, monkeypatch, "--follow", "--json", "--max-minutes", "0.0001")

    assert code == 6
    assert json.loads(out.splitlines()[-1]) == {"event": "watch-end", "reason": "time-limit", "exit_code": 6}


def test_follow_on_a_terminal_is_accepted(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "R5"), step_start("R5"), continuation("R5", 2))
    ends_with(monkeypatch, path, run_end("built", ["R5"]))

    with live_lock(path):
        code, out, _ = watch(repo, capsys, monkeypatch, "--follow", terminal=True)

    assert code == 0
    assert CONTROL.sub("", out)  # a frame was drawn
