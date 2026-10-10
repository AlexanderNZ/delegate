"""`delegate watch` exits on the first problem event, with the exit code `3`, and names the ticket and the reason.

A problem is a step that ends blocked or partial, a step whose gates are not
green, an agent that fails or returns no result, or a second continuation of the
same step. The headline cases build a real run with a scripted adapter and then
watch its journal. The cases for each agent role write the journal events of that
role, because a scripted run reaches them only after many steps.
"""

import json

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import (
    ScriptedAdapter, WORKFLOW, bare_repo, make_repo, read_journal, run_end, run_start, step_end, step_start, valid_report, write_journal,
)

POLL = ["--poll-seconds", "0.02"]
PROBLEM = 3


def build_then_watch(tmp_path, capsys, adapter, workflow=WORKFLOW):
    """Run `workflow.toml` with the adapter, remove the adapter, and watch the journal of the run."""
    adapters.register("scripted", adapter)
    try:
        repo = make_repo(tmp_path, workflow)
        delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
        events = read_journal(capsys.readouterr().out)
    finally:
        adapters.unregister("scripted")
    code = delegate.main(["watch", events[0]["run_id"], "--repo", str(repo), *POLL])
    captured = capsys.readouterr()
    return code, captured.out, captured.err, events


def problem_line(out: str) -> str:
    (line,) = [line for line in out.splitlines() if line.startswith("watch: problem")]
    return line


@pytest.mark.parametrize("status", ["blocked", "partial"])
def test_a_step_that_ends_blocked_or_partial_is_a_problem_that_names_the_ticket_and_the_reason(tmp_path, capsys, git_identity, status):
    def report(request, head):
        return json.dumps(valid_report("a", "run/demo-a", head, status=status, blocked_reason="the export format is not decided"))

    code, out, err, _ = build_then_watch(tmp_path, capsys, ScriptedAdapter(report_text=report))

    assert (code, err) == (PROBLEM, "")
    assert problem_line(out) == f"watch: problem: ticket a: the step ended {status}: the export format is not decided"
    assert "step-end" not in out  # the problem ends the watch before the step ends


def test_a_step_whose_gates_are_not_green_is_a_problem_that_names_the_ticket_and_the_gate(tmp_path, capsys, git_identity):
    # The gate needs feature.txt. The specialist writes another file, so the gate is red after the first build and after the one continuation of economy mode.
    workflow = WORKFLOW.replace('mode = "assure"', 'mode = "economy"')

    code, out, err, _ = build_then_watch(tmp_path, capsys, ScriptedAdapter(files={"other.txt": "x\n"}), workflow)

    assert (code, err) == (PROBLEM, "")
    assert problem_line(out) == "watch: problem: ticket a: the gates are not green: test -f feature.txt"
    continuations = [line for line in out.splitlines() if line.split()[2:3] == ["continuation"]]
    assert len(continuations) == 1  # the first continuation is not a problem


def test_an_agent_that_ends_failed_is_a_problem_that_names_the_ticket_and_the_end_state(tmp_path, capsys, git_identity):
    code, out, err, _ = build_then_watch(tmp_path, capsys, ScriptedAdapter(failing_tickets={"a"}))

    assert (code, err) == (PROBLEM, "")
    assert problem_line(out) == "watch: problem: ticket a: the specialist ended failed with exit status 1"


def test_an_agent_that_returns_no_result_is_a_problem_that_names_the_ticket_and_the_report(tmp_path, capsys, git_identity):
    code, out, err, events = build_then_watch(tmp_path, capsys, ScriptedAdapter(write_report=False))

    assert (code, err) == (PROBLEM, "")
    assert problem_line(out).startswith("watch: problem: ticket a: the specialist returned no valid result: ")
    assert "report" in problem_line(out)


def test_the_second_continuation_of_a_step_is_a_problem_and_the_first_is_not(tmp_path, capsys, git_identity):
    # The specialist is capped twice, then finishes. Capped is not a failure; the second continuation is the problem.
    adapter = ScriptedAdapter(outcomes=[("capped", 0), ("capped", 0), ("finished", 0)])

    code, out, err, _ = build_then_watch(tmp_path, capsys, adapter)

    assert (code, err) == (PROBLEM, "")
    assert problem_line(out) == "watch: problem: ticket a: the step continued 2 times (limit 2, trigger capped)"


def test_one_continuation_after_a_capped_specialist_is_not_a_problem(tmp_path, capsys, git_identity):
    adapter = ScriptedAdapter(outcomes=[("capped", 0), ("finished", 0)])

    code, out, err, _ = build_then_watch(tmp_path, capsys, adapter)

    assert (code, err) == (0, "")
    assert "watch: problem" not in out


def test_a_verifier_that_returns_no_result_is_a_problem_that_names_the_ticket(tmp_path, capsys, git_identity):
    code, out, err, _ = build_then_watch(tmp_path, capsys, ScriptedAdapter(write_verifier_report=False))

    assert (code, err) == (PROBLEM, "")
    assert problem_line(out).startswith("watch: problem: ticket a: the verifier returned no valid result: ")


@pytest.mark.parametrize(
    ("event", "fields", "reason"),
    [
        pytest.param(
            "verify-result", {"round": 0, "exit_status": 1, "end_state": "failed", "session_id": None, "event_stream": "/s"},
            "the verifier ended failed with exit status 1", id="verifier-failed",
        ),
        pytest.param(
            "verify-result", {"round": 0, "exit_status": 0, "end_state": "capped", "session_id": None, "event_stream": "/s"},
            "the verifier ended capped with exit status 0", id="verifier-capped",
        ),
        pytest.param(
            "fixup-result", {"round": 1, "exit_status": 0, "end_state": "failed", "session_id": None, "event_stream": "/s"},
            "the fix-up specialist ended failed with exit status 0", id="fixup-failed",
        ),
        pytest.param(
            "adapter-result", {"exit_status": 3, "end_state": "finished", "session_id": None, "event_stream": "/s"},
            "the specialist ended finished with exit status 3", id="nonzero-exit",
        ),
        pytest.param(
            "fixup-report", {"round": 1, "valid": False, "path": "/r.json", "reason": "report /r.json not found"},
            "the fix-up specialist returned no valid result: report /r.json not found", id="fixup-no-report",
        ),
        pytest.param(
            "fixup-report", {"round": 1, "valid": True, "path": "/r.json", "reason": None, "status": "partial"},
            "the fix-up specialist ended partial", id="fixup-partial",
        ),
    ],
)
def test_each_agent_role_that_fails_or_returns_no_result_is_a_problem(tmp_path, capsys, event, fields, reason):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), (event, {"ticket": "a", **fields}), step_end("a", "failed", "x"), run_end("failed", [], ["a"]))

    code = delegate.main(["watch", "r1", "--repo", str(repo), *POLL])
    out = capsys.readouterr().out

    assert code == PROBLEM
    assert problem_line(out) == f"watch: problem: ticket a: {reason}"


def test_a_step_that_failed_for_any_other_reason_is_a_problem_that_gives_the_reason(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(
        repo, "r1", run_start("r1", "a"), step_start("a"),
        step_end("a", "failed", "rebase onto run/demo stopped with a conflict in x.txt"), run_end("failed", [], ["a"]),
    )

    code = delegate.main(["watch", "r1", "--repo", str(repo), *POLL])
    out = capsys.readouterr().out

    assert code == PROBLEM
    assert problem_line(out) == "watch: problem: ticket a: the step failed: rebase onto run/demo stopped with a conflict in x.txt"
