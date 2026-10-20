"""`delegate watch` exits on the first problem event, with the exit code `3`, and names the ticket and the reason.

In the replay, a problem that a later event of the run goes past is history (#146), also when the run has ended (#151):
a `step-end` goes past each problem of its step. So a run that failed stops at the `step-end` of the step that failed,
and its reason gives the cause. A case that checks the problem of one event writes a journal with no later event that
goes past it.

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
    reason = f"the specialist reported status '{status}': the export format is not decided"
    assert problem_line(out) == f"watch: problem: ticket a: the step failed: {reason}"
    # The step-end goes past the report of the specialist, so the problem is at the step end (#151).
    assert out.splitlines()[-3].split()[2] == "step-end"


def test_a_step_whose_gates_are_not_green_is_a_problem_that_names_the_ticket_and_the_gate(tmp_path, capsys, git_identity):
    # The gate needs feature.txt. The specialist and the fix-up specialist write other files, so in economy mode the gate
    # on the tip of the chain is red, and red again after each of the two fix-up rounds.
    workflow = WORKFLOW.replace('mode = "assure"', 'mode = "economy"')

    code, out, err, _ = build_then_watch(tmp_path, capsys, ScriptedAdapter(files={"other.txt": "x\n"}), workflow)

    assert (code, err) == (PROBLEM, "")
    assert problem_line(out) == "watch: problem: ticket a: the gates are not green: test -f feature.txt"
    fixups = [line for line in out.splitlines() if line.split()[2:3] == ["fixup-start"]]
    assert len(fixups) == 2  # a fix-up round of red gates is not a problem


def test_an_agent_that_ends_failed_is_a_problem_that_names_the_ticket_and_the_end_state(tmp_path, capsys, git_identity):
    code, out, err, _ = build_then_watch(tmp_path, capsys, ScriptedAdapter(failing_tickets={"a"}))

    assert (code, err) == (PROBLEM, "")
    assert problem_line(out) == (
        "watch: problem: ticket a: the step failed: the specialist ended failed with exit status 1; the continuation limit of 2 is reached"
    )


def test_an_agent_that_returns_no_result_is_a_problem_that_names_the_ticket_and_the_report(tmp_path, capsys, git_identity):
    code, out, err, events = build_then_watch(tmp_path, capsys, ScriptedAdapter(write_report=False))

    assert (code, err) == (PROBLEM, "")
    assert problem_line(out).startswith("watch: problem: ticket a: the step failed: report missing: the specialist wrote no file at ")
    assert problem_line(out).endswith("a.specialist.json; the continuation limit of 2 is reached")


def continuation(count: int) -> tuple[str, dict[str, object]]:
    return "continuation", {
        "ticket": "a", "count": count, "limit": 2, "trigger": "capped", "mode": "resume", "resume_session": "s1",
        "commits": [], "reason": "the specialist ended capped",
    }


def test_the_second_continuation_of_a_step_is_a_problem_and_the_first_is_not(tmp_path, capsys):
    # Capped is not a failure; the second continuation is the problem. No later event goes past it.
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), continuation(1), continuation(2))

    code = delegate.main(["watch", "r1", "--repo", str(repo), *POLL])
    out = capsys.readouterr().out

    assert code == PROBLEM
    assert out.splitlines()[-2:] == ["watch: problem: ticket a: the step continued 2 times (limit 2, trigger capped)", "position 4"]


def test_a_second_continuation_is_history_when_the_run_built_the_step_after_it(tmp_path, capsys, git_identity):
    # The specialist is capped twice, then finishes, and the run is built: the step-end goes past the problem (#151).
    adapter = ScriptedAdapter(outcomes=[("capped", 0), ("capped", 0), ("finished", 0)])

    code, out, err, _ = build_then_watch(tmp_path, capsys, adapter)

    assert (code, err) == (0, "")
    assert "watch: problem" not in out
    continuations = [line.split() for line in out.splitlines() if line.split()[2:3] == ["continuation"]]
    assert [[field for field in line if field.startswith("count=")] for line in continuations] == [["count=1"], ["count=2"]]


def test_a_finished_agent_with_the_exit_status_of_sigterm_and_a_valid_report_is_not_a_problem(tmp_path, capsys, git_identity):
    # A coordinator stops an idle session with SIGTERM after its valid report; the step continues.
    adapter = ScriptedAdapter(outcomes=[("finished", 143)], verifier_exit_status=143)

    code, out, err, events = build_then_watch(tmp_path, capsys, adapter)

    assert (code, err) == (0, "")
    assert "watch: problem" not in out
    assert [e["exit_status"] for e in events if e["event"] in ("adapter-result", "verify-result")] == [143, 143]


@pytest.mark.parametrize("role", ["specialist", "verifier"])
def test_an_agent_that_the_engine_ended_at_the_idle_timeout_is_not_a_problem(tmp_path, capsys, git_identity, role):
    # The session wrote no `result` event, so it ended `failed`; but its report was valid, and the step continues.
    adapter = ScriptedAdapter(idle_roles={role})

    code, out, err, events = build_then_watch(tmp_path, capsys, adapter)

    assert (code, err) == (0, "")
    assert "watch: problem" not in out
    assert [e["role"] for e in events if e["event"] == "idle-timeout"] == [role]


def test_a_finished_agent_with_a_bad_exit_status_and_no_valid_report_is_a_problem_at_its_step_end(tmp_path, capsys, git_identity):
    adapter = ScriptedAdapter(verifier_exit_status=2, write_verifier_report=False)

    code, out, err, _ = build_then_watch(tmp_path, capsys, adapter)

    assert (code, err) == (PROBLEM, "")
    assert problem_line(out) == "watch: problem: ticket a: the step failed: the verifier ended finished with exit status 2"


def test_one_continuation_after_a_capped_specialist_is_not_a_problem(tmp_path, capsys, git_identity):
    adapter = ScriptedAdapter(outcomes=[("capped", 0), ("finished", 0)])

    code, out, err, _ = build_then_watch(tmp_path, capsys, adapter)

    assert (code, err) == (0, "")
    assert "watch: problem" not in out


def test_a_verifier_that_returns_no_result_is_a_problem_that_names_the_ticket(tmp_path, capsys, git_identity):
    code, out, err, _ = build_then_watch(tmp_path, capsys, ScriptedAdapter(write_verifier_report=False))

    assert (code, err) == (PROBLEM, "")
    assert problem_line(out).startswith("watch: problem: ticket a: the step failed: report missing: the verifier wrote no file at ")
    assert problem_line(out).endswith("verdict.json")


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
            "fixup-report", {"round": 1, "valid": False, "path": "/r.json", "reason": "report /r.json not found"},
            "the fix-up specialist returned no valid result: report /r.json not found", id="fixup-no-report",
        ),
        # A report event with no reason gives no reason text, not the text None.
        pytest.param(
            "fixup-report", {"round": 1, "valid": False, "path": "/r.json", "reason": None},
            "the fix-up specialist returned no valid result", id="fixup-no-report-no-reason",
        ),
        pytest.param(
            "report-validation", {"valid": False, "path": "/r.json", "reason": None},
            "the specialist returned no valid result", id="specialist-no-report-no-reason",
        ),
        pytest.param(
            "fixup-report", {"round": 1, "valid": True, "path": "/r.json", "reason": None, "status": "partial"},
            "the fix-up specialist ended partial", id="fixup-partial",
        ),
    ],
)
def test_each_agent_role_that_fails_or_returns_no_result_is_a_problem(tmp_path, capsys, event, fields, reason):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), (event, {"ticket": "a", **fields}), run_end("failed", [], ["a"]))

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


# A tool that a permission mode refused (#96): the agent host ran each agent in "dontAsk" mode with an empty allow list.
REFUSED_HINT: str = "give the agents a settings file with DELEGATE_AGENT_SETTINGS"


def test_a_tool_that_a_permission_mode_refused_is_in_the_journal_and_is_history_when_the_run_built_the_step(tmp_path, capsys, git_identity):
    adapter = ScriptedAdapter(refusals={"specialist": (("Bash", "don't ask"), ("Edit", "don't ask"))})

    code, out, _, events = build_then_watch(tmp_path, capsys, adapter)

    (result,) = [event for event in events if event["event"] == "adapter-result"]
    assert result["refused"] == [{"tool": "Bash", "mode": "don't ask"}, {"tool": "Edit", "mode": "don't ask"}]
    # The scripted specialist does its work also with the refusal, and the run is built: the step-end goes past it (#151).
    assert code == 0
    assert "watch: problem" not in out


def test_an_agent_with_no_refused_tool_writes_no_refused_field(tmp_path, capsys, git_identity):
    _, _, _, events = build_then_watch(tmp_path, capsys, ScriptedAdapter())

    (result,) = [event for event in events if event["event"] == "adapter-result"]
    assert "refused" not in result


@pytest.mark.parametrize(
    ("event", "fields", "role"),
    [
        pytest.param("adapter-result", {}, "specialist", id="specialist"),
        pytest.param("verify-result", {"round": 0}, "verifier", id="verifier"),
        pytest.param("fixup-result", {"round": 1}, "fix-up specialist", id="fixup"),
    ],
)
def test_each_agent_role_that_a_permission_mode_refused_is_a_problem(tmp_path, capsys, event, fields, role):
    repo = bare_repo(tmp_path)
    refused = [{"tool": "Bash", "mode": "don't ask"}, {"tool": "Edit", "mode": "don't ask"}]
    result = {"exit_status": 0, "end_state": "finished", "session_id": None, "event_stream": "/s", "refused": refused}
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), (event, {"ticket": "a", **fields, **result}), run_end("failed", [], ["a"]))

    code = delegate.main(["watch", "r1", "--repo", str(repo), *POLL])
    out = capsys.readouterr().out

    assert code == PROBLEM
    assert problem_line(out) == f"watch: problem: ticket a: the {role} was refused Bash, Edit in don't ask mode; {REFUSED_HINT}"


def test_a_refused_result_after_an_idle_timeout_clears_the_idle_mark_so_the_next_result_is_checked(tmp_path, capsys):
    # The idle timeout belongs to the result event that follows it. A refused result consumes it as the idle branch
    # does, so a later failed result of the same ticket is a problem and not skipped as idle (#96).
    repo = bare_repo(tmp_path)
    refused = {"exit_status": 0, "end_state": "finished", "session_id": None, "event_stream": "/s", "refused": [{"tool": "Bash", "mode": "don't ask"}]}
    failed = {"exit_status": 1, "end_state": "failed", "session_id": None, "event_stream": "/s"}
    write_journal(
        repo, "r1", run_start("r1", "a"), step_start("a"),
        ("idle-timeout", {"ticket": "a", "role": "verifier", "round": 0, "seconds": 900}),
        ("verify-result", {"ticket": "a", "round": 0, **refused}),
        ("verify-result", {"ticket": "a", "round": 1, **failed}),
        run_end("failed", [], ["a"]),
    )

    code = delegate.main(["watch", "r1", "--repo", str(repo), "--from", "4", *POLL])
    out = capsys.readouterr().out

    assert code == PROBLEM
    assert problem_line(out) == "watch: problem: ticket a: the verifier ended failed with exit status 1"
