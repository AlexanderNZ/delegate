"""`delegate run --resume RUN_ID --retry TICKET`: go on with one failed step at the first phase that did not complete.

A failed step has a `step-end`, so a plain resume never builds it again, and a
run that ended cannot be resumed at all. `--retry` uses the worktree and the
commits of the step again. It spawns no specialist, and goes on at the gates,
at the verifier, or at the scoped verifier of the fix-up round. Each case calls
`delegate.main` against a real temporary git repository, with the scripted
adapter in the place of the harness.
"""

from .support import event_names, git, journal_path, make_repo, read_journal, read_journal_file, resume_main, run_main, tree
from .test_run_multi import HEAD, ticket

FINDING = "FINDING-5120 a.txt holds no header row"
SCOPED = "## Findings under verification"


def first_run(repo, capsys):
    """Run `workflow.toml` of `repo` to its end. Return the exit code and the run id."""
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    return code, next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]


def end_with_no_verdict(scripted, scoped_only):
    """The harness ends a verifier at its time limit: exit status -9, and no report. `scoped_only` limits it to the fix-up verifier."""

    def time_limit(request):
        killed = request.prompt.startswith(SCOPED) or not scoped_only
        scripted.write_verifier_report = not killed
        scripted.verifier_end_state, scripted.verifier_exit_status = ("failed", -9) if killed else ("finished", 0)

    scripted.during_verifier = time_limit


def healthy(scripted, *verdicts):
    """From now on each verifier ends with a verdict: `verdicts` in turn. Forget the calls of the first run."""
    scripted.during_verifier = None
    scripted.write_verifier_report = True
    scripted.verifier_end_state, scripted.verifier_exit_status = "finished", 0
    scripted.verdict_sequence = list(verdicts)
    for calls in (scripted.calls, scripted.continuation_calls, scripted.fixup_calls, scripted.verifier_calls):
        calls.clear()


def after_retry(events):
    """The events from the `retry` event on."""
    start = event_names(events).index("retry")
    return events[start:]


def gate_workflow(command):
    return HEAD.replace('gates = ["ls *.txt"]', f'gates = ["{command}"]') + ticket("a")


def test_a_step_that_failed_at_the_scoped_verifier_goes_on_at_the_scoped_verifier_with_no_new_specialist(tmp_path, capsys, scripted):
    scripted.verdict_sequence, scripted.verdict_findings = ["REJECT"], [FINDING]
    end_with_no_verdict(scripted, scoped_only=True)
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    fixed = git(repo, "rev-parse", "run/demo-a").strip()
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    assert scripted.calls == [] and scripted.continuation_calls == [] and scripted.fixup_calls == []
    (verifier,) = scripted.verifier_calls
    assert verifier.prompt.startswith(SCOPED) and FINDING in verifier.prompt
    assert git(repo, "rev-parse", "run/demo").strip() == fixed
    events = read_journal_file(repo, run_id)
    retried = after_retry(events)
    assert {k: retried[0][k] for k in ("ticket", "phase", "round")} == {"ticket": "a", "phase": "scoped-verifier", "round": 1}
    assert "the verifier ended failed with exit status -9" in retried[0]["reason"]
    assert "gate-result" not in event_names(retried)
    assert [e["round"] for e in events if e["event"] == "verify-start"] == [0, 1, 1]
    assert [e["state"] for e in events if e["event"] == "step-end"] == ["failed", "built"]
    assert (events[-1]["event"], events[-1]["result"], events[-1]["built"], events[-1]["failed"]) == ("run-end", "built", ["a"], [])


def test_a_verifier_that_ended_with_no_verdict_is_run_again_at_its_round_and_leaves_both_fix_up_rounds(tmp_path, capsys, scripted):
    end_with_no_verdict(scripted, scoped_only=False)
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    healthy(scripted, "REJECT", "REJECT", "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    assert scripted.calls == [] and scripted.continuation_calls == [] and len(scripted.fixup_calls) == 2
    assert not scripted.verifier_calls[0].prompt.startswith(SCOPED)
    events = read_journal_file(repo, run_id)
    assert {k: after_retry(events)[0][k] for k in ("phase", "round")} == {"phase": "verifier", "round": 0}
    assert [e["round"] for e in events if e["event"] == "verify-start"] == [0, 0, 1, 2]
    assert {"a.txt", "fixup-1.txt", "fixup-2.txt"} <= set(tree(repo, "run/demo"))


def test_a_step_whose_gates_were_red_goes_on_at_the_gates_with_no_new_specialist(tmp_path, capsys, scripted):
    flag = tmp_path / "green"
    repo = make_repo(tmp_path, gate_workflow(f"test -f {flag}"))
    code, run_id = first_run(repo, capsys)
    assert code == 1 and len(scripted.continuation_calls) == 2
    flag.touch()
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    assert scripted.calls == [] and scripted.continuation_calls == [] and len(scripted.verifier_calls) == 1
    retried = after_retry(read_journal_file(repo, run_id))
    assert {k: retried[0][k] for k in ("phase", "round")} == {"phase": "gates", "round": 0}
    # The gate was red on the commit, so it runs again. The rebase keeps the commit, so the gate does not run a third time.
    assert [(e["phase"], e["green"]) for e in retried if e["event"] == "gate-result"] == [("build", True)]
    assert [e["phase"] for e in retried if e["event"] == "gate-skip"] == ["rebase"]
    assert "a.txt" in tree(repo, "run/demo")


def test_a_fix_up_whose_gates_were_red_goes_on_at_its_gates_and_then_the_scoped_verifier(tmp_path, capsys, scripted):
    flag = tmp_path / "green"
    scripted.verdict_sequence, scripted.verdict_findings = ["REJECT"], [FINDING]
    repo = make_repo(tmp_path, gate_workflow(f"test ! -f fixup-1.txt || test -f {flag}"))
    code, run_id = first_run(repo, capsys)
    assert code == 1 and len(scripted.fixup_calls) == 1
    flag.touch()
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    assert scripted.calls == [] and scripted.fixup_calls == [] and scripted.continuation_calls == []
    (verifier,) = scripted.verifier_calls
    assert verifier.prompt.startswith(SCOPED)
    retried = after_retry(read_journal_file(repo, run_id))
    assert {k: retried[0][k] for k in ("phase", "round")} == {"phase": "gates", "round": 1}
    assert [(e["phase"], e["round"], e["green"]) for e in retried if e["event"] == "gate-result"] == [("fixup", 1, True)]
    assert "fixup-1.txt" in tree(repo, "run/demo")


def test_a_retry_after_the_run_branch_moved_rebases_runs_the_gates_and_verifies_the_whole_branch(tmp_path, capsys, scripted):
    # Ticket a fails at its scoped verifier; ticket b is built after it, so the run branch moves.
    scripted.verdict_sequence, scripted.verdict_findings = ["REJECT", "REJECT", "ACCEPT"], [FINDING]
    end_with_no_verdict(scripted, scoped_only=True)
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))
    code, run_id = first_run(repo, capsys)
    assert code == 1 and "b.txt" in tree(repo, "run/demo")
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    assert scripted.calls == [] and scripted.fixup_calls == []
    (verifier,) = scripted.verifier_calls
    assert not verifier.prompt.startswith(SCOPED)
    retried = after_retry(read_journal_file(repo, run_id))
    assert {k: retried[0][k] for k in ("phase", "round")} == {"phase": "gates", "round": 1}
    assert [e["result"] for e in retried if e["event"] == "rebase"] == ["rebased"]
    assert [(e["phase"], e["green"]) for e in retried if e["event"] == "gate-result"] == [("rebase", True)]
    assert {"a.txt", "b.txt", "fixup-1.txt"} <= set(tree(repo, "run/demo"))
    assert sorted(retried[-1]["built"]) == ["a", "b"] and retried[-1]["failed"] == []


def test_a_retry_of_a_built_step_exits_one_names_the_ticket_and_changes_nothing(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, run_id = first_run(repo, capsys)
    assert code == 0
    before = journal_path(repo, run_id).read_bytes()
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert code == 1 and "Traceback" not in err
    assert "ticket 'a'" in err and "built" in err
    assert journal_path(repo, run_id).read_bytes() == before
    assert scripted.calls == [] and scripted.verifier_calls == []


def test_a_retry_of_an_unknown_ticket_exits_one_names_the_ticket_and_changes_nothing(tmp_path, capsys, scripted):
    end_with_no_verdict(scripted, scoped_only=False)
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    before = journal_path(repo, run_id).read_bytes()
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "zz")

    assert code == 1 and "Traceback" not in err
    assert "ticket 'zz'" in err and "not a ticket" in err
    assert journal_path(repo, run_id).read_bytes() == before
    assert scripted.calls == [] and scripted.verifier_calls == []


def test_a_retry_of_a_step_whose_specialist_gave_no_valid_report_exits_one_and_spawns_nothing(tmp_path, capsys, scripted):
    scripted.report_text = "not json"
    scripted.commit = False
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    before = journal_path(repo, run_id).read_bytes()
    scripted.calls.clear()

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert code == 1 and "Traceback" not in err
    assert "ticket 'a'" in err and "specialist" in err
    assert journal_path(repo, run_id).read_bytes() == before
    assert scripted.calls == [] and scripted.verifier_calls == []


def test_retry_without_resume_is_a_usage_error(tmp_path, capsys):
    repo = make_repo(tmp_path, HEAD + ticket("a"))

    try:
        code = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--retry", "a")[0]
    except SystemExit as stop:
        code = stop.code

    assert code == 2 and "--retry" in capsys.readouterr().err
    assert not (repo / ".git" / "delegate").exists()
