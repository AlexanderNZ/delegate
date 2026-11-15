"""No test waits for a process with a sleep of a fixed time (#367).

A fixed sleep is too long on a quiet machine and too short on a loaded one. A test that waits for a process waits for an
observable fact (a journal line, a pid file, a lock, the end of a process) with `wait_for` or `poll_until` of
`tests/support.py`, which poll a short time and stop at a limit.

The first case reads the source of each Python file under `tests/` and fails for a call of `time.sleep` that is not in
`ALLOWED`. The list names the sleeps that are part of the behaviour under test, or that are a poll of a helper, each with
the reason. The second case keeps the list honest: each entry matches one line at least. The last cases drive the helpers.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest

from ..support import poll_until, wait_for

TESTS = Path(__file__).resolve().parent.parent
SLEEP_CALL = re.compile(r"time\.sleep\(")

# (path under tests/, text of the line) -> the reason that the sleep is not a wait for a process.
ALLOWED: dict[tuple[str, str], str] = {
    ("support.py", "time.sleep(interval)"): "the poll of `poll_until`: the one short wait of the helpers",
    ("harness_fake.py", 'time.sleep(float(os.environ.get("FAKE_HARNESS_START", "0")))'): "the start delay of the stand-in is its behaviour",
    ("harness_fake.py", "time.sleep(drip)"): "the pace of the stream of the stand-in is its behaviour",
    ("harness_fake.py", 'time.sleep(float(os.environ.get("FAKE_HARNESS_HOLD", "0")))'): "the hold of the stand-in is its behaviour",
    ("test_run_stop.py", "time.sleep(300)"): "a holder that stays alive and ignores SIGTERM: the process under test",
    ("test_run_stop.py", "time.sleep(120)"): "a process that stays alive: it must survive the stop",
    ("test_run_stop.py", "time.sleep(0.5)"): "a signal that must not come: no event shows an absence, so the case waits the time of a signal",
    ("test_state_root.py", "time.sleep(300)"): "a holder that stays alive: the process under test",
    ("test_run_detach_sigint.py", "time.sleep(300)"): "a holder that stays alive: the process under test",
    ("test_run_detach.py", "time.sleep(1.0)"): "a run process that must not start: no event shows an absence",
    ("test_run_lock.py", "time.sleep(0.005)"): "the poll of a child process that waits for the go file or the release file; it has no access to the helpers",
    ("test_default_state_root.py", "time.sleep(0.05)"): "the second run must have a later start time than the first",
    ("runs/test_runs_read_hosts.py", "time.sleep(0.1)"): "a real wait of the frame loop while the background read of the host goes on",
    ("runs/test_runs_read_hosts.py", "time.sleep(0.2)"): "the shim of ssh is in its own wait; no fact shows it",
}


def sleeps_of_tests() -> list[tuple[str, str]]:
    found = []
    for path in sorted(TESTS.rglob("*.py")):
        name = path.relative_to(TESTS).as_posix()
        if name == Path(__file__).relative_to(TESTS).as_posix():
            continue
        for line in path.read_text().splitlines():
            code = line.split("#", 1)[0]
            if SLEEP_CALL.search(code):
                found.append((name, code.strip()))
    return found


def is_allowed(name: str, line: str) -> bool:
    return any(name == path and fragment in line for (path, fragment) in ALLOWED)


def test_no_test_sleeps_for_a_fixed_time_to_wait_for_a_process():
    unexplained = [f"{name}: {line}" for name, line in sleeps_of_tests() if not is_allowed(name, line)]

    assert unexplained == [], "wait for a fact with `wait_for` or `poll_until`, or name the sleep in ALLOWED with its reason"


def test_each_entry_of_the_allow_list_matches_a_sleep_that_exists():
    found = sleeps_of_tests()

    stale = [key for key in ALLOWED if not any(name == key[0] and key[1] in line for name, line in found)]

    assert stale == []


def test_poll_until_returns_true_when_the_fact_comes_after_the_first_check_and_false_at_the_limit():
    flag = threading.Event()
    threading.Timer(0.05, flag.set).start()

    assert poll_until(flag.is_set, 30.0) is True
    assert poll_until(lambda: False, 0.05) is False


def test_wait_for_fails_with_the_name_of_the_fact_when_the_limit_passes():
    started = time.monotonic()

    with pytest.raises(AssertionError, match="the pid file did not happen in 0.05 seconds"):
        wait_for(lambda: False, "the pid file", seconds=0.05)

    assert time.monotonic() - started < 5
