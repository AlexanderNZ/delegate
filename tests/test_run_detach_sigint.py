"""A detached run that gets SIGINT during an agent session stops within 10 seconds (#198).

The run starts as a coordinator starts it from a background shell: `delegate run --detach` in a shell with `&`. A
non-interactive shell sets SIGINT to ignored for a background command, and the setsid child of the command inherits
that setting. The `claude` process is the system boundary: a stand-in on PATH that ignores SIGINT and stays open, as the
session of a harness that is busy does. Each case runs a real process against a real temporary git repository.
"""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

from . import claude_fake
from .support import state_dir
from .support import GIT_IDENTITY, WORKFLOW, event_names, is_gone, make_repo, read_journal_file

CLAUDE_CODE = WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"')

# The stand-in of the harness. A specialist session commits `feature.txt` and writes its report, and then, like a
# verifier session does, every session starts a child process, ignores SIGINT, and stays open. With `FAKE_HOLD_IN`
# set to `specialist`, the specialist session stays open before it works. With `verifier`, the specialist session
# ends, and the verifier session stays open.
STAND_IN = """\
#!{python}
import json, os, re, signal, subprocess, sys, time
signal.signal(signal.SIGINT, signal.SIG_IGN)
role = "verifier" if any("verifier" in argument for argument in sys.argv) else "specialist"
prompt = sys.stdin.read()
if role == os.environ["FAKE_HOLD_IN"]:
    with open(os.environ["FAKE_PIDS"], "a") as pids:
        pids.write(str(os.getpid()) + "\\n")
        pids.write(str(subprocess.Popen(["sleep", "300"]).pid) + "\\n")
    time.sleep(300)
    sys.exit(0)
with open(os.environ["FAKE_STREAM"]) as stream:
    sys.stdout.write(stream.read())
sys.stdout.flush()
def run(*command):
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()
with open("feature.txt", "w") as feature:
    feature.write("the feature\\n")
run("git", "add", "-A")
run("git", "commit", "-q", "-m", "feature")
head = run("git", "rev-parse", "HEAD")
report = re.search(r"absolute path `([^`]+)`", prompt).group(1)
with open(report, "w") as out:
    json.dump({{
        "ticket": "a", "status": "committed", "branch": run("git", "rev-parse", "--abbrev-ref", "HEAD"), "head_sha": head,
        "commits": [head], "gates_green": True, "summary": "Done.",
    }}, out)
"""

# The longest time that a test waits for a step of a process.
WAIT_SECONDS: float = 120.0

# The time in which a detached run must stop after SIGINT.
STOP_SECONDS: float = 10.0


def wait_until(condition, what: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, f"{what} did not happen in {WAIT_SECONDS} seconds"
        time.sleep(0.1)


def stop_with_sigint(tmp_path: Path, monkeypatch, hold_in: str) -> tuple[Path, str, float, list[int]]:
    """Start a detached run whose `hold_in` session stays open, send SIGINT to the run process, and wait for it to end.

    Return the repository, the run id, the seconds that the run process needed to end, and the process ids of the
    session that the stand-in held. The processes that stay alive after the test ends are killed.
    """
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(STAND_IN.format(python=sys.executable))
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    pids_file = tmp_path / "pids"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_HOLD_IN", hold_in)
    monkeypatch.setenv("FAKE_PIDS", str(pids_file))
    monkeypatch.setenv("FAKE_STREAM", str(claude_fake.FIXTURES / "finished.jsonl"))
    repo = make_repo(tmp_path, CLAUDE_CODE)
    started = tmp_path / "started.txt"
    # `&` in a shell that is not interactive: the command has SIGINT ignored, and the run process inherits that.
    command = f"{sys.executable} -m delegate.cli.delegate run {repo / 'workflow.toml'} --repo {repo} --detach > {started} 2>&1 &"
    subprocess.run(["sh", "-c", command], env={**os.environ, **GIT_IDENTITY}, check=True, stdin=subprocess.DEVNULL)
    pids: list[int] = []
    try:
        wait_until(lambda: started.is_file() and "run " in started.read_text(), "the start of the detached run")
        run_id = re.search(r"^run (\S+)$", started.read_text(), flags=re.MULTILINE).group(1)
        wait_until(lambda: pids_file.is_file() and len(pids_file.read_text().split()) == 2, f"the {hold_in} session")
        pids = [int(pid) for pid in pids_file.read_text().split()]
        (lock,) = (state_dir(repo) / "locks").glob("*.lock")
        engine = int(json.loads(lock.read_text())["pid"])

        before = time.monotonic()
        os.kill(engine, signal.SIGINT)
        ended = is_gone(engine, WAIT_SECONDS)
        seconds = time.monotonic() - before
        assert ended, "the run process did not end"
        return repo, run_id, seconds, pids
    finally:
        for pid in pids:
            if not is_gone(pid, 0):
                os.kill(pid, signal.SIGKILL)  # keep the machine clean when the run left a process alive


def assert_stopped(repo: Path, run_id: str, seconds: float, pids: list[int]) -> None:
    assert seconds < STOP_SECONDS, f"the run process needed {seconds:.1f} seconds to end"
    events = read_journal_file(repo, run_id)
    assert event_names(events)[-1] == "interrupted"
    assert "run-end" not in event_names(events)
    assert list((state_dir(repo) / "locks").glob("*.lock")) == []
    for pid in pids:
        assert is_gone(pid, 0), f"process {pid} of the session stayed alive"
    assert re.search(rf"go on with delegate run --resume {run_id}", (state_dir(repo) / "runs" / run_id / "run.log").read_text(), re.I)


def test_sigint_to_a_detached_run_in_a_specialist_session_stops_the_run_within_10_seconds(tmp_path, monkeypatch):
    repo, run_id, seconds, pids = stop_with_sigint(tmp_path, monkeypatch, "specialist")

    assert_stopped(repo, run_id, seconds, pids)


def test_sigint_to_a_detached_run_in_a_verifier_session_stops_the_run_within_10_seconds(tmp_path, monkeypatch):
    repo, run_id, seconds, pids = stop_with_sigint(tmp_path, monkeypatch, "verifier")

    assert_stopped(repo, run_id, seconds, pids)
