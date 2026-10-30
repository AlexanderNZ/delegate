"""SIGTERM and SIGHUP stop `delegate run` as Ctrl-C does, and no child process of the run stays alive (#178).

Each case starts `delegate run` as a real process against a real temporary git repository. The `claude` process is
the system boundary: a stand-in on PATH that stays open, and starts a child process of its own, as a harness does.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from . import claude_fake
from .support import state_dir
from .support import GIT_IDENTITY, WORKFLOW, event_names, is_gone, make_repo, read_journal_file

CLAUDE_CODE = WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"')

# The longest time that a test waits for a step of the run process.
WAIT_SECONDS: float = 120.0


def start_run(repo: Path) -> subprocess.Popen[str]:
    """`delegate run` for the workflow of `repo` in a new process, with the import path of the tree under test."""
    return subprocess.Popen(
        [sys.executable, "-m", "delegate.cli.delegate", "run", str(repo / "workflow.toml"), "--repo", str(repo)],
        env={**os.environ, **GIT_IDENTITY}, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def wait_until(condition, what: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, f"{what} did not happen in {WAIT_SECONDS} seconds"
        time.sleep(0.1)


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGHUP])
def test_a_signal_stops_the_run_as_ctrl_c_does_and_no_process_of_the_harness_stays_alive(tmp_path, monkeypatch, stop_signal):
    child_pid = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_HARNESS_CHILD", str(child_pid))
    fake = claude_fake.install(tmp_path, monkeypatch, "", hold_seconds=300)
    repo = make_repo(tmp_path, CLAUDE_CODE)
    engine = start_run(repo)
    harness = child = None
    try:
        wait_until(lambda: fake.calls() and child_pid.is_file() and child_pid.read_text().isdigit(), "the start of the harness")
        harness, child = fake.calls()[0]["pid"], int(child_pid.read_text())

        engine.send_signal(stop_signal)
        out, err = engine.communicate(timeout=WAIT_SECONDS)
    finally:
        engine.kill()
        for pid in (harness, child):
            if pid is not None and not is_gone(pid, 0):
                os.kill(pid, signal.SIGKILL)  # keep the machine clean when the run left a process alive

    assert engine.returncode == 130, err
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    events = read_journal_file(repo, run_dir.name)
    assert event_names(events)[-1] == "interrupted"
    assert "run-end" not in event_names(events)
    assert f"go on with delegate run --resume {run_dir.name}".lower() in err.lower()
    assert list((state_dir(repo) / "locks").glob("*.lock")) == []
    assert is_gone(harness), "the harness process stayed alive"
    assert is_gone(child), "a child process of the harness stayed alive"
