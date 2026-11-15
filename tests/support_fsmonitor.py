"""The helpers of the tests of the file system monitor of git: the daemon of a repository, and the reason that none can start (#354)."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


GLOBAL_CONFIG: str = "[core]\n\tfsmonitor = true\n"


def daemon_pid(path: Path) -> int | None:
    """The process id of the fsmonitor daemon that serves the repository at `path`, or None when none serves it.

    The daemon listens on a socket in the git directory. The kernel tells the
    process id of the peer that listens on a socket. Only macOS has that call
    (`LOCAL_PEERPID`), so any other platform is an error. The probe skips the
    test on those platforms before this function runs.
    """
    if sys.platform != "darwin":
        raise RuntimeError(f"daemon_pid reads LOCAL_PEERPID, which {sys.platform!r} does not have")
    ipc = path / ".git" / "fsmonitor--daemon.ipc"
    if not ipc.exists():
        return None
    with socket.socket(socket.AF_UNIX) as client:
        try:
            client.connect(str(ipc))
        except ConnectionRefusedError:
            return None
        return client.getsockopt(0, 2)  # SOL_LOCAL, LOCAL_PEERPID


NO_DAEMON_REASON: str = (
    "this environment cannot run an fsmonitor daemon, for example a build sandbox or a git with no backend"
)


def why_no_daemon_can_start() -> str | None:
    """None when a daemon can start here and the test can observe it, or the reason to skip.

    The test reads the daemon's process id with `LOCAL_PEERPID`, which only
    macOS has, so every other platform skips. On some Linux builds git does run
    a daemon, but this test cannot observe it there. A git backend is not
    enough either: a build sandbox that has a backend can still refuse to run
    the daemon. So the probe starts a daemon in a short temporary repository,
    checks that it listens, and stops it. The path is short because of the
    socket path limit. The probe sets LC_ALL=C because git translates its
    messages.
    """
    if sys.platform != "darwin":
        return f"the test reads the daemon's process id with LOCAL_PEERPID, which {sys.platform!r} does not have"
    env = {**os.environ, "LC_ALL": "C"}
    probe = Path(tempfile.mkdtemp(prefix="fp", dir="/tmp"))
    try:
        subprocess.run(["git", "init", "--quiet", str(probe)], check=True, capture_output=True, env=env)
        started = subprocess.run(
            ["git", "-C", str(probe), "fsmonitor--daemon", "start"], capture_output=True, text=True, env=env
        )
        try:
            if started.returncode != 0:
                if "not supported on this platform" in started.stderr:
                    return "this git has no fsmonitor daemon backend on this platform, so no daemon can leak"
                return NO_DAEMON_REASON
            if daemon_pid(probe) is None:
                return NO_DAEMON_REASON
            return None
        finally:
            subprocess.run(
                ["git", "-C", str(probe), "fsmonitor--daemon", "stop"], capture_output=True, text=True, env=env
            )
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
