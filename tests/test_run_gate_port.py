"""The engine runs a gate command through the gate port.

The engine takes the gate runner from its caller, as it takes the harness
adapter and the version-control backend. Each case hands `run_workflow` a gate
runner, on a real temporary repository, and checks what the engine asked of it
and what came of it. The gate runner stands at a system boundary: it starts a
process.
"""

from pathlib import Path

import pytest

from delegate import adapters
from delegate.adapters.git import GitVersionControl
from delegate.adapters.shell import ShellGateRunner
from delegate.ports.gates import GateResult
from delegate.run.engine import run_workflow
from delegate.run.workflow import load_workflow
from delegate.shared.tiers import load_tiers

from .support import WORKFLOW, ScriptedAdapter, make_repo, read_journal_file, journal_path


class SpyGates:
    """A gate runner that records each call, and answers with `answer` or with the real shell."""

    def __init__(self, answer: GateResult | None = None) -> None:
        self._answer = answer
        self.calls: list[tuple[str, str]] = []

    def run(self, command, worktree, log=None):
        self.calls.append((command, worktree.name))
        return self._answer if self._answer is not None else ShellGateRunner().run(command, worktree, log=log)


@pytest.fixture
def scripted_adapter(git_identity):
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def start_run(repo, gates, adapter):
    tiers = load_tiers()
    workflow_path = repo / "workflow.toml"
    workflow = load_workflow(workflow_path, tiers, adapters.registered_names())
    return run_workflow(workflow, workflow_path, repo, tiers, adapter, GitVersionControl(), gates)


def test_every_gate_command_reaches_the_runner_with_the_worktree_of_its_ticket(tmp_path, scripted_adapter):
    repo = make_repo(tmp_path)
    gates = SpyGates()

    result = start_run(repo, gates, scripted_adapter)

    assert result.ok
    assert gates.calls
    assert set(gates.calls) == {("test -f feature.txt", "a")}
    results = [e for e in read_journal_file(repo, result.run_id) if e["event"] == "gate-result"]
    assert [(e["phase"], e["green"]) for e in results] == [("build", True)]  # the rebase keeps the commit


def test_a_gate_that_the_runner_reports_red_fails_the_ticket_and_the_journal_keeps_its_output(tmp_path, scripted_adapter):
    repo = make_repo(tmp_path)
    gates = SpyGates(GateResult(exit_status=3, output="boom: three tests failed\n"))

    result = start_run(repo, gates, scripted_adapter)

    assert result.failed == ["a"]
    assert "gates red: test -f feature.txt" in result.failures["a"]
    first = next(e for e in read_journal_file(repo, result.run_id) if e["event"] == "gate-result")
    assert (first["exit_status"], first["green"], first["output_tail"]) == (3, False, "boom: three tests failed\n")


def test_a_gate_that_the_runner_reports_green_passes_whatever_the_command_says(tmp_path, scripted_adapter):
    repo = make_repo(tmp_path)
    gates = SpyGates(GateResult(exit_status=0, output="fine\n"))

    result = start_run(repo, gates, scripted_adapter)

    assert result.ok
    assert {command for command, _ in gates.calls} == {"test -f feature.txt"}


# The gate waits until the journal holds `gate-start` and its first line is in a gate log of ticket a. So it is green
# only when the engine journals the gate before it runs, and the log grows while the gate runs.
WATCHED_GATE = (
    'echo first; d="$(git rev-parse --git-common-dir)/delegate/runs"; for i in $(seq 100); do '
    'grep -q gate-start "$d"/*/journal.jsonl && grep -qx first "$d"/*/gates/a/*.log && { echo seen; exit 0; }; '
    "sleep 0.05; done; exit 9"
)


def test_while_a_gate_runs_the_journal_holds_gate_start_with_its_log_and_the_log_grows(tmp_path, scripted_adapter):
    repo = make_repo(tmp_path, WORKFLOW.replace('"test -f feature.txt"', f"'{WATCHED_GATE}'"))

    result = start_run(repo, ShellGateRunner(), scripted_adapter)

    assert result.ok, result.failures
    events = read_journal_file(repo, result.run_id)
    (start,) = [e for e in events if e["event"] == "gate-start"]
    (gate,) = [e for e in events if e["event"] == "gate-result"]
    assert events[events.index(start) + 1] is gate
    assert (start["ticket"], start["round"], start["phase"], start["command"]) == ("a", 0, "build", WATCHED_GATE)
    log = Path(start["gate_log"])
    assert log.parent == journal_path(repo, result.run_id).parent / "gates" / "a"
    assert log.read_text() == gate["output_tail"] == "first\nseen\n"


def test_gate_result_keeps_its_fields(tmp_path, scripted_adapter):
    repo = make_repo(tmp_path)

    result = start_run(repo, ShellGateRunner(), scripted_adapter)

    (gate,) = [e for e in read_journal_file(repo, result.run_id) if e["event"] == "gate-result"]
    assert set(gate) == {"seq", "time", "event", "ticket", "round", "phase", "command", "commit", "exit_status", "green", "output_tail"}


def test_each_gate_run_has_its_own_log_with_its_own_output(tmp_path, scripted_adapter):
    repo = make_repo(tmp_path, WORKFLOW.replace('"test -f feature.txt"', '"echo one", "echo two"'))

    result = start_run(repo, ShellGateRunner(), scripted_adapter)

    assert result.ok
    starts = [e for e in read_journal_file(repo, result.run_id) if e["event"] == "gate-start"]
    assert [e["command"] for e in starts] == ["echo one", "echo two"]
    assert [Path(e["gate_log"]).read_text() for e in starts] == ["one\n", "two\n"]
