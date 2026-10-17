"""The run lock: a second run on one run branch exits non-zero. A resume takes over the stale lock of its own run, and a stale lock of another run needs an explicit flag.

A killed run is a real one: a child process runs `delegate run` with a scripted
adapter and SIGKILLs itself, so it leaves a lock that nobody releases. Each case
calls `delegate.main` against a real temporary git repository.
"""

import contextlib
import io
import os

import pytest

from delegate.cli import delegate

from .support import (
    event_names, finished_run, journal_path, kill_a_run, make_repo, read_journal, read_journal_file, resume_main, run_main, ticket_of, tree,
)
from .test_run_multi import HEAD, ticket
from .test_run_crash import CRASH, raise_in_b

THREE = HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c")



def nested_run(repo, *extra):
    """Start `delegate run` on the same run branch from inside a running adapter. Return (code, stderr)."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), *extra])
    return code, err.getvalue()


@pytest.mark.parametrize("extra", [[], ["--break-lock"]], ids=["plain", "with-break-lock"])
def test_a_second_run_on_a_run_branch_in_use_exits_non_zero_and_names_the_lock_holder(tmp_path, capsys, scripted, extra):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    seen = []
    scripted.on_specialist = lambda request: seen.append(nested_run(repo, *extra))

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")  # the first run is not harmed
    ((second_code, second_err),) = seen
    (run_dir,) = (repo / ".git" / "delegate" / "runs").iterdir()
    assert second_code == 1 and "Traceback" not in second_err
    assert "run/demo" in second_err and run_dir.name in second_err and f"process {os.getpid()}" in second_err
    assert "a.txt" in tree(repo, "run/demo")


def test_a_resume_of_the_same_run_takes_over_the_lock_of_its_killed_process_with_no_flag_and_says_so(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id, dead_pid = kill_a_run(repo, 3)

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 0 and "Traceback" not in err
    assert err.splitlines() == [
        f"delegate run: took over the lock of run/demo from run {run_id}, process {dead_pid}, which no longer exists"
    ]
    events = read_journal_file(repo, run_id)
    broken = next(e for e in events if e["event"] == "lock-broken")
    assert (broken["run_id"], broken["pid"]) == (run_id, dead_pid)
    assert [ticket_of(c) for c in scripted.calls] == ["b", "c"]
    assert {"a.txt", "b.txt", "c.txt"} <= set(tree(repo, "run/demo"))


def test_a_resume_with_break_lock_of_the_same_run_takes_over_the_lock_of_its_killed_process_with_no_note(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id, dead_pid = kill_a_run(repo, 3)

    code, out, err = resume_main(repo, capsys, run_id, "--break-lock")

    assert (code, err) == (0, "")
    broken = [e for e in read_journal_file(repo, run_id) if e["event"] == "lock-broken"]
    assert [(e["run_id"], e["pid"]) for e in broken] == [(run_id, dead_pid)]


@pytest.mark.parametrize("extra", [[], ["--break-lock"]], ids=["plain", "with-break-lock"])
def test_a_resume_of_the_same_run_is_refused_while_the_process_of_its_lock_is_alive(tmp_path, capsys, scripted, extra):
    repo = make_repo(tmp_path, THREE)
    seen = []

    def resume_inside(request):
        if ticket_of(request) == "b":
            (run_dir,) = (repo / ".git" / "delegate" / "runs").iterdir()
            seen.append((run_dir.name, journal_path(repo, run_dir.name).read_bytes(), nested_run(repo, "--resume", run_dir.name, *extra)))

    scripted.on_specialist = resume_inside

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")  # the run that holds the lock is not harmed
    ((run_id, before, (second_code, second_err)),) = seen
    assert second_code == 1 and "Traceback" not in second_err
    assert f"run branch run/demo is in use by run {run_id} (process {os.getpid()})" in second_err
    assert "took over" not in second_err
    events = read_journal_file(repo, run_id)
    assert "lock-broken" not in event_names(events) and event_names(events).count("resume") == 0
    assert journal_path(repo, run_id).read_bytes().startswith(before)


def test_a_lock_of_another_run_whose_process_no_longer_exists_still_needs_break_lock(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id, dead_pid = kill_a_run(repo, 3)
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1 and "Traceback" not in err
    assert (
        f"run branch run/demo is locked by run {run_id}, process {dead_pid}, which no longer exists; "
        "pass --break-lock to remove the lock" in err
    )
    assert "took over" not in err
    assert [p.name for p in (repo / ".git" / "delegate" / "runs").iterdir()] == [run_id]
    assert journal_path(repo, run_id).read_bytes() == before and scripted.calls == []


def test_the_lock_is_gone_when_a_run_ends(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id = finished_run(repo, capsys)

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1 and "lock" not in err


def test_the_lock_is_gone_when_a_run_is_stopped_by_an_interrupt_and_it_can_be_resumed(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)

    def interrupt(request):
        if ticket_of(request) == "b":
            raise KeyboardInterrupt

    scripted.on_specialist = interrupt
    with pytest.raises(KeyboardInterrupt):
        delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    capsys.readouterr()
    (run_dir,) = (repo / ".git" / "delegate" / "runs").iterdir()
    assert "run-end" not in event_names(read_journal_file(repo, run_dir.name))
    scripted.on_specialist = None

    code, out, err = resume_main(repo, capsys, run_dir.name)

    assert (code, err) == (0, "")
    assert {"a.txt", "b.txt", "c.txt"} <= set(tree(repo, "run/demo"))


def test_a_crashed_run_releases_its_lock(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, CRASH)
    scripted.on_specialist = raise_in_b
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
    assert code == 1

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1 and "lock" not in err
