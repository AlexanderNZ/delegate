"""`delegate watch --json`: one JSON object on one line for each event that `watch` reports.

A case writes the journal of a run with the journal class of the engine and calls
`delegate.main(["watch", ...])` against a real temporary git repository. The
output is read back with `json.loads`, one line at a time.
"""

import json
import os

import pytest

from delegate.cli import delegate

from .support import bare_repo, run_end, run_start, step_end, step_start, write_journal

POLL = ["--poll-seconds", "0.02"]


def watch(repo, capsys, *extra):
    code = delegate.main(["watch", "r1", "--repo", str(repo), *POLL, *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def objects_of(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines()]


def verdict(ticket, value, findings=()):
    return "verdict", {
        "ticket": ticket, "round": 0, "mode": "full", "verdict": value, "findings": list(findings),
        "unverified": [], "report": "/r.json",
    }


def test_json_of_a_run_that_ended_prints_each_event_as_one_json_object_in_order_and_exits_with_its_result(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), step_end("a"), run_end("built", ["a"]))
    journal = [json.loads(line) for line in path.read_text().splitlines()]

    code, out, err = watch(repo, capsys, "--json")

    assert (code, err) == (0, "")
    assert objects_of(out) == journal
    assert [event["event"] for event in objects_of(out)] == ["run-start", "step-start", "step-end", "run-end"]


def test_json_of_a_run_that_failed_exits_1_and_prints_no_watch_end(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), run_end("failed", [], ["a"]))

    code, out, _ = watch(repo, capsys, "--json")

    assert code == 1
    assert [event["event"] for event in objects_of(out)] == ["run-start", "run-end"]


def test_json_with_until_verdict_prints_the_verdict_event_then_a_watch_end_object_with_the_reason_verdict(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(
        repo, "r1", run_start("r1", "a"), step_start("a"), verdict("a", "REJECT", ["no test"]), step_end("a"), run_end("built", ["a"]),
    )

    code, out, _ = watch(repo, capsys, "--json", "--until", "verdict")

    assert code == 4
    objects = objects_of(out)
    assert [event["event"] for event in objects] == ["run-start", "step-start", "verdict", "watch-end"]
    assert objects[2]["verdict"] == "REJECT"
    assert objects[3] == {"event": "watch-end", "reason": "verdict", "exit_code": 4}


def test_json_prints_a_watch_end_object_for_a_problem(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), ("step-end", {"ticket": "a", "state": "failed", "reason": "boom"}))

    code, out, _ = watch(repo, capsys, "--json")

    assert code == 3
    objects = objects_of(out)
    assert objects[-2]["event"] == "step-end"
    assert objects[-1] == {"event": "watch-end", "reason": "problem", "exit_code": 3}


def test_json_prints_a_watch_end_object_for_a_stall(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"))
    old = os.stat(path).st_mtime - 3600
    os.utime(path, (old, old))

    code, out, _ = watch(repo, capsys, "--json", "--stall-minutes", "1")

    assert code == 5
    assert objects_of(out)[-1] == {"event": "watch-end", "reason": "stall", "exit_code": 5}


def test_json_prints_a_watch_end_object_for_the_time_limit(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"))

    code, out, _ = watch(repo, capsys, "--json", "--max-minutes", "0.0001")

    assert code == 6
    assert objects_of(out)[-1] == {"event": "watch-end", "reason": "time-limit", "exit_code": 6}


def test_json_with_from_prints_only_the_events_after_the_position(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), step_end("a"), run_end("built", ["a"]))

    code, out, _ = watch(repo, capsys, "--json", "--from", "2")

    assert code == 0
    assert [event["seq"] for event in objects_of(out)] == [3, 4]


def test_json_and_raw_together_exit_2_with_a_usage_error(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), run_end("built", ["a"]))

    with pytest.raises(SystemExit) as stop:
        watch(repo, capsys, "--json", "--raw")

    assert stop.value.code == 2
    err = capsys.readouterr().err
    assert "--json" in err and "--raw" in err


def test_json_without_a_run_id_replays_the_journal_of_the_newest_run_when_no_run_is_active(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), step_end("a"), run_end("built", ["a"]))

    code = delegate.main(["watch", "--repo", str(repo), *POLL, "--json"])
    captured = capsys.readouterr()

    assert (code, captured.err) == (0, "")
    assert [event["event"] for event in objects_of(captured.out)] == ["run-start", "step-start", "step-end", "run-end"]
