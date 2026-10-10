"""The shell backend of the gate port, tested through the port on real directories.

Each case calls `run` and reads the result. No case imports a private function
of the backend.
"""

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
