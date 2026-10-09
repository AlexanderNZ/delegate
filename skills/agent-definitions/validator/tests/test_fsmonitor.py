"""The engine and the test suite leave no git fsmonitor daemon behind.

With `core.fsmonitor` true, git starts a `git fsmonitor--daemon` for each
repository it touches, and the daemon outlives the directory. The cases here
set the user's setting through the git configuration environment, run `delegate
run` through `main(argv)` with the scripted adapter, and read what git reports
for the worktrees and the copies that the engine makes.
"""

import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from agent_definitions import adapters, delegate

from .support import ScriptedAdapter, git, make_repo


def git_status(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in `path` and return the result, whatever the exit code is."""
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


GLOBAL_CONFIG: str = "[core]\n\tfsmonitor = true\n"


def user_enables_fsmonitor(monkeypatch, tmp_path: Path) -> Path:
    """Give the user a global git configuration with `core.fsmonitor=true`, and return its path.

    The suite turns the setting off in the git configuration environment. That
    scope outranks every file, including the configuration of a worktree, so this
    drops the entries of the suite for the test. The global file then holds the setting, as it does for a user.
    """
    path = tmp_path / "global-gitconfig"
    path.write_text(GLOBAL_CONFIG)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    monkeypatch.delenv("GIT_CONFIG_COUNT")
    return path


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


def test_the_ticket_worktree_runs_with_fsmonitor_off_when_the_user_turns_it_on(
    tmp_path, capsys, registered, monkeypatch, no_daemon_survives
):
    repo = make_repo(tmp_path)
    user_enables_fsmonitor(monkeypatch, tmp_path)
    assert git(repo, "config", "core.fsmonitor").strip() == "true"
    no_daemon_survives.append(repo)

    code, out, err = run(repo, capsys)

    (call,) = registered.calls
    no_daemon_survives.append(call.cwd)
    assert (code, err) == (0, "")
    assert git(call.cwd, "config", "core.fsmonitor").strip() == "false"
    assert git_status(call.cwd, "fsmonitor--daemon", "status").returncode != 0


def test_the_verifier_copy_runs_with_fsmonitor_off_when_the_user_turns_it_on(
    tmp_path, capsys, registered, monkeypatch, no_daemon_survives
):
    repo = make_repo(tmp_path)
    user_enables_fsmonitor(monkeypatch, tmp_path)
    no_daemon_survives.append(repo)
    seen: dict[str, object] = {}

    def look_at_copy(request):
        no_daemon_survives.append(request.cwd)
        seen["fsmonitor"] = git(request.cwd, "config", "core.fsmonitor").strip()
        seen["daemon_running"] = git_status(request.cwd, "fsmonitor--daemon", "status").returncode == 0

    registered.during_verifier = look_at_copy

    code, out, err = run(repo, capsys)

    (call,) = registered.calls
    no_daemon_survives.append(call.cwd)
    assert (code, err) == (0, "")
    assert seen == {"fsmonitor": "false", "daemon_running": False}


def test_a_run_leaves_the_configuration_of_the_user_and_of_the_repository_as_it_was(
    tmp_path, capsys, registered, monkeypatch, no_daemon_survives
):
    repo = make_repo(tmp_path)
    global_config = user_enables_fsmonitor(monkeypatch, tmp_path)
    no_daemon_survives.append(repo)

    code, out, err = run(repo, capsys)

    (call,) = registered.calls
    no_daemon_survives.append(call.cwd)
    assert (code, err) == (0, "")
    assert git_status(repo, "config", "--local", "--get", "core.fsmonitor").returncode == 1
    assert git(repo, "config", "core.fsmonitor").strip() == "true"
    assert global_config.read_text() == GLOBAL_CONFIG


def test_the_engine_stops_the_fsmonitor_daemon_of_the_verifier_copy_before_it_removes_the_copy(
    tmp_path, capsys, registered, monkeypatch
):
    repo = make_repo(tmp_path)
    # The socket of the daemon has a path limit of about 100 characters. The temporary directory of a Nix shell is longer.
    short = Path(tempfile.mkdtemp(prefix="fs", dir="/tmp"))
    monkeypatch.setattr(tempfile, "tempdir", str(short))
    started: list[int] = []
    alive_at_removal: list[bool] = []

    def start_a_daemon_in_the_copy(request):
        # The verifier starts a daemon by hand: the setting of the copy does not stop an explicit start.
        git(request.cwd, "fsmonitor--daemon", "start")
        pid = daemon_pid(request.cwd)
        assert pid is not None
        started.append(pid)

    registered.during_verifier = start_a_daemon_in_the_copy
    remove_tree = shutil.rmtree

    def watch_removal(path, *args, **kwargs):
        # The filesystem is the boundary. A daemon that quits by itself once its directory is gone
        # is a race, so the test reads the daemon at the moment the engine removes the directory.
        if started:
            alive_at_removal.append(any(alive(pid) for pid in started))
        remove_tree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", watch_removal)

    try:
        code, out, err = run(repo, capsys)
        assert (code, err) == (0, "")
        (call,) = registered.verifier_calls
        assert not call.cwd.exists()
        assert alive_at_removal == [False]
    finally:
        for pid in started:
            if alive(pid):
                os.kill(pid, 15)
        remove_tree(short, ignore_errors=True)
