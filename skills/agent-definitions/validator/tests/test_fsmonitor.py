"""The engine and the test suite leave no git fsmonitor daemon behind.

With `core.fsmonitor` true, git starts a `git fsmonitor--daemon` for each
repository it touches, and the daemon outlives the directory. The cases here
set the user's setting through the git configuration environment, run `delegate
run` through `main(argv)` with the scripted adapter, and read what git reports
for the worktrees and the copies that the engine makes.
"""

import os
import socket
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from agent_definitions import adapters, delegate

from .support import ScriptedAdapter, git, make_repo


def git_status(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in `path` and return the result, whatever the exit code is."""
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


def user_enables_fsmonitor(monkeypatch) -> None:
    """Make `core.fsmonitor=true` the effective setting of every git command, as a global setting does.

    The entry goes after the entries that the suite sets, because git takes the last value.
    """
    count = int(os.environ["GIT_CONFIG_COUNT"])
    monkeypatch.setenv(f"GIT_CONFIG_KEY_{count}", "core.fsmonitor")
    monkeypatch.setenv(f"GIT_CONFIG_VALUE_{count}", "true")
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(count + 1))


def daemon_pid(path: Path) -> int | None:
    """The process id of the fsmonitor daemon that serves the repository at `path`, or None when none serves it.

    The daemon listens on a socket in the git directory. The kernel tells the
    process id of the peer that listens on a socket.
    """
    ipc = path / ".git" / "fsmonitor--daemon.ipc"
    if not ipc.exists():
        return None
    with socket.socket(socket.AF_UNIX) as client:
        try:
            client.connect(str(ipc))
        except ConnectionRefusedError:
            return None
        if sys.platform == "darwin":
            return client.getsockopt(0, 2)  # SOL_LOCAL, LOCAL_PEERPID
        return struct.unpack("3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[0]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def registered():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


@pytest.fixture
def no_daemon_survives():
    """Stop every daemon in the given paths when the test ends, so a failing test leaks none."""
    paths: list[Path] = []
    yield paths
    for path in paths:
        if path.exists():
            git_status(path, "fsmonitor--daemon", "stop")


def run(repo, capsys):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_a_git_command_of_the_suite_runs_with_fsmonitor_off(tmp_path):
    repo = make_repo(tmp_path)

    assert git(repo, "config", "core.fsmonitor").strip() == "false"
