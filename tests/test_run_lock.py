"""The run lock: a second run on one run branch exits non-zero. A lock file that no process holds is free (#288).

The lock is a kernel lock (`flock`) on the lock file. A killed run is a real one: a child process runs `delegate run`
with a scripted adapter and SIGKILLs itself, so it leaves a lock file that names a process that no longer exists. The
kernel released its lock. Each case calls `delegate.main` against a real temporary git repository, or takes the lock
in real child processes.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from delegate.cli import delegate

from .support import state_dir
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
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    assert second_code == 1 and "Traceback" not in second_err
    assert "run/demo" in second_err and run_dir.name in second_err and f"process {os.getpid()}" in second_err
    assert "a.txt" in tree(repo, "run/demo")


def test_a_resume_of_the_same_run_takes_over_the_lock_of_its_killed_process_with_no_flag_and_says_so(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id, dead_pid = kill_a_run(repo, 3)

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 0 and "Traceback" not in err
    assert err.splitlines() == [
        f"delegate run: took over the lock of run/demo from run {run_id}, process {dead_pid}, which holds the lock no longer"
    ]
    events = read_journal_file(repo, run_id)
    broken = next(e for e in events if e["event"] == "lock-broken")
    assert (broken["run_id"], broken["pid"]) == (run_id, dead_pid)
    assert [ticket_of(c) for c in scripted.calls] == ["b", "c"]
    assert {"a.txt", "b.txt", "c.txt"} <= set(tree(repo, "run/demo"))


@pytest.mark.parametrize("extra", [[], ["--break-lock"]], ids=["plain", "with-break-lock"])
def test_a_resume_of_the_same_run_is_refused_while_the_process_of_its_lock_is_alive(tmp_path, capsys, scripted, extra):
    repo = make_repo(tmp_path, THREE)
    seen = []

    def resume_inside(request):
        if ticket_of(request) == "b":
            (run_dir,) = (state_dir(repo) / "runs").iterdir()
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


def rewrite_lock(repo, run_id: str, pid: int) -> None:
    """Make the lock file of run/demo name `run_id` and `pid`, as a lock file that a killed run left, after a reuse of its process id."""
    (lock,) = (state_dir(repo) / "locks").glob("*.lock")
    lock.write_text(json.dumps({"run_id": run_id, "pid": pid}))


def test_a_lock_file_that_names_a_live_process_that_holds_no_lock_is_free_and_a_resume_takes_it(tmp_path, capsys, scripted):
    # After a reboot or a reuse of the process id, the lock file of a killed run names a process that exists and is not the run.
    repo = make_repo(tmp_path, THREE)
    run_id, _ = kill_a_run(repo, 3)
    rewrite_lock(repo, run_id, os.getpid())

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 0 and "Traceback" not in err
    assert err.splitlines() == [
        f"delegate run: took over the lock of run/demo from run {run_id}, process {os.getpid()}, which holds the lock no longer"
    ]
    (broken,) = [e for e in read_journal_file(repo, run_id) if e["event"] == "lock-broken"]
    assert (broken["run_id"], broken["pid"]) == (run_id, os.getpid())
    assert [ticket_of(c) for c in scripted.calls] == ["b", "c"]


def test_a_lock_file_of_another_run_that_no_process_holds_is_free_and_needs_no_break_lock(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id, dead_pid = kill_a_run(repo, 3)
    rewrite_lock(repo, "20260101T000000Z-0000", dead_pid)

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 0 and "Traceback" not in err
    assert err.splitlines() == [
        f"delegate run: took over the lock of run/demo from run 20260101T000000Z-0000, process {dead_pid}, which holds the lock no longer"
    ]
    (broken,) = [e for e in read_journal_file(repo, run_id) if e["event"] == "lock-broken"]
    assert (broken["run_id"], broken["pid"]) == ("20260101T000000Z-0000", dead_pid)
    assert {"a.txt", "b.txt", "c.txt"} <= set(tree(repo, "run/demo"))


def test_break_lock_says_that_the_lock_is_free_when_no_process_holds_it(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id, dead_pid = kill_a_run(repo, 3)

    code, out, err = resume_main(repo, capsys, run_id, "--break-lock")

    assert code == 0 and "Traceback" not in err
    assert err.splitlines() == [
        "delegate run: --break-lock: the lock of run/demo is free, because no process holds it; "
        "the flag is not necessary, and 2.0.0 removes it",
        f"delegate run: took over the lock of run/demo from run {run_id}, process {dead_pid}, which holds the lock no longer",
    ]


def test_a_run_is_refused_while_another_process_holds_the_lock(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    with lock_held_by_a_child(state_dir(repo), "run/demo", "20260101T000000Z-0001") as holder:
        code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1 and "Traceback" not in err
    assert f"run branch run/demo is in use by run 20260101T000000Z-0001 (process {holder})" in err
    assert scripted.calls == [] and not (state_dir(repo) / "runs").exists()


def test_of_several_processes_that_take_one_stale_lock_at_the_same_time_one_only_gets_it(tmp_path):
    # Two resumes that both found a stale lock could each remove the lock of the other, and both held the run.
    state = tmp_path / "state"
    (state / "locks").mkdir(parents=True)
    (state / "locks" / "run%2Fdemo.lock").write_text(json.dumps({"run_id": "r1", "pid": os.getpid()}))
    go = tmp_path / "go"
    children = [
        subprocess.Popen(
            [sys.executable, "-c", RACE_SCRIPT, str(state), "run/demo", "r1", str(go)],
            cwd=ROOT, env=child_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(6)
    ]
    time.sleep(1.0)  # each child starts and waits for the go file
    go.write_text("")
    results = [child.communicate(timeout=60) for child in children]

    assert [err for _, err in results if err] == []
    assert sorted(out.strip() for out, _ in results) == ["got"] + ["refused"] * 5


ROOT = Path(__file__).resolve().parents[1]

# A child process that waits for a go file, then takes the lock of a run branch for a run id and holds it for 3 seconds.
RACE_SCRIPT = """\
import sys, time
from pathlib import Path
from delegate.run.lock import LockError, run_lock

go = Path(sys.argv[4])
while not go.exists():
    time.sleep(0.005)
try:
    with run_lock(Path(sys.argv[1]), sys.argv[2], sys.argv[3]):
        print("got", flush=True)
        time.sleep(3)
except LockError:
    print("refused", flush=True)
"""

# A child process that takes the lock of a run branch, prints ready, and holds the lock until its stdin closes.
HOLD_SCRIPT = """\
import sys
from pathlib import Path
from delegate.run.lock import run_lock

with run_lock(Path(sys.argv[1]), sys.argv[2], sys.argv[3]):
    print("ready", flush=True)
    sys.stdin.read()
"""


def child_env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT / "src"), os.environ.get("PYTHONPATH")]))}


@contextlib.contextmanager
def lock_held_by_a_child(state: Path, run_branch: str, run_id: str):
    """Hold the lock of `run_branch` in a child process while the block runs. Yield the process id of the child."""
    child = subprocess.Popen(
        [sys.executable, "-c", HOLD_SCRIPT, str(state), run_branch, run_id],
        cwd=ROOT, env=child_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        yield child.pid
    finally:
        child.stdin.close()
        child.wait(timeout=60)


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
    assert delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)]) == 130  # Ctrl-C, as a shell gives it (#109)
    capsys.readouterr()
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    assert list((state_dir(repo) / "locks").glob("*.lock")) == []
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
    assert code == 4  # a crash stops the run as a halt does (#281)
    scripted.on_specialist = None

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
