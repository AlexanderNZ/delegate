"""The shell backend of the gate port, tested through the port on real directories.

Each case calls `run` and reads the result. No case imports a private function
of the backend.
"""

import os
from pathlib import Path

import delegate
import pytest
from delegate.adapters.shell import ShellGateRunner
from delegate.ports.gates import GateResult, GateRunner


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
