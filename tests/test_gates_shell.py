"""The shell backend of the gate port, tested through the port on real directories.

Each case calls `run` and reads the result. No case imports a private function
of the backend.
"""

import os
import signal
import threading
import time
from pathlib import Path

import delegate
import pytest
from delegate.adapters.shell import ShellGateRunner
from delegate.ports.gates import GateResult, GateRunner

from .support import is_gone


def test_the_shell_backend_is_a_gate_runner():
    assert isinstance(ShellGateRunner(), GateRunner)


def test_a_command_that_succeeds_gives_status_zero_and_is_green(tmp_path):
    result = ShellGateRunner().run("true", tmp_path)

    assert result == GateResult(exit_status=0, output="")
    assert result.green


def test_a_command_that_fails_gives_its_exit_status_and_is_not_green(tmp_path):
    result = ShellGateRunner().run("exit 7", tmp_path)

    assert result.exit_status == 7
    assert not result.green


def test_the_command_runs_in_the_directory_that_the_caller_names(tmp_path):
    (tmp_path / "marker.txt").write_text("here\n")

    assert ShellGateRunner().run("test -f marker.txt", tmp_path).green
    assert not ShellGateRunner().run("test -f marker.txt", tmp_path.parent).green


def test_the_output_holds_standard_output_then_standard_error(tmp_path):
    result = ShellGateRunner().run("echo out; echo err >&2", tmp_path)

    assert result.output == "out\nerr\n"


def test_the_command_goes_to_bash_so_a_pipeline_and_a_list_work(tmp_path):
    result = ShellGateRunner().run("echo one | tr a-z A-Z && echo two", tmp_path)

    assert result.output == "ONE\ntwo\n"
    assert result.green


def test_the_output_goes_to_the_log_file_while_the_command_runs(tmp_path):
    # The command waits until its first line is in the log, so it is green only when the log grows while it runs.
    log = tmp_path / "gate.log"
    log.touch()
    command = (
        f'echo first; for i in $(seq 200); do grep -qx first "{log}" && {{ echo seen; exit 0; }}; sleep 0.05; done; exit 9'
    )

    result = ShellGateRunner().run(command, tmp_path, log=log)

    assert result == GateResult(exit_status=0, output="first\nseen\n")
    assert log.read_text() == "first\nseen\n"


def test_the_log_file_holds_the_standard_error_too_and_the_output_keeps_its_order(tmp_path):
    log = tmp_path / "gate.log"
    log.touch()

    result = ShellGateRunner().run("echo err >&2; echo out", tmp_path, log=log)

    assert result.output == "out\nerr\n"
    assert sorted(log.read_text().splitlines()) == ["err", "out"]


def test_a_command_with_no_log_file_writes_no_file(tmp_path):
    result = ShellGateRunner().run("echo out", tmp_path)

    assert result.output == "out\n"
    assert list(tmp_path.iterdir()) == []


# The directory that holds the `delegate` package that the engine imported.
ENGINE_SOURCE: Path = Path(delegate.__file__).resolve().parent.parent


@pytest.mark.parametrize("entries, expected", [
    ([str(ENGINE_SOURCE), "/other/src"], "/other/src"),
    (["/other/src", str(ENGINE_SOURCE) + os.sep, "/more/src"], f"/other/src{os.pathsep}/more/src"),
    ([str(ENGINE_SOURCE)], "unset"),
])
def test_a_gate_command_does_not_get_the_pythonpath_entry_of_the_engine_package_and_keeps_the_other_entries(tmp_path, monkeypatch, entries, expected):
    # The report of #154: a gate gets the same environment as an agent, so its tests import the code under review.
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(entries))

    result = ShellGateRunner().run('echo "${PYTHONPATH-unset}"', tmp_path)

    assert result == GateResult(exit_status=0, output=f"{expected}\n")


def test_a_gate_command_keeps_the_other_variables_of_the_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("DELEGATE_TEST_MARKER", "kept")

    assert ShellGateRunner().run('echo "$DELEGATE_TEST_MARKER"', tmp_path).output == "kept\n"


def test_an_interrupt_during_a_gate_ends_every_process_of_the_gate_including_a_background_child(tmp_path):
    # The gate starts a child in the background and waits. The interrupt comes as Ctrl-C does (#178).
    pid_file = tmp_path / "child.pid"
    command = f"sleep 300 & echo $! > '{pid_file}'; wait"

    def interrupt_when_the_child_runs():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (pid_file.is_file() and pid_file.read_text().endswith("\n")):
            time.sleep(0.05)
        os.kill(os.getpid(), signal.SIGINT)  # a real signal, which cuts a blocked wait of the main thread

    watcher = threading.Thread(target=interrupt_when_the_child_runs, daemon=True)
    watcher.start()
    with pytest.raises(KeyboardInterrupt):
        ShellGateRunner().run(command, tmp_path)
    watcher.join()

    child = int(pid_file.read_text())
    gone = is_gone(child, 5)
    if not gone:
        os.kill(child, signal.SIGKILL)  # keep the machine clean when the gate left its child alive
    assert gone, "a background child of the gate stayed alive after the interrupt"


def test_a_gate_that_reads_standard_input_gets_end_of_file_at_once(tmp_path):
    # The process of the test has a standard input that never ends, as a terminal has. The gate must not wait for it.
    read_end, write_end = os.pipe()
    saved = os.dup(0)
    os.dup2(read_end, 0)
    try:
        started = time.monotonic()
        result = ShellGateRunner().run('read -t 20 -r line; echo "status=$?"', tmp_path)
        elapsed = time.monotonic() - started
    finally:
        os.dup2(saved, 0)
        for fd in (saved, read_end, write_end):
            os.close(fd)

    assert result.output == "status=1\n"  # 1 is end of file; a read that timed out gives more than 128
    assert elapsed < 10


def test_a_gate_that_runs_past_its_time_limit_ends_with_timed_out_set_and_is_not_green(tmp_path):
    started = time.monotonic()

    result = ShellGateRunner().run("echo before; sleep 30", tmp_path, timeout=1.0)

    assert time.monotonic() - started < 20
    assert result.timed_out
    assert not result.green
    assert result.output == "before\n"  # the output up to the end of the gate is kept


def test_a_gate_that_ends_before_its_time_limit_is_not_timed_out(tmp_path):
    result = ShellGateRunner().run("echo done", tmp_path, timeout=30)

    assert (result.exit_status, result.green, result.timed_out) == (0, True, False)


def test_a_gate_that_exits_zero_when_the_runner_ends_it_is_still_not_green(tmp_path):
    # A trap turns the SIGTERM of the runner into a clean exit. The gate did not finish, so it is red.
    result = ShellGateRunner().run("trap 'exit 0' TERM; sleep 30 & wait", tmp_path, timeout=1.0)

    assert result.timed_out
    assert not result.green


def test_the_time_limit_also_ends_the_children_of_the_gate(tmp_path):
    pid_file = tmp_path / "child.pid"

    result = ShellGateRunner().run(f"sleep 300 & echo $! > '{pid_file}'; wait", tmp_path, timeout=1.0)

    child = int(pid_file.read_text())
    gone = is_gone(child, 5)
    if not gone:
        os.kill(child, signal.SIGKILL)  # keep the machine clean when the gate left its child alive
    assert result.timed_out
    assert gone, "a background child of the gate stayed alive after the time limit"


def test_a_gate_with_no_time_limit_waits_for_the_gate(tmp_path):
    result = ShellGateRunner().run("sleep 1; echo late", tmp_path, timeout=None)

    assert (result.green, result.timed_out, result.output) == (True, False, "late\n")
