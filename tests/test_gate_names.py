"""The short name of a gate, and a gate that a workflow names (#223).

The feed of `delegate watch`, the status and the reasons show each gate by a short name. A gate in a frontend stack starts
with `cd <dir> &&`: its short name is the tool, not `cd`. A stack can also give a gate as a table with a `name`, and the
name goes into each place that shows the short name.
"""

from __future__ import annotations

import pytest

from delegate import adapters
from delegate.adapters.git import GitVersionControl
from delegate.adapters.shell import ShellGateRunner
from delegate.cli import delegate
from delegate.ports.gates import GateResult
from delegate.run.engine import run_workflow
from delegate.run.workflow import WorkflowError, load_workflow
from delegate.shared.tiers import load_tiers

from .support import WORKFLOW, ScriptedAdapter, bare_repo, frames, make_repo, read_journal_file, write_project_agents
from .test_run_gate_port import SpyGates
from .test_watch_live import PYTEST, Run, local_zone, watch  # noqa: F401 - local_zone is a fixture, used by name

# The commands of each case use the fake clock of the case (#297): the helpers of `test_watch_live` set it.
pytestmark = pytest.mark.usefixtures("home", "claude_stand_in", "clock")

VITEST = "cd frontend && npx vitest run --project unit"
PLAYWRIGHT = "cd e2e; npx playwright test"  # a `;` chain, as `&&` is in VITEST


def feed_of_gates(tmp_path, monkeypatch, *gates: tuple[str, dict[str, object]]) -> list[str]:
    """The feed lines of a run of one ticket whose gates are `(command, extra fields)`, one after the other."""
    repo = bare_repo(tmp_path)
    run = Run(repo, "a")
    run.step_start(0, "a")
    for number, (command, extra) in enumerate(gates):
        start = 60 + number * 120
        log = run.dir / "gates" / "a" / f"build-{number}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("")
        run.add(start, "gate-start", ticket="a", round=0, phase="build", command=command, gate_log=str(log), **extra)
        run.add(start + 60, "gate-result", ticket="a", round=0, phase="build", command=command, commit="1" * 40, exit_status=0,
                green=True, output_tail="ok", **extra)
    code, out = watch(repo, monkeypatch, then=lambda: run.add(900, "run-end", result="failed", built=[], failed=["a"], skipped=[]))
    assert code == 1
    first = frames(out)[0]
    return first[first.index("─" * 200) + 1:]


def test_a_gate_that_changes_directory_first_has_the_short_name_of_the_tool_in_the_feed(tmp_path, monkeypatch, local_zone):
    local_zone("NZT-13")

    lines = feed_of_gates(tmp_path, monkeypatch, (VITEST, {}), (PLAYWRIGHT, {}), (PYTEST, {}))

    assert [line for line in lines if "gates" in line] == ["16:06  #a   gates green (vitest 1m, playwright 1m, pytest 1m)"]


def test_a_gate_with_a_name_shows_the_name_in_the_feed(tmp_path, monkeypatch, local_zone):
    local_zone("NZT-13")

    lines = feed_of_gates(tmp_path, monkeypatch, (VITEST, {"name": "unit"}), (PLAYWRIGHT, {"name": "e2e"}))

    (gates,) = [line for line in lines if "gates" in line]
    assert gates.endswith("gates green (unit 1m, e2e 1m)")


def two_stack_workflow(gates: str) -> str:
    """The workflow of one stack with the `gates` given as TOML. The dry run needs an adapter that is registered by default."""
    return WORKFLOW.replace('gates = ["test -f feature.txt"]', f"gates = {gates}")


def dry_run_workflow(gates: str) -> str:
    return two_stack_workflow(gates).replace('adapter = "scripted"', 'adapter = "claude-code"')


@pytest.fixture
def scripted_adapter(git_identity):
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def start_run(repo, gates, adapter):
    tiers = load_tiers()
    path = repo / "workflow.toml"
    workflow = load_workflow(path, tiers, adapters.registered_names())
    return run_workflow(workflow, path, repo, tiers, adapter, GitVersionControl(), gates)


def test_a_red_gate_with_a_name_gives_the_name_in_the_reason_and_the_journal_and_the_command_still_runs(tmp_path, scripted_adapter):
    repo = make_repo(tmp_path, two_stack_workflow('[{ name = "unit", command = "test -f feature.txt" }, "echo two"]'))
    gates = SpyGates(GateResult(exit_status=3, output="boom\n"))

    result = start_run(repo, gates, scripted_adapter)

    assert result.failed == ["a"]
    assert "gates red: unit; echo two" in result.failures["a"]
    assert "test -f feature.txt" not in result.failures["a"]
    assert gates.calls[0][0] == "test -f feature.txt"
    events = read_journal_file(repo, result.run_id)
    start, second_start = [e for e in events if e["event"] == "gate-start"][:2]
    done, second_done = [e for e in events if e["event"] == "gate-result"][:2]
    assert (start["name"], start["command"]) == ("unit", "test -f feature.txt")
    assert (done["name"], done["command"]) == ("unit", "test -f feature.txt")
    assert "name" not in second_start and "name" not in second_done  # a gate that is a string has no name


def test_a_workflow_of_strings_and_tables_runs_each_command_and_names_only_the_tables(tmp_path, scripted_adapter):
    repo = make_repo(tmp_path, two_stack_workflow('[{ name = "files", command = "echo one" }, "echo two"]'))

    result = start_run(repo, ShellGateRunner(), scripted_adapter)

    assert result.ok, result.failures
    starts = [e for e in read_journal_file(repo, result.run_id) if e["event"] == "gate-start"]
    assert [(e["command"], e.get("name")) for e in starts] == [("echo one", "files"), ("echo two", None)]


@pytest.mark.parametrize(
    "gates",
    [
        '[{ command = "echo one" }]',
        '[{ name = "x" }]',
        '[{ name = "", command = "echo one" }]',
        '[{ name = "x", command = "echo one", extra = 1 }]',
        '[{ name = 3, command = "echo one" }]',
    ],
)
def test_a_gate_table_that_lacks_a_name_or_a_command_is_refused(tmp_path, capsys, gates):
    repo = make_repo(tmp_path, dry_run_workflow(gates))

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run"])

    assert code == 1
    assert "stacks.python.gates" in capsys.readouterr().err


def dry_run(repo, capsys) -> tuple[int, str, str]:
    write_project_agents(repo)
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run"])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def gate_names_line(out: str) -> str:
    """The line of the check `gate-names` in the section `quality check` of the dry run (#247)."""
    (line,) = [line for line in out.splitlines() if line.startswith(("ok gate-names:", "warn gate-names:"))]
    return line


def test_the_dry_run_warns_about_two_gates_with_the_same_short_name_and_exits_0(tmp_path, capsys):
    repo = make_repo(tmp_path, dry_run_workflow('["cd a && npx vitest run", "cd b && npx vitest run --project unit"]'))

    code, out, err = dry_run(repo, capsys)

    assert (code, err) == (0, "")
    assert gate_names_line(out) == (
        "warn gate-names: stack python: the gates `cd a && npx vitest run`, `cd b && npx vitest run --project unit` have the "
        """same short name 'vitest'; give each one a name: { name = "<name>", command = "<command>" }; """
        "without it, the views cannot tell the gates apart"
    )


def test_the_dry_run_does_not_warn_when_each_gate_has_its_own_short_name_or_a_name(tmp_path, capsys):
    repo = make_repo(tmp_path, dry_run_workflow('["cd a && npx vitest run", { name = "unit", command = "cd b && npx vitest run" }]'))

    code, out, err = dry_run(repo, capsys)

    assert (code, err) == (0, "")
    assert gate_names_line(out) == "ok gate-names: stack python: each gate has its own short name: vitest, unit"


def test_the_dry_run_warns_when_two_names_are_the_same(tmp_path, capsys):
    repo = make_repo(tmp_path, dry_run_workflow('[{ name = "t", command = "echo a" }, { name = "t", command = "echo b" }]'))

    code, out, _ = dry_run(repo, capsys)

    assert code == 0
    assert gate_names_line(out).startswith("warn gate-names: stack python: the gates `echo a`, `echo b` have the same short name 't';")


def test_a_workflow_with_strings_only_is_valid_and_has_no_warning(tmp_path, capsys):
    repo = make_repo(tmp_path, WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"'))

    code, out, err = dry_run(repo, capsys)

    assert (code, err) == (0, "")
    assert gate_names_line(out) == "ok gate-names: stack python: each gate has its own short name: test"


def running_gate(tmp_path, command: str, **extra: object):
    """The repository and the journal of a run whose gate `command` started and has not ended."""
    repo = bare_repo(tmp_path)
    run = Run(repo, "a")
    run.step_start(0, "a")
    log = run.dir / "gates" / "a" / "build-0.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("")
    run.add(60, "gate-start", ticket="a", round=0, phase="build", command=command, gate_log=str(log), **extra)
    return repo, run


@pytest.mark.parametrize(("extra", "shown"), [({}, "gate vitest"), ({"name": "unit"}, "gate unit")])
def test_the_live_view_of_a_running_gate_shows_its_short_name_or_its_name(tmp_path, monkeypatch, extra, shown):
    repo, run = running_gate(tmp_path, VITEST, **extra)

    code, out = watch(repo, monkeypatch, then=lambda: run.add(900, "run-end", result="failed", built=[], failed=["a"], skipped=[]))

    assert code == 1
    assert any(line.rstrip().endswith(shown) or f"{shown} " in line for line in frames(out)[0])


def test_a_pause_before_a_named_gate_gives_the_name_in_the_feed(tmp_path, monkeypatch, local_zone):
    local_zone("NZT-13")
    repo = bare_repo(tmp_path)
    run = Run(repo, "a")
    run.step_start(0, "a")
    run.add(60, "paused", ticket="a", before="gate", command=VITEST, name="unit", phase="build", reason="r")
    _, out = watch(repo, monkeypatch, then=lambda: None)

    assert "before the gate unit of ticket a" in out
