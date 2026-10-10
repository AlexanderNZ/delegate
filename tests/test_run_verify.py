"""`delegate run` verifies each built step with a blind verifier and advances the run branch on ACCEPT.

Each case writes a workflow into a real temporary git repository, registers a
scripted adapter by name, and calls `delegate.main`. The scripted adapter plays
the specialist and the verifier, so the engine runs end to end with no model.
"""

import json
from pathlib import Path

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, WORKFLOW, event_names, git, make_repo, read_journal, valid_report, valid_verdict

SPECIALIST_CLAIM = "SPECIALIST-CLAIM-7731 all criteria are met"
SPECIALIST_NOTE = "SPECIALIST-NOTE-5519 I chose a flat file"


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys, *extra):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def tip(repo, branch):
    return git(repo, "rev-parse", branch).strip()


def test_on_accept_the_run_branch_tip_equals_the_ticket_branch_tip_and_the_journal_records_the_verdict(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    base_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert tip(repo, "run/demo") == tip(repo, "run/demo-a") != base_tip
    assert tip(repo, "main") == base_tip
    events = read_journal(out)
    verdict = next(e for e in events if e["event"] == "verdict")
    assert (verdict["ticket"], verdict["mode"], verdict["verdict"], verdict["findings"]) == ("a", "full", "ACCEPT", [])
    names = event_names(events)
    assert names.index("gate-result") < names.index("verdict") < names.index("step-end")
    assert next(e for e in events if e["event"] == "run-end")["result"] == "built"


def test_the_verifier_prompt_holds_the_task_and_the_diff_and_not_one_line_of_the_specialist_report(tmp_path, capsys, scripted):
    scripted.report_text = lambda request, head: json.dumps(
        valid_report("a", "run/demo-a", head, summary=SPECIALIST_CLAIM, judgement_calls=SPECIALIST_NOTE)
    )
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (call,) = scripted.verifier_calls
    assert call.agent == "python-verifier"
    assert "The export command writes a CSV file." in call.prompt
    assert "diff --git a/feature.txt b/feature.txt" in call.prompt and "+feature" in call.prompt
    for leaked in (SPECIALIST_CLAIM, SPECIALIST_NOTE):
        assert leaked not in call.prompt
    assert str(scripted.calls[0].report_path) not in call.prompt


def test_on_reject_the_run_branch_does_not_change_the_journal_records_the_findings_and_the_command_exits_non_zero(
    tmp_path, capsys, scripted
):
    scripted.verdict = "REJECT"
    scripted.verdict_findings = ["feature.txt is not a CSV file.", "The header row is missing."]
    repo = make_repo(tmp_path)
    base_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    assert "feature.txt is not a CSV file." in err and "The header row is missing." in err
    assert tip(repo, "run/demo") == base_tip
    events = read_journal(out)
    verdict = next(e for e in events if e["event"] == "verdict")
    assert (verdict["mode"], verdict["verdict"]) == ("full", "REJECT")
    assert verdict["findings"] == ["feature.txt is not a CSV file.", "The header row is missing."]
    assert "run-branch-advance" not in event_names(events)
    step_end = next(e for e in events if e["event"] == "step-end")
    assert step_end["state"] == "failed" and "rejected" in step_end["reason"]
    assert next(e for e in events if e["event"] == "run-end")["result"] == "failed"


def tier_table(tmp_path):
    """A tier table whose model names tell the tier apart."""
    lines = ['[effort]\nlevels = ["low"]\n\n[effort.opencode]\nvariants = ["high"]\n']
    for tier in ("strong", "standard", "cheap", "verifier"):
        lines.append(f'[tiers.{tier}]\nclaude-code = "{tier}-model"\n')
    lines.append('[allowed-models]\nclaude-code = []\n\n[budget]\nclaude-code-description-chars = 1\n')
    path = tmp_path / "tiers.toml"
    path.write_text("\n".join(lines))
    return path


@pytest.mark.parametrize(
    ("overrides", "specialist_model", "verifier_model"),
    [
        pytest.param("", "strong-model", "verifier-model", id="assure-default"),
        pytest.param('specialist = "cheap"\n', "cheap-model", "verifier-model", id="cheap-specialist-keeps-the-verifier-tier"),
        pytest.param('verifier = "strong"\n', "strong-model", "strong-model", id="verifier-override-by-tier-name"),
    ],
)
def test_the_verifier_runs_on_the_verifier_tier_whatever_the_tier_of_the_specialist(
    tmp_path, capsys, scripted, overrides, specialist_model, verifier_model
):
    workflow = WORKFLOW.replace("[stacks.python]", f"[tier-overrides]\n{overrides}\n[stacks.python]") if overrides else WORKFLOW
    repo = make_repo(tmp_path, workflow)

    code, out, err = run(repo, capsys, "--tiers", str(tier_table(tmp_path)))

    assert (code, err) == (0, "")
    assert [c.model for c in scripted.calls] == [specialist_model]
    assert [c.model for c in scripted.verifier_calls] == [verifier_model]
    start = next(e for e in read_journal(out) if e["event"] == "verify-start")
    assert (start["agent"], start["model"]) == ("python-verifier", verifier_model)


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        pytest.param("not json at all", ["not valid JSON"], id="not-json"),
        pytest.param("[1]", ["JSON object"], id="not-an-object"),
        pytest.param(json.dumps({"verdict": "ACCEPT"}), ["mode", "missing"], id="fields-missing"),
        pytest.param(json.dumps(valid_verdict("MAYBE")), ["verdict", "'MAYBE'", "ACCEPT", "REJECT"], id="verdict-outside-the-set"),
        pytest.param(json.dumps(valid_verdict(mode="fix-up")), ["mode", "'fix-up'", "'full'"], id="the-report-names-another-mode"),
        pytest.param(json.dumps(valid_verdict(criteria=["feature.txt"])), ["criteria", "evidence"], id="criteria-are-not-objects"),
        pytest.param(json.dumps(valid_verdict(criteria=[])), ["criteria", "ACCEPT"], id="an-accept-without-evidence"),
        pytest.param(json.dumps(valid_verdict("REJECT", findings=[])), ["findings", "REJECT"], id="a-reject-without-a-finding"),
        pytest.param(json.dumps(valid_verdict(gate_output=3)), ["gate_output", "string"], id="gate-output-not-a-string"),
    ],
)
def test_an_invalid_verdict_report_fails_the_step_and_the_run_branch_does_not_change(tmp_path, capsys, scripted, report, expected):
    scripted.verifier_report_text = report
    repo = make_repo(tmp_path)
    base_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    assert tip(repo, "run/demo") == base_tip
    events = read_journal(out)
    validation = [e for e in events if e["event"] == "verify-report"]
    assert [v["valid"] for v in validation] == [False]
    for part in expected:
        assert part in validation[0]["reason"]
        assert part in err
    assert "verdict" not in event_names(events)


def test_a_missing_verdict_report_fails_the_step_and_the_run_branch_does_not_change(tmp_path, capsys, scripted):
    scripted.write_verifier_report = False
    repo = make_repo(tmp_path)
    base_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert code == 1
    assert tip(repo, "run/demo") == base_tip
    assert "missing" in err and "verifier" in err and "Traceback" not in err


@pytest.mark.parametrize(("end_state", "exit_status"), [("failed", 1), ("capped", 0), ("finished", 2)])
def test_a_verifier_that_ends_failed_capped_or_with_a_bad_exit_status_never_advances_the_run_branch_even_with_an_accept_on_disk(
    tmp_path, capsys, scripted, end_state, exit_status
):
    scripted.verifier_end_state = end_state
    scripted.verifier_exit_status = exit_status  # the ACCEPT report is valid, but the run did not finish
    repo = make_repo(tmp_path)
    base_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert code == 1
    assert tip(repo, "run/demo") == base_tip
    events = read_journal(out)
    result = [e for e in events if e["event"] == "verify-result"]
    assert [(r["end_state"], r["exit_status"]) for r in result] == [(end_state, exit_status)]
    assert "verdict" not in event_names(events)
    assert end_state in err and str(exit_status) in err


def test_the_verifier_works_in_a_temporary_copy_and_a_verifier_that_writes_changes_neither_the_branch_nor_the_worktree(
    tmp_path, capsys, scripted
):
    scripted.verifier_files = {"feature.txt": "broken by the verifier\n", "scratch.txt": "proof\n"}
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (call,) = scripted.verifier_calls
    events = read_journal(out)
    worktree = next(e for e in events if e["event"] == "step-start")["worktree"]
    assert str(call.cwd) != worktree and not str(call.cwd).startswith(str(repo))
    assert scripted.verifier_heads == [tip(repo, "run/demo-a")]  # the copy held the branch tip
    assert git(repo, "show", "run/demo-a:feature.txt") == "feature\n"
    assert git(worktree, "status", "--short") == ""
    assert Path(worktree, "feature.txt").read_text() == "feature\n"
    assert not call.cwd.exists()  # the engine removes the copy


def test_the_copy_of_the_verifier_has_no_remote_so_it_cannot_reach_the_real_repository(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert scripted.verifier_remotes == [[]]


def test_the_verifier_brief_names_the_gates_of_the_stack_the_working_copy_and_the_report_path(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    (call,) = scripted.verifier_calls
    assert "test -f feature.txt" in call.prompt.split("## Gates")[1].split("## ")[0]
    assert str(call.cwd) in call.prompt.split("## Working copy")[1]
    report = call.prompt.split("## Report")[1]
    assert str(call.report_path) in report
    for name in ("mode", "verdict", "criteria", "gate_output", "findings", "unverified"):
        assert f"`{name}`" in report


def test_the_event_stream_of_the_verifier_survives_the_removal_of_its_copy(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    events = read_journal(out)
    result = next(e for e in events if e["event"] == "verify-result")
    verdict = next(e for e in events if e["event"] == "verdict")
    assert open(result["event_stream"]).read() == '{"type":"result"}\n'
    assert json.loads(open(verdict["report"]).read())["verdict"] == "ACCEPT"


def test_an_accepted_ticket_that_cannot_fast_forward_the_run_branch_fails_and_the_run_branch_keeps_the_commit_that_landed_meanwhile(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path)

    def land_a_commit_on_the_run_branch(request):
        # The coordinator commits on the run branch while the verifier runs.
        git(repo, "worktree", "add", "-q", str(tmp_path / "coordinator"), "run/demo")
        (tmp_path / "coordinator" / "hand.txt").write_text("by hand\n")
        git(tmp_path / "coordinator", "add", "-A")
        git(tmp_path / "coordinator", "commit", "-q", "-m", "a commit by hand")

    scripted.during_verifier = land_a_commit_on_the_run_branch

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    assert "run/demo" in err and "fast-forward" in err
    assert git(repo, "show", "run/demo:hand.txt") == "by hand\n"
    assert "feature.txt" not in git(repo, "ls-tree", "-r", "--name-only", "run/demo")
    assert "run-branch-advance" not in event_names(read_journal(out))
