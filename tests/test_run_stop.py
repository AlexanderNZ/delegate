"""`delegate stop <run-id>` stops a run now, and the run can be resumed (#184).

The command sends SIGTERM to the process that holds the lock of the run. The engine stops as for Ctrl-C (#178): it
ends the process group of its child, writes `interrupted` and releases the lock. The command waits for the lock, then
prints the command that goes on with the run.

Each case calls `delegate.main` against a real temporary git repository. The detached run is a real process. The
`claude` process is the system boundary: a stand-in on PATH that stays open, as an idle session does.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from delegate.cli import delegate
from delegate.cli import stop as stop_command

from . import claude_fake
from .support import state_dir
from .support import (
    WORKFLOW, bare_repo, event_names, is_gone, journal_path, make_repo, read_journal_file, run_end, run_main, run_start,
    write_project_agents,
    stale_lock, write_journal,
)

CLAUDE_CODE = WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"')

# The longest time that a test waits for a step of a run process.
WAIT_SECONDS: float = 120.0

# A process that takes the lock of a run branch, ignores SIGTERM, and stays alive: an engine that does not stop.
STUBBORN_HOLDER = """\
import signal, sys, time
from pathlib import Path
from delegate.run.lock import run_lock

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with run_lock(Path(sys.argv[1]), sys.argv[2], sys.argv[3]):
    print("ready", flush=True)
    time.sleep(300)
"""


def stop(repo: Path, capsys, *args: str) -> tuple[int, str, str]:
    code = delegate.main(["stop", *args, "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def wait_until(condition, what: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, f"{what} did not happen in {WAIT_SECONDS} seconds"
        time.sleep(0.1)


def reap_in_background(repo: Path) -> None:
    """Wait for the process that holds the lock, as the parent of a run process does.

    The test process started the detached run, so it is the parent of the run process. A real detached run has
    the init process as its parent, which reaps it. A process that is not reaped stays a zombie and shows as alive.
    """
    (lock,) = (state_dir(repo) / "locks").glob("*.lock")
    pid = int(json.loads(lock.read_text())["pid"])

    def reap() -> None:
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass  # another wait reaped it

    threading.Thread(target=reap, daemon=True).start()


def test_stop_ends_a_detached_run_now_and_a_resume_goes_on_with_the_step(tmp_path, monkeypatch, capsys):
    child_pid = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_HARNESS_CHILD", str(child_pid))
    fake = claude_fake.install(tmp_path, monkeypatch, "", hold_seconds=300)
    repo = make_repo(tmp_path, CLAUDE_CODE)
    write_project_agents(repo)
    code, out, _ = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--detach")
    assert code == 0
    run_id = next(line.split()[1] for line in out.splitlines() if line.startswith("run "))
    wait_until(lambda: fake.calls() and child_pid.is_file() and child_pid.read_text().isdigit(), "the start of the harness")
    harness, child = fake.calls()[0]["pid"], int(child_pid.read_text())
    reap_in_background(repo)
    try:
        code, out, err = stop(repo, capsys, run_id)
    finally:
        for pid in (harness, child):
            if not is_gone(pid, 0):
                os.kill(pid, signal.SIGKILL)  # keep the machine clean when the run left a process alive

    assert (code, err) == (0, "")
    assert f"delegate run --resume {run_id}" in out
    events = read_journal_file(repo, run_id)
    assert event_names(events)[-1] == "interrupted"
    assert "run-end" not in event_names(events)
    assert list((state_dir(repo) / "locks").glob("*.lock")) == []
    assert is_gone(harness, 0), "the harness process stayed alive"
    assert is_gone(child, 0), "a child process of the harness stayed alive"

    monkeypatch.setenv("FAKE_HARNESS_HOLD", "0")
    run_main(capsys, "--resume", run_id, "--repo", str(repo))
    assert len(fake.calls()) >= 2, "the resume did not start the step again"
    assert "resume" in event_names(read_journal_file(repo, run_id))


def test_stop_of_an_ended_run_exits_1_and_gives_the_reason(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "20261029T000000Z-0001", run_start("20261029T000000Z-0001", "a"), run_end("built", built=["a"]))

    code, out, err = stop(repo, capsys, "20261029T000000Z-0001")

    assert (code, out) == (1, "")
    assert "ended" in err and "built" in err and "20261029T000000Z-0001" in err


def test_stop_of_a_run_with_no_live_process_exits_1_and_names_the_resume(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    journal = write_journal(repo, "20261029T000000Z-0002", run_start("20261029T000000Z-0002", "a"))
    stale_lock(journal)

    code, out, err = stop(repo, capsys, "20261029T000000Z-0002")

    assert (code, out) == (1, "")
    assert "no live process" in err
    assert "delegate run --resume 20261029T000000Z-0002" in err


def test_stop_of_a_paused_run_exits_1_and_names_the_resume(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "20261029T000000Z-0003", run_start("20261029T000000Z-0003", "a"), ("paused", {"before": "a"}))

    code, out, err = stop(repo, capsys, "20261029T000000Z-0003")

    assert (code, out) == (1, "")
    assert "paused" in err
    assert "delegate run --resume 20261029T000000Z-0003" in err


def test_stop_of_an_unknown_run_exits_1(tmp_path, capsys):
    repo = bare_repo(tmp_path)

    code, out, err = stop(repo, capsys, "20261029T000000Z-9999")

    assert (code, out) == (1, "")
    assert "20261029T000000Z-9999" in err


def test_stop_exits_1_and_says_so_when_the_lock_is_still_held_at_the_end_of_the_wait(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(stop_command, "WAIT_SECONDS", 1.0)
    repo = bare_repo(tmp_path)
    journal = write_journal(repo, "20261029T000000Z-0004", run_start("20261029T000000Z-0004", "a"))
    holder = subprocess.Popen(
        [sys.executable, "-c", STUBBORN_HOLDER, str(journal.parents[2]), "run/demo", "20261029T000000Z-0004"],
        env={**os.environ}, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "ready"
        code, out, err = stop(repo, capsys, "20261029T000000Z-0004")
    finally:
        holder.kill()
        holder.wait()

    assert code == 1
    assert "still holds the lock" in err and str(holder.pid) in err
    assert "resume" not in out
    assert journal_path(repo, "20261029T000000Z-0004").is_file()
