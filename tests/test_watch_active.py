"""`delegate watch` and `delegate status` with no run id: the active runs of the repository decide the run.

A run is active when its journal has no `run-end` after its newest `resume`, and the lock of its run branch names the
run and a process that exists. A case writes each journal with the journal class of the engine, and each lock with the
lock class of the engine: a live lock is held by the process of the test, and a stale lock by a child process that
ended. A case calls `delegate.main([...])` against a real temporary git repository.

A run that failed at start has a directory with a `run.log` and no journal. A case writes that log as a file.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta

from delegate.cli import delegate

from .support import (
    append_events, bare_repo, journal_path, live_lock, run_end, run_start, stale_lock, step_end, step_start, write_journal,
)
from .test_watch import twelve_hours_east  # noqa: F401 - a fixture

# The time limit ends a watch that follows a run with no end, so a case fails and does not wait.
POLL = ["--poll-seconds", "0.02", "--max-minutes", "0.1"]


def main(capsys, *argv):
    code = delegate.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def watch(repo, capsys, *extra):
    return main(capsys, "watch", "--repo", str(repo), *POLL, *extra)


def status(repo, capsys, *extra):
    return main(capsys, "status", "--repo", str(repo), *extra)


def local_start(path, offset: timedelta) -> str:
    """The time of the run-start event of a journal, in the zone `offset` east of UTC, as `YYYY-MM-DD HH:MM`."""
    first = json.loads(path.read_text().splitlines()[0])
    assert first["event"] == "run-start"
    return (datetime.fromisoformat(first["time"]).replace(tzinfo=None) + offset).strftime("%Y-%m-%d %H:%M")


def two_active_runs(repo):
    """Two runs that go on, on two run branches, each with no lock yet. Their journals were last changed 2 hours ago."""
    first = write_journal(
        repo, "r-assure", run_start("r-assure", "a", "b", run_branch="run/one"), step_start("a"), step_end("a"), step_start("b"),
    )
    second = write_journal(repo, "r-economy", run_start("r-economy", "x", mode="economy", run_branch="run/two"), step_start("x"))
    then = time.time() - 7200 - 10  # 10 seconds more, so a slow case still shows 2h00m
    for journal in (first, second):
        os.utime(journal, (then, then))
    return first, second


def test_watch_without_a_run_id_follows_the_one_active_run_when_a_newer_run_ended(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    active = write_journal(repo, "older", run_start("older", "a", run_branch="run/older"), step_start("a"))
    write_journal(repo, "newer", run_start("newer", "b", run_branch="run/newer"), run_end("failed", [], ["b"]))
    # The active run ends while watch waits after its first read of the journal.
    monkeypatch.setattr(time, "sleep", lambda seconds: append_events(active, step_end("a"), run_end("built", ["a"])))

    with live_lock(active, "run/older"):
        code, out, err = watch(repo, capsys)

    assert (code, err) == (0, "")
    assert "no run is active" not in out
    assert "run_id=older" in out and "run_id=newer" not in out
    assert [line.split()[2] for line in out.splitlines()[:4]] == ["run-start", "step-start", "step-end", "run-end"]
    assert out.splitlines()[4:] == ["position 4"]


def test_watch_without_a_run_id_and_no_active_run_prints_the_one_line_of_the_newest_run_that_ended(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "older", run_start("older", "a", run_branch="run/older"), run_end("failed", [], ["a"]))
    newer = write_journal(repo, "newer", run_start("newer", "b", run_branch="run/newer"), run_end("built", ["b"]))

    # A lock that the process of the newest run still holds does not make a run with a `run-end` active.
    with live_lock(newer, "run/newer"):
        code, out, err = watch(repo, capsys)

    assert (code, err) == (0, "")
    assert out.startswith("no run is active; the newest run newer ended built at ")
    assert out.count("\n") == 1


def test_watch_without_a_run_id_and_two_active_runs_not_on_a_terminal_names_them_on_stderr_follows_none_and_exits_2(
    tmp_path, capsys,
):
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code, out, err = watch(repo, capsys)

    # Exit 2 replaces the exit 8 of #110 (#118): a coordinator cannot answer the prompt of the picker.
    assert (code, out) == (2, "")
    assert err == (
        "delegate watch: 2 runs are active in this repository; give a run id: "
        "r-economy (run/two, running), r-assure (run/one, running)\n"
    )


def test_watch_without_a_run_id_does_not_count_a_run_with_no_run_end_and_no_live_process_as_active(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    active = write_journal(repo, "older", run_start("older", "a", run_branch="run/older"), step_start("a"))
    # The newest run stopped with no `run-end`: its process was killed, and its lock names a process that no longer exists.
    stopped = write_journal(repo, "newer", run_start("newer", "b", run_branch="run/newer"), step_start("b"))
    stale_lock(stopped, "run/newer")
    monkeypatch.setattr(time, "sleep", lambda seconds: append_events(active, step_end("a"), run_end("built", ["a"])))

    with live_lock(active, "run/older"):
        code, out, err = watch(repo, capsys)

    assert (code, err) == (0, "")
    assert "run_id=older" in out and "run_id=newer" not in out


def test_watch_without_a_run_id_reports_a_newest_run_that_stopped_with_no_live_process_in_one_line_with_the_resume(
    tmp_path, capsys, twelve_hours_east,  # noqa: F811 - the fixture
):
    repo = bare_repo(tmp_path)
    write_journal(repo, "older", run_start("older", "a", run_branch="run/older"), run_end("built", ["a"]))
    stopped = write_journal(repo, "newer", run_start("newer", "b", run_branch="run/newer"), step_start("b"))
    stale_lock(stopped, "run/newer")
    last = json.loads(stopped.read_text().splitlines()[-1])["time"]
    at = (datetime.fromisoformat(last).replace(tzinfo=None) + twelve_hours_east).strftime("%Y-%m-%d %H:%M")

    code, out, err = watch(repo, capsys)

    assert (code, out) == (2, "")
    assert err == (
        f"delegate watch: no run is active; the newest run newer stopped at {at} with no run-end, and no process works on it; "
        "go on with delegate run --resume newer\n"
    )


def test_watch_without_a_run_id_reports_a_newest_run_that_is_paused_in_one_line_and_exits_7(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(
        repo, "newer", run_start("newer", "a", "b"), step_start("a"), step_end("a"),
        ("pause-requested", {"ticket": "b", "requested_at": None}), ("paused", {"ticket": "b", "before": "step"}),
    )

    code, out, err = watch(repo, capsys)

    assert (code, err) == (7, "")
    assert re.fullmatch(
        r"no run is active; the newest run newer paused at \d{4}-\d\d-\d\d \d\d:\d\d; go on with delegate run --resume newer\n", out,
    )


def test_status_without_a_run_id_lists_two_active_runs_newest_first_and_exits_0(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code, out, err = status(repo, capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[0] == "2 runs are active in this repository"
    assert [line.split()[1] for line in out.splitlines()[2:]] == ["r-economy", "r-assure"]


def test_status_without_a_run_id_shows_the_one_active_run_when_a_newer_run_ended(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    active = write_journal(repo, "older", run_start("older", "a", run_branch="run/older"), step_start("a"))
    write_journal(repo, "newer", run_start("newer", "b", run_branch="run/newer"), run_end("failed", [], ["b"]))

    with live_lock(active, "run/older"):
        code, out, err = status(repo, capsys)
        json_code, snapshot, _ = status(repo, capsys, "--json")

    assert (code, err) == (0, "")
    assert out.splitlines()[0] == "run older: running (mode assure, adapter scripted)"
    assert json_code == 0 and json.loads(snapshot)["run_id"] == "older"


def test_status_json_without_a_run_id_and_two_active_runs_exits_2_and_names_each_run(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code, out, err = status(repo, capsys, "--json")

    assert (code, out) == (2, "")
    assert err == "delegate status: 2 runs are active: r-assure, r-economy; give a run id to show one of them\n"


# The last line that a detached run process wrote to its log when a check failed before the run wrote its journal.
GIT_ERROR = (
    "delegate run: git branch run/watch-chain e0dbee4 failed: fatal: a branch named 'run/watch-chain' already exists"
)


def failed_start(repo, run_id: str) -> str:
    """Write the directory of a run that failed at start: a `run.log` that ends with an error, and no journal.

    Return the path of the log. Its time of change is 60 seconds after now, so it is newer than each journal of the case.
    """
    log = journal_path(repo, run_id).parent / "run.log"
    log.parent.mkdir(parents=True)
    log.write_text(f"an earlier line of the log\n{GIT_ERROR}\n\n")
    later = time.time() + 60
    os.utime(log, (later, later))
    return str(log)


def test_watch_and_status_without_a_run_id_report_a_newest_run_with_only_a_log_as_failed_at_start(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "older", run_start("older", "a", run_branch="run/older"), run_end("built", ["a"]))
    log = failed_start(repo, "newer")
    line = f"no run is active; the newest run newer failed at start: {GIT_ERROR}; log {log}\n"

    watch_code, watched, watch_err = watch(repo, capsys)
    status_code, shown, status_err = status(repo, capsys)
    json_code, snapshot, json_err = status(repo, capsys, "--json")

    assert (watch_code, watched, watch_err) == (1, line, "")
    assert (status_code, shown, status_err) == (0, line, "")
    assert (json_code, snapshot) == (2, "")
    assert json_err == f"delegate status: {line}"


def test_watch_without_a_run_id_reports_a_failed_start_also_when_no_run_has_a_journal(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    log = failed_start(repo, "only")

    code, out, err = watch(repo, capsys)

    assert (code, out, err) == (1, f"no run is active; the newest run only failed at start: {GIT_ERROR}; log {log}\n", "")


def test_a_run_with_only_a_log_whose_lock_names_a_live_process_has_not_failed_at_start(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "older", run_start("older", "a", run_branch="run/older"), run_end("built", ["a"]))
    failed_start(repo, "starting")

    # The run process holds its lock and has not written its journal yet: it is active by the rule of the lock.
    with live_lock(journal_path(repo, "starting"), "run/starting"):
        watch_code, watched, _ = watch(repo, capsys)
        status_code, shown, _ = status(repo, capsys)

    assert "failed at start" not in watched + shown
    assert watch_code == 0 and watched.startswith("no run is active; the newest run older ended built at ")
    assert status_code == 0 and shown.splitlines()[0].startswith("run older: ")


def test_a_run_with_an_empty_log_and_no_journal_has_not_failed_at_start(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "older", run_start("older", "a", run_branch="run/older"), run_end("built", ["a"]))
    # `run --detach` makes the log before the run process starts. The process writes nothing to it before it takes its lock.
    log = journal_path(repo, "starting").parent / "run.log"
    log.parent.mkdir(parents=True)
    log.touch()

    code, out, _ = watch(repo, capsys)

    assert code == 0 and out.startswith("no run is active; the newest run older ended built at ")


def test_watch_without_a_run_id_follows_the_one_active_run_when_a_newer_run_failed_at_start(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    active = write_journal(repo, "older", run_start("older", "a", run_branch="run/older"), step_start("a"))
    failed_start(repo, "newer")
    monkeypatch.setattr(time, "sleep", lambda seconds: append_events(active, step_end("a"), run_end("built", ["a"])))

    with live_lock(active, "run/older"):
        code, out, err = watch(repo, capsys)

    assert (code, err) == (0, "")
    assert "run_id=older" in out and "failed at start" not in out


def test_watch_from_a_position_without_a_run_id_follows_the_one_active_run_when_a_newer_run_ended(tmp_path, capsys, monkeypatch):
    # #137: with `--from` and no run id, `watch` followed the newest run, and not the active run.
    repo = bare_repo(tmp_path)
    active = write_journal(repo, "older", run_start("older", "a", run_branch="run/older"), step_start("a"))
    write_journal(repo, "newer", run_start("newer", "b", run_branch="run/newer"), run_end("failed", [], ["b"]))
    monkeypatch.setattr(time, "sleep", lambda seconds: append_events(active, step_end("a"), run_end("built", ["a"])))

    with live_lock(active, "run/older"):
        code, out, err = watch(repo, capsys, "--from", "1")

    # The events after position 1 of the active run: its ticket a starts and ends, and the run is built.
    assert (code, err) == (0, "")
    assert [line.split()[2] for line in out.splitlines()[:3]] == ["step-start", "step-end", "run-end"]
    assert "ticket=a" in out.splitlines()[0] and "ticket=b" not in out
    assert out.splitlines()[3:] == ["position 4"]


def test_watch_from_a_position_without_a_run_id_and_two_active_runs_not_on_a_terminal_follows_none_and_exits_2(
    tmp_path, capsys,
):
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code, out, err = watch(repo, capsys, "--from", "1")

    assert (code, out) == (2, "")
    assert err == (
        "delegate watch: 2 runs are active in this repository; give a run id: "
        "r-economy (run/two, running), r-assure (run/one, running)\n"
    )


def test_watch_from_a_position_without_a_run_id_reports_a_newest_run_that_failed_at_start_in_one_line(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "older", run_start("older", "a", run_branch="run/older"), run_end("built", ["a"]))
    log = failed_start(repo, "newer")

    code, out, err = watch(repo, capsys, "--from", "1")

    # The newest run has no journal to go on in, so `watch` gives its line, as with no `--from`.
    assert (code, out, err) == (1, f"no run is active; the newest run newer failed at start: {GIT_ERROR}; log {log}\n", "")
