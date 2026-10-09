"""`delegate watch`: follow the journal of a run, print each event, and exit with a code for the reason.

A case calls `delegate.main(["watch", ...])` against a real temporary git
repository. A journal is written with the journal class of the engine, one event
at a time, as a run in progress writes it; a case that follows a live run appends
from a second thread while `watch` polls. A case that needs a real run uses the
scripted adapter, which is removed before `watch` starts, so no case has an
adapter installed when `watch` runs.
"""

import threading
import time

from delegate import delegate

from .support import append_events, bare_repo, run_end, run_start, step_end, step_start, write_journal

POLL = ["--poll-seconds", "0.02"]


def watch(repo, capsys, *extra):
    code = delegate.main(["watch", "--repo", str(repo), *POLL, *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def lines_of(out: str, kind: str) -> list[str]:
    return [line for line in out.splitlines() if f" {kind} " in line or line.endswith(f" {kind}")]


def test_watch_of_a_run_that_built_prints_each_event_in_order_and_exits_0(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), step_end("a"), run_end("built", ["a"]))

    code, out, err = watch(repo, capsys, "r1")

    assert (code, err) == (0, "")
    printed = out.splitlines()
    assert [line.split()[0] for line in printed[:4]] == ["1", "2", "3", "4"]
    assert [line.split()[2] for line in printed[:4]] == ["run-start", "step-start", "step-end", "run-end"]
    assert "ticket=a state=built" in printed[2]
    assert "result=built" in printed[3]


def test_watch_of_a_run_that_ends_failed_exits_1(tmp_path, capsys):
    # A skip is no problem event, so a run whose only record of failure is the run-end has no problem to report first.
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), run_end("failed", [], ["a"]))

    code, out, _ = watch(repo, capsys, "r1")

    assert code == 1
    assert lines_of(out, "run-end")


def test_watch_prints_events_as_they_are_appended_and_exits_when_the_run_ends(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"))

    def append_later():
        for events in ([step_start("a")], [step_end("a")], [run_end("built", ["a"])]):
            time.sleep(0.15)
            append_events(path, *events)

    writer = threading.Thread(target=append_later)
    writer.start()
    code, out, err = watch(repo, capsys, "r1")
    writer.join()

    assert (code, err) == (0, "")
    assert [line.split()[2] for line in out.splitlines()[:4]] == ["run-start", "step-start", "step-end", "run-end"]


def test_watch_without_a_run_id_follows_the_newest_run(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "old", run_start("old", "a"), run_end("failed", [], ["a"]))
    write_journal(repo, "new", run_start("new", "b"), run_end("built", ["b"]))

    code, out, _ = watch(repo, capsys)

    assert code == 0
    assert "run_id=new" in out and "run_id=old" not in out


def test_watch_of_an_unknown_run_exits_2_and_names_the_run_id(tmp_path, capsys):
    repo = bare_repo(tmp_path)

    code, out, err = watch(repo, capsys, "no-such-run")

    assert (code, out) == (2, "")
    assert "no-such-run" in err


def test_watch_waits_for_the_end_of_a_line_that_is_half_written_and_does_not_fail_on_it(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"))
    done = write_journal(repo, "scratch", run_start("scratch", "a"), run_end("built", ["a"]))
    second_line = done.read_text().splitlines()[1]  # the run-end event, with seq 2
    half = len(second_line) // 2

    def finish_the_line():
        with path.open("a") as handle:
            handle.write(second_line[:half])
        time.sleep(0.2)
        with path.open("a") as handle:
            handle.write(second_line[half:] + "\n")

    writer = threading.Thread(target=finish_the_line)
    writer.start()
    code, out, err = watch(repo, capsys, "r1")
    writer.join()

    assert (code, err) == (0, "")
    assert [line.split()[2] for line in out.splitlines()[:2]] == ["run-start", "run-end"]


def test_watch_exits_2_and_names_the_line_when_the_journal_holds_a_line_that_is_not_an_event(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"))
    with path.open("a") as handle:
        handle.write("this is not json\n")

    code, _, err = watch(repo, capsys, "r1")

    assert code == 2
    assert str(path) in err and "line 2" in err
