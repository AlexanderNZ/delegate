"""How the engine treats the end of an agent session: the exit status and the idle timeout.

A coordinator stopped an idle session with SIGTERM. The last `result` event of
the session was a success and the report was valid, but the exit status 143
failed the step. These cases fix the two rules: a valid report and a success
result continue the step, whatever the exit status; and after a valid report,
the engine ends a session that writes no event for the idle timeout, and
continues.

Each case writes a workflow into a real temporary git repository, registers a
scripted adapter by name, and calls `delegate.main`.
"""

import pytest

from delegate import adapters
from delegate.cli import delegate
from delegate.run.domain import IDLE_TIMEOUT_SECONDS

from .support import ScriptedAdapter, event_names, git, make_repo, read_journal

FINDING = "FINDING-5120 the header row is missing from feature.txt"

# The SIGTERM status that the shell gives a stopped process: 128 + 15.
SIGTERM_STATUS = 143


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def assert_built(repo, out):
    events = read_journal(out)
    step_end = next(e for e in events if e["event"] == "step-end")
    assert (step_end["state"], step_end["reason"]) == ("built", None)
    assert git(repo, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "run/demo-a").strip()
    return events


def test_a_valid_report_a_success_result_and_exit_status_143_continue_the_fixup_step(tmp_path, capsys, scripted):
    # The reported case: the fix-up session was stopped with SIGTERM after its valid report.
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = [FINDING]
    scripted.fixup_end_state, scripted.fixup_exit_status = "finished", SIGTERM_STATUS
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = assert_built(repo, out)
    result = next(e for e in events if e["event"] == "fixup-result")
    assert (result["end_state"], result["exit_status"]) == ("finished", SIGTERM_STATUS)
    report = next(e for e in events if e["event"] == "fixup-report")
    assert (report["valid"], report["status"]) == (True, "committed")
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["REJECT", "ACCEPT"]


def test_a_valid_report_a_success_result_and_exit_status_143_continue_the_specialist_step(tmp_path, capsys, scripted):
    scripted.end_state, scripted.exit_status = "finished", SIGTERM_STATUS
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = assert_built(repo, out)
    assert "continuation" not in event_names(events)
    assert len(scripted.calls) == 1
    report = next(e for e in events if e["event"] == "report-validation")
    assert report["valid"] is True
    assert [e["green"] for e in events if e["event"] == "gate-result"] == [True, True]  # the build and the rebase


def test_a_valid_verdict_a_success_result_and_exit_status_143_continue_the_verifier_step(tmp_path, capsys, scripted):
    scripted.verifier_end_state, scripted.verifier_exit_status = "finished", SIGTERM_STATUS
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = assert_built(repo, out)
    assert [(e["mode"], e["verdict"]) for e in events if e["event"] == "verdict"] == [("full", "ACCEPT")]


@pytest.mark.parametrize("role", ["specialist", "fix-up", "verifier"])
def test_after_a_valid_report_an_idle_session_ends_at_the_timeout_and_the_step_continues(tmp_path, capsys, scripted, role):
    if role == "fix-up":
        scripted.verdict_sequence = ["REJECT", "ACCEPT"]
        scripted.verdict_findings = [FINDING]
    scripted.idle_roles = {role}
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = assert_built(repo, out)
    names = event_names(events)
    (idle,) = [e for e in events if e["event"] == "idle-timeout"]
    expected_round = 1 if role == "fix-up" else 0
    assert {k: idle[k] for k in ("ticket", "role", "round", "seconds")} == {
        "ticket": "a", "role": role, "round": expected_round, "seconds": IDLE_TIMEOUT_SECONDS,
    }
    # The journal records the timeout just before the result of the session that it ended.
    result_event = {"specialist": "adapter-result", "fix-up": "fixup-result", "verifier": "verify-result"}[role]
    ended = names.index(result_event)
    assert names[ended - 1] == "idle-timeout"
    assert (events[ended]["end_state"], events[ended]["exit_status"]) == ("failed", -15)
    assert "continuation" not in names


def test_each_agent_gets_the_idle_timeout_and_a_report_check_that_is_false_before_the_report_and_true_after(tmp_path, capsys, scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = [FINDING]
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert scripted.report_checks == [
        ("specialist", IDLE_TIMEOUT_SECONDS, False, True),
        ("verifier", IDLE_TIMEOUT_SECONDS, False, True),
        ("fix-up", IDLE_TIMEOUT_SECONDS, False, True),
        ("verifier", IDLE_TIMEOUT_SECONDS, False, True),
    ]
    assert "idle-timeout" not in event_names(read_journal(out))


def test_the_report_check_of_an_agent_is_false_for_a_report_that_is_not_valid(tmp_path, capsys, scripted):
    # An invalid report never lets the adapter end a session at the idle timeout.
    scripted.report_text = '{"ticket": "a"}'
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert scripted.report_checks == [("specialist", IDLE_TIMEOUT_SECONDS, False, False)]
