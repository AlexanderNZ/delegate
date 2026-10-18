"""A run that ended and was resumed: `status` and `watch` read the newest part of its journal (#103).

`delegate run --resume <id>` of a run that ended appends `resume` and `retry` events after the old `run-end`. The run
then works again, so the old `run-end` does not end it, and the retried ticket runs again. A case writes the journal
with the journal class of the engine, and a case that follows a live run appends from a second thread.
"""

import json
import threading
import time

from delegate.cli import delegate

from .support import append_events, bare_repo, run_end, run_start, step_end, step_start, write_journal

POLL = ["--poll-seconds", "0.02"]


def resume(*failed: str) -> tuple[str, dict[str, object]]:
    return "resume", {"run_id": "r1", "built": [], "failed": list(failed), "skipped": [], "open": None}


def retry(ticket: str) -> tuple[str, dict[str, object]]:
    return "retry", {"ticket": ticket, "phase": "specialist", "round": 0, "reason": "the specialist ended failed"}


def agent_start(ticket: str) -> tuple[str, dict[str, object]]:
    return "agent-start", {"ticket": ticket, "role": "specialist", "round": 0, "event_stream": f"/w/{ticket}.stream.jsonl"}


ENDED_THEN_RESUMED = (
    run_start("r1", "a"), step_start("a"), step_end("a", "failed", "the specialist ended failed"),
    run_end("failed", [], ["a"]), resume("a"), retry("a"), agent_start("a"),
)


def main(capsys, *argv: str) -> tuple[int, str]:
    code = delegate.main(list(argv))
    return code, capsys.readouterr().out


def test_status_of_a_resumed_run_that_had_ended_gives_the_run_and_the_retried_ticket_as_running(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", *ENDED_THEN_RESUMED)

    code, out = main(capsys, "status", "--repo", str(repo), "r1")

    assert code == 0
    assert out.splitlines()[0].startswith("run r1: running ")
    (line,) = [line for line in out.splitlines() if line.startswith("ticket a:")]
    assert line.startswith("ticket a: state running,")


def test_status_json_of_a_resumed_run_that_had_ended_gives_the_run_and_the_retried_ticket_as_running(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", *ENDED_THEN_RESUMED)

    code, out = main(capsys, "status", "--repo", str(repo), "--json", "r1")

    snap = json.loads(out)
    assert code == 0
    assert snap["state"] == "running"
    assert [(t["id"], t["state"]) for t in snap["tickets"]] == [("a", "running")]
    assert snap["percent"] < 100


def test_watch_of_a_resumed_run_goes_past_the_old_run_end_and_exits_with_the_result_of_the_new_one(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", *ENDED_THEN_RESUMED)

    def append_later():
        time.sleep(0.2)
        append_events(path, step_end("a"), run_end("built", ["a"]))

    writer = threading.Thread(target=append_later)
    writer.start()
    code, out = main(capsys, "watch", "--repo", str(repo), *POLL, "--max-minutes", "1", "r1")
    writer.join()

    ends = [line for line in out.splitlines() if line.split()[2:3] == ["run-end"]]
    assert code == 0
    assert [line.split()[0] for line in ends] == ["4", "9"]
    assert "result=built" in ends[-1]


def test_watch_from_a_position_after_the_old_run_end_follows_the_resumed_run(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", *ENDED_THEN_RESUMED)

    def append_later():
        time.sleep(0.2)
        append_events(path, step_end("a"), run_end("built", ["a"]))

    writer = threading.Thread(target=append_later)
    writer.start()
    code, out = main(capsys, "watch", "--repo", str(repo), *POLL, "--max-minutes", "1", "--from", "7", "r1")
    writer.join()

    ends = [line for line in out.splitlines() if line.split()[2:3] == ["run-end"]]
    assert code == 0
    assert ends and ends[-1].split()[0] == "9" and "result=built" in ends[-1]


def test_watch_of_a_run_that_ended_and_was_not_resumed_still_exits_at_once_with_its_result(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), step_end("a"), run_end("built", ["a"]))

    started = time.monotonic()
    code, _ = main(capsys, "watch", "--repo", str(repo), *POLL, "--max-minutes", "1", "r1")

    assert code == 0
    assert time.monotonic() - started < 5


def test_watch_of_a_resumed_run_that_never_ended_still_exits_3_at_its_old_problem(tmp_path, capsys):
    # A run that stopped with no `run-end` (a crash) and was resumed: its problem before the `resume` still ends the watch.
    repo = bare_repo(tmp_path)
    write_journal(
        repo, "r1", run_start("r1", "a", "b"), step_start("a"), step_end("a", "failed", "boom"), resume("a"), step_start("b"),
    )

    code, out = main(capsys, "watch", "--repo", str(repo), *POLL, "--max-minutes", "1", "r1")

    assert code == 3
    assert "watch: problem: ticket a:" in out


def test_watch_until_verdict_of_a_resumed_run_does_not_exit_at_a_verdict_before_the_old_run_end(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    verdict = ("verdict", {"ticket": "a", "round": 0, "mode": "full", "verdict": "REJECT", "findings": ["x"], "report": "/w/v.json"})
    path = write_journal(
        repo, "r1", run_start("r1", "a"), step_start("a"), verdict, step_end("a", "failed", "rejected"),
        run_end("failed", [], ["a"]), resume("a"), retry("a"),
    )

    def append_later():
        time.sleep(0.2)
        append_events(path, ("verdict", {**verdict[1], "verdict": "ACCEPT", "findings": []}))

    writer = threading.Thread(target=append_later)
    writer.start()
    code, out = main(capsys, "watch", "--repo", str(repo), *POLL, "--max-minutes", "1", "--until", "verdict", "r1")
    writer.join()

    assert code == 4
    assert "ACCEPT" in out and "REJECT" not in out.split("resume", 1)[1]


def test_watch_of_a_run_that_ended_resumed_crashed_and_resumed_again_exits_3_at_the_problem_after_the_first_resume(tmp_path, capsys):
    # The history ends at the first `resume` after the last `run-end`. A problem after it is no history, also when a later
    # `resume` follows a crash with no `run-end`.
    repo = bare_repo(tmp_path)
    write_journal(
        repo, "r1", run_start("r1", "a", "b"), step_start("a"), step_end("a", "failed", "boom1"), run_end("failed", [], ["a"]),
        resume("a"), retry("a"), step_start("a"), step_end("a", "failed", "boom2"), resume("a"), retry("a"), step_start("b"),
    )

    code, out = main(capsys, "watch", "--repo", str(repo), *POLL, "--max-minutes", "1", "r1")

    assert code == 3
    assert "boom2" in out and "boom1" not in out.split("watch: problem")[1]


def test_watch_of_a_resumed_run_that_ended_again_exits_with_the_result_of_its_last_run_end(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", *ENDED_THEN_RESUMED, step_end("a"), run_end("built", ["a"]))

    code, out = main(capsys, "watch", "--repo", str(repo), *POLL, "--max-minutes", "1", "r1")

    assert code == 0
    assert "result=built" in [line for line in out.splitlines() if line.split()[2:3] == ["run-end"]][-1]
