"""A REJECT in `assure` mode starts a scoped fix-up round, and at most two rounds run.

Each case writes a workflow into a real temporary git repository, registers a
scripted adapter by name, and calls `delegate.main`. The scripted adapter plays
the specialist, the fix-up specialist and each fresh verifier.
"""

import json
from pathlib import Path

import pytest

from delegate import adapters, delegate

from .support import WORKFLOW, ScriptedAdapter, event_names, git, make_repo, read_journal, valid_report

SPECIALIST_CLAIM = "SPECIALIST-CLAIM-3318 the findings are fixed"
SPECIALIST_NOTE = "SPECIALIST-NOTE-8120 I added a header file"
FINDING = "FINDING-4471 the header row is missing from feature.txt"


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


def test_an_accept_after_a_fixup_moves_the_run_branch_to_the_tip_that_holds_the_fixup_commit(tmp_path, capsys, scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = [FINDING]
    repo = make_repo(tmp_path)
    base_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert len(scripted.fixup_calls) == 1
    first, fixup = git(repo, "rev-list", "--reverse", f"main..run/demo-a").split()
    assert git(repo, "show", "--name-only", "--format=", fixup).split() == ["fixup-1.txt"]
    assert tip(repo, "run/demo") == fixup != base_tip
    assert tip(repo, "main") == base_tip
    events = read_journal(out)
    verdicts = [(e["mode"], e["verdict"]) for e in events if e["event"] == "verdict"]
    assert verdicts == [("full", "REJECT"), ("fix-up", "ACCEPT")]
    assert [e["to_commit"] for e in events if e["event"] == "run-branch-advance"] == [fixup]
    assert next(e for e in events if e["event"] == "run-end")["result"] == "built"


def test_the_fixup_verifier_brief_holds_the_findings_and_the_delta_and_not_the_full_diff_or_the_specialist_report(
    tmp_path, capsys, scripted
):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = [FINDING]
    scripted.report_text = lambda request, head: json.dumps(
        valid_report("a", "run/demo-a", head, summary=SPECIALIST_CLAIM, judgement_calls=SPECIALIST_NOTE)
    )
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    first, fixup = scripted.verifier_calls
    assert first.prompt.startswith("## Task")
    prompt = fixup.prompt
    assert prompt.startswith("## Findings under verification")
    assert f"- {FINDING}" in prompt
    rejected = git(repo, "rev-list", "--reverse", "main..run/demo-a").split()[0]
    delta = prompt.split("## Delta")[1].split("## Gates")[0]
    assert f"git diff {rejected}..run/demo-a" in delta
    assert "+++ b/fixup-1.txt" in delta
    assert "feature.txt" not in delta  # the full diff would hold the file of the rejected commit
    for leaked in (SPECIALIST_CLAIM, SPECIALIST_NOTE):
        assert leaked not in prompt
    assert str(scripted.fixup_calls[0].report_path) not in prompt
    assert "## Task" not in prompt and "The export command writes a CSV file." not in prompt
    assert "test -f feature.txt" in prompt.split("## Gates")[1].split("## ")[0]
    assert str(fixup.cwd) in prompt.split("## Working copy")[1]
    report = prompt.split("## Report")[1]
    assert str(fixup.report_path) in report and "`mode` is `fix-up`" in report


def test_the_specialist_gets_a_fixup_brief_in_the_same_worktree_and_the_findings_go_to_a_file(tmp_path, capsys, scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = [FINDING, "A second finding."]
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (call,) = scripted.calls
    (fixup,) = scripted.fixup_calls
    assert fixup.cwd == call.cwd and fixup.agent == "python-specialist"
    rejected = git(repo, "rev-list", "--reverse", "main..run/demo-a").split()[0]
    assert f"- {FINDING}\n- A second finding." in fixup.prompt.split("## Findings to fix")[1].split("## ")[0]
    assert rejected in fixup.prompt.split("## Rejected commit")[1]
    assert "new commit" in fixup.prompt.split("## Rejected commit")[1]
    start = next(e for e in read_journal(out) if e["event"] == "fixup-start")
    assert (start["ticket"], start["round"], start["rejected_commit"]) == ("a", 1, rejected)
    assert Path(start["findings_file"]).read_text() == f"- {FINDING}\n- A second finding.\n"


def test_each_round_spawns_a_new_verifier_in_a_new_copy_and_a_verifier_session_is_never_resumed(tmp_path, capsys, scripted):
    scripted.verdict_sequence = ["REJECT", "REJECT", "ACCEPT"]
    scripted.verdict_findings = [FINDING]
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert len(scripted.verifier_calls) == 3 and len(scripted.fixup_calls) == 2
    assert len({str(call.cwd) for call in scripted.verifier_calls}) == 3
    assert not any(call.cwd.exists() for call in scripted.verifier_calls)
    events = read_journal(out)
    results = [e for e in events if e["event"] == "verify-result"]
    assert [e["round"] for e in results] == [0, 1, 2]
    assert len({e["session_id"] for e in results}) == 3
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["REJECT", "REJECT", "ACCEPT"]
    # The delta of round 2 starts at the commit that round 1 rejected, so it holds the second fix-up only.
    delta = scripted.verifier_calls[2].prompt.split("## Delta")[1].split("## Gates")[0]
    assert "+++ b/fixup-2.txt" in delta and "fixup-1.txt" not in delta
    assert tip(repo, "run/demo") == tip(repo, "run/demo-a")


WITH_SECOND_TICKET = (
    WORKFLOW + '\n[[tickets]]\nid = "b"\ntext = "The export command writes a header row."\nstack = "python"\nblocked-by = ["a"]\n'
)


def test_after_two_rejected_fixup_rounds_the_step_fails_the_run_ends_and_the_run_branch_does_not_move(tmp_path, capsys, scripted):
    scripted.verdict = "REJECT"
    scripted.verdict_findings = [FINDING]
    repo = make_repo(tmp_path, WITH_SECOND_TICKET)
    base_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err and FINDING in err
    assert len(scripted.fixup_calls) == 2 and len(scripted.verifier_calls) == 3  # no third round
    assert tip(repo, "run/demo") == base_tip
    events = read_journal(out)
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["REJECT", "REJECT", "REJECT"]
    step_end = next(e for e in events if e["event"] == "step-end")
    assert step_end["state"] == "failed" and "after 2 fix-up rounds" in step_end["reason"]
    assert "run-branch-advance" not in event_names(events)
    assert [e["ticket"] for e in events if e["event"] == "step-start"] == ["a"]  # ticket b never starts
    run_end = next(e for e in events if e["event"] == "run-end")
    assert (run_end["result"], run_end["failed"]) == ("failed", ["a"])


@pytest.mark.parametrize(
    ("changes", "cause"),
    [
        pytest.param({"fixup_amend": True}, "amended or rewrote", id="the-specialist-amended-the-rejected-commit"),
        pytest.param({"fixup_commit": False}, "no fix-up to verify", id="the-specialist-added-no-commit"),
    ],
)
def test_a_fixup_that_does_not_sit_on_top_of_the_rejected_commit_makes_the_brief_generator_refuse_and_the_step_fails(
    tmp_path, capsys, scripted, changes, cause
):
    scripted.verdict = "REJECT"
    scripted.verdict_findings = [FINDING]
    for name, value in changes.items():
        setattr(scripted, name, value)
    repo = make_repo(tmp_path, WITH_SECOND_TICKET)
    base_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err and cause in err
    assert len(scripted.verifier_calls) == 1  # no verifier reads a branch that has no delta
    assert tip(repo, "run/demo") == base_tip
    events = read_journal(out)
    (refused,) = [e for e in events if e["event"] == "fixup-refused"]
    assert (refused["ticket"], refused["round"]) == ("a", 1) and cause in refused["reason"]
    step_end = next(e for e in events if e["event"] == "step-end")
    assert step_end["state"] == "failed" and cause in step_end["reason"]
    assert [e["ticket"] for e in events if e["event"] == "step-start"] == ["a"]


@pytest.mark.parametrize(
    ("changes", "cause"),
    [
        pytest.param({"fixup_end_state": "failed", "fixup_exit_status": 1}, "failed with exit status 1", id="the-specialist-failed"),
        pytest.param({"fixup_end_state": "capped"}, "capped with exit status 0", id="the-specialist-hit-its-cap"),
        pytest.param({"fixup_exit_status": 2}, "finished with exit status 2", id="the-exit-status-is-not-zero"),
        pytest.param({"fixup_write_report": False}, "report missing", id="no-report"),
        pytest.param({"fixup_report_text": "not json"}, "not valid JSON", id="a-report-that-is-not-json"),
        pytest.param(
            {"fixup_report_text": json.dumps(valid_report("a", "run/demo-a", "0" * 40, status="blocked", blocked_reason="no access"))},
            "'blocked': no access",
            id="the-specialist-reports-blocked",
        ),
    ],
)
def test_a_fixup_specialist_that_fails_caps_or_reports_badly_fails_the_step_and_no_second_verifier_starts(
    tmp_path, capsys, scripted, changes, cause
):
    scripted.verdict = "REJECT"
    scripted.verdict_findings = [FINDING]
    for name, value in changes.items():
        setattr(scripted, name, value)
    repo = make_repo(tmp_path)
    base_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err and cause in err
    assert len(scripted.verifier_calls) == 1
    assert tip(repo, "run/demo") == base_tip
    events = read_journal(out)
    step_end = next(e for e in events if e["event"] == "step-end")
    assert step_end["state"] == "failed" and cause in step_end["reason"]
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["REJECT"]


def test_a_fixup_that_turns_a_gate_red_fails_the_step_whatever_the_specialist_claims(tmp_path, capsys, scripted):
    scripted.verdict = "REJECT"
    scripted.verdict_findings = [FINDING]
    workflow = WORKFLOW.replace('gates = ["test -f feature.txt"]', 'gates = ["test ! -f fixup-1.txt"]')
    repo = make_repo(tmp_path, workflow)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "gates red: test ! -f fixup-1.txt" in err
    assert len(scripted.verifier_calls) == 1
    gates = [(e["round"], e["green"]) for e in read_journal(out) if e["event"] == "gate-result"]
    assert gates == [(0, True), (0, True), (1, False)]  # the build, the rebase, then the fix-up
