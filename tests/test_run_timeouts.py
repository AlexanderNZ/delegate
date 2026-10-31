"""A stack can set a time limit for a gate and for an agent session (#179).

Each case writes a workflow into a real temporary git repository and calls `delegate.main`. A gate is a real shell
command. An agent is the scripted adapter, or the `claude` stand-in of `claude_fake` that hangs, as a session that
never writes an event. With no limit, the engine waits as before.
"""

import time

import pytest

from delegate import adapters
from delegate.run.domain import IDLE_TIMEOUT_SECONDS

from . import claude_fake
from .support import WORKFLOW, ScriptedAdapter, event_names, git, make_repo, read_journal, run_main, write_project_agents

pytestmark = pytest.mark.usefixtures("home")  # the dry run looks in the user directory for agent files (#182)

GATE = 'gates = ["test -f feature.txt"]'


def with_limits(gate: str | None = None, agent: str | None = None, gates: str = GATE, text: str = WORKFLOW) -> str:
    """The test workflow with the stack fields `gate-timeout` and `agent-timeout` after the gates."""
    lines = [gates] + ([f"gate-timeout = {gate}"] if gate is not None else []) + ([f"agent-timeout = {agent}"] if agent is not None else [])
    return text.replace(GATE, "\n".join(lines))


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys, *extra):
    return run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), *extra)


def dry_run(tmp_path, capsys, text):
    """`delegate run --dry-run` with a built-in adapter, which the check knows without a registration."""
    repo = make_repo(tmp_path, text.replace('adapter = "scripted"', 'adapter = "claude-code"'))
    write_project_agents(repo)
    return run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run")


@pytest.mark.parametrize("field", ["gate-timeout", "agent-timeout"])
@pytest.mark.parametrize(
    ("value", "message"),
    [
        pytest.param("0", "must be greater than 0", id="zero"),
        pytest.param("-5", "must be greater than 0", id="negative"),
        pytest.param('"ten"', "must be a number of minutes", id="text"),
        pytest.param("true", "must be a number of minutes", id="boolean"),
        pytest.param("nan", "must be a number of minutes", id="not-a-number"),
        pytest.param("inf", "must be a number of minutes", id="infinite"),
    ],
)
def test_the_workflow_check_refuses_a_time_limit_that_is_not_a_positive_number_and_names_the_field(tmp_path, capsys, field, value, message):
    text = WORKFLOW.replace(GATE, f"{GATE}\n{field} = {value}")

    code, out, err = dry_run(tmp_path, capsys, text)

    assert code == 1
    assert f"stacks.python.{field}: {message}" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("value", ["10", "0.05", "1.5"])
def test_the_workflow_check_accepts_a_whole_number_and_a_fraction_of_minutes(tmp_path, capsys, value):
    text = WORKFLOW.replace(GATE, f"{GATE}\ngate-timeout = {value}\nagent-timeout = {value}")

    code, out, err = dry_run(tmp_path, capsys, text)

    assert (code, err) == (0, "")


def test_a_gate_that_runs_past_gate_timeout_is_red_with_timed_out_and_the_specialist_continues_with_gates_red(
    tmp_path, capsys, scripted
):
    # The first run of the gate hangs; the continuation adds the file that makes the gate pass.
    scripted.continuation_files = [{"second.txt": "second\n"}]
    repo = make_repo(tmp_path, with_limits(gate="0.05", gates='gates = ["test -f second.txt || sleep 30"]'))
    started = time.monotonic()

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert time.monotonic() - started < 25  # the limit is 3 seconds; a gate with no limit would wait 30 seconds
    events = read_journal(out)
    results = [e for e in events if e["event"] == "gate-result" and e["phase"] == "build"]
    assert [(e["green"], e["timed_out"]) for e in results] == [(False, True), (True, False)]
    (continuation,) = [e for e in events if e["event"] == "continuation"]
    assert continuation["trigger"] == "gates-red"
    # The specialist learns from the brief that the gate ran past its limit, not only that it was red.
    (brief,) = [call.prompt for call in scripted.continuation_calls]
    assert "gate-timeout" in brief
    assert next(e for e in events if e["event"] == "step-end")["state"] == "built"


def test_a_gate_that_stays_past_its_limit_fails_the_step_and_the_reason_names_the_time_limit(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, with_limits(gate="0.02", gates='gates = ["sleep 30"]'))

    code, out, err = run(repo, capsys)

    assert code == 1
    events = read_journal(out)
    assert [e["timed_out"] for e in events if e["event"] == "gate-result"] == [True, True, True]  # the build, and two continuations
    step_end = next(e for e in events if e["event"] == "step-end")
    assert step_end["state"] == "failed"
    assert "sleep 30" in step_end["reason"] and "timed out" in step_end["reason"]


def test_a_workflow_with_no_limit_runs_as_before(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    assert next(e for e in events if e["event"] == "step-end")["state"] == "built"
    assert [(e["green"], e["timed_out"]) for e in events if e["event"] == "gate-result"] == [(True, False)]
    assert "agent-timeout" not in event_names(events)
    assert [call.agent_timeout for call in [*scripted.calls, *scripted.verifier_calls]] == [None, None]


def test_each_agent_gets_the_agent_timeout_of_its_stack_in_seconds(tmp_path, capsys, scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = ["FINDING-1 the header row is missing from feature.txt"]
    repo = make_repo(tmp_path, with_limits(agent="0.5"))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert scripted.report_checks == [
        ("specialist", IDLE_TIMEOUT_SECONDS, False, True),
        ("verifier", IDLE_TIMEOUT_SECONDS, False, True),
        ("fix-up", IDLE_TIMEOUT_SECONDS, False, True),
        ("verifier", IDLE_TIMEOUT_SECONDS, False, True),
    ]
    requests = [*scripted.calls, *scripted.fixup_calls, *scripted.verifier_calls]
    assert [request.agent_timeout for request in requests] == [30.0] * 4


def test_a_specialist_session_that_hangs_ends_at_agent_timeout_and_the_step_goes_on_by_the_continuation_rules(
    tmp_path, monkeypatch, capsys
):
    # The `claude` stand-in sleeps for 300 seconds before it writes an event. The limit is 3 seconds.
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl", start_seconds=300)
    repo = make_repo(tmp_path, with_limits(agent="0.05", text=WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"')))
    write_project_agents(repo)
    started = time.monotonic()

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    assert time.monotonic() - started < 120
    events = read_journal(out)
    timeouts = [e for e in events if e["event"] == "agent-timeout"]
    assert [(e["ticket"], e["role"], e["round"], e["seconds"]) for e in timeouts] == [("a", "specialist", 0, 3.0)] * 3
    # The existing rules apply: the specialist continues twice in assure mode, and then the step fails.
    assert [(e["count"], e["trigger"]) for e in events if e["event"] == "continuation"] == [(1, "failed"), (2, "failed")]
    names = event_names(events)
    assert names.index("agent-timeout") < names.index("adapter-result")
    step_end = next(e for e in events if e["event"] == "step-end")
    assert step_end["state"] == "failed"
    assert "agent-timeout" in step_end["reason"]


def test_a_verifier_session_that_runs_past_agent_timeout_fails_the_step_with_the_time_limit_in_the_reason(tmp_path, capsys, scripted):
    scripted.timeout_roles = {"verifier"}
    repo = make_repo(tmp_path, with_limits(agent="10"))

    code, out, err = run(repo, capsys)

    assert code == 1
    events = read_journal(out)
    (timeout,) = [e for e in events if e["event"] == "agent-timeout"]
    assert (timeout["role"], timeout["seconds"]) == ("verifier", 600.0)
    names = event_names(events)
    assert names.index("agent-timeout") + 1 == names.index("verify-result")
    step_end = next(e for e in events if e["event"] == "step-end")
    assert step_end["state"] == "failed" and "agent-timeout of 10 minutes" in step_end["reason"]
    assert "verdict" not in names


def test_a_fixup_session_that_runs_past_agent_timeout_fails_the_step(tmp_path, capsys, scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = ["FINDING-2 the header row is missing from feature.txt"]
    scripted.timeout_roles = {"fix-up"}
    repo = make_repo(tmp_path, with_limits(agent="10"))

    code, out, err = run(repo, capsys)

    assert code == 1
    events = read_journal(out)
    (timeout,) = [e for e in events if e["event"] == "agent-timeout"]
    assert (timeout["role"], timeout["round"]) == ("fix-up", 1)
    step_end = next(e for e in events if e["event"] == "step-end")
    assert step_end["state"] == "failed" and "agent-timeout of 10 minutes" in step_end["reason"]
