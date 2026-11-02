"""Count the processes that the tests start, and the `git` processes that the test code starts.

On a machine with endpoint security, each program start is slow, and the
setup of the tests starts many `git` processes. This module counts them, so a
change to a helper shows its cost as a number.

The count of all processes (#229) holds each process that a test process of
the suite starts: the test code and the engine that a test calls in its own
process. The audit events of a process start are the boundary: `subprocess.Popen`,
`os.system`, `os.fork`, `os.forkpty`, `os.spawn` and `os.posix_spawn`. The
`subprocess` module raises `os.posix_spawn` after `subprocess.Popen` for the same
process, so that event counts only from a caller outside the `subprocess`
module. A process that a child process starts (a command of a gate script, a
`git` process of a `delegate` subprocess) is outside the count, because the
audit hook is in the test process only.

The count holds each `git` process that test code starts: a helper, a
fixture, a test body, or the scripted adapter. It does not hold a process
that the engine starts, because those starts are the behaviour under test.
The audit event `subprocess.Popen` is the boundary: Python raises it for each
process that the `subprocess` module starts. The code that started the process
is the innermost frame outside the `subprocess` module. When that frame is in
the `tests` directory, the process counts.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TESTS = str(Path(__file__).resolve().parent) + os.sep
_SUBPROCESS = os.path.realpath(subprocess.__file__)

_started = 0
_all_started = 0

# The audit events of a process start. The `os` events come from the `subprocess` module too: see `_hook`.
_STARTS: frozenset[str] = frozenset(
    {"subprocess.Popen", "os.system", "os.fork", "os.forkpty", "os.spawn", "os.posix_spawn"}
)


def started() -> int:
    """The number of `git` processes that the test code of this process started since the import of this module."""
    return _started


def all_started() -> int:
    """The number of processes that this process started since the import of this module, from any code."""
    return _all_started


def _is_git(args: object) -> bool:
    if isinstance(args, (list, tuple)):
        words = [os.fsdecode(args[0])] if args else []
    else:
        words = os.fsdecode(args).split(maxsplit=1)
    return bool(words) and os.path.basename(words[0]) == "git"


def _from_tests() -> bool:
    frame = sys._getframe(2)
    while frame is not None and os.path.realpath(frame.f_code.co_filename) == _SUBPROCESS:
        frame = frame.f_back
    return frame is not None and os.path.realpath(frame.f_code.co_filename).startswith(TESTS)


def _hook(event: str, args: tuple[object, ...]) -> None:
    global _started, _all_started
    if event not in _STARTS:
        return
    if event == "subprocess.Popen":
        _all_started += 1
        if _is_git(args[1]) and _from_tests():
            _started += 1
    elif os.path.realpath(sys._getframe(1).f_code.co_filename) != _SUBPROCESS:
        _all_started += 1


sys.addaudithook(_hook)
