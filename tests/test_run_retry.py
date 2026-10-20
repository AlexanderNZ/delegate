"""`delegate run --resume RUN_ID --retry TICKET`: go on with one failed step at the first phase that did not complete.

A failed step has a `step-end`, so a plain resume never builds it again, and a
run that ended cannot be resumed at all. `--retry` uses the worktree and the
commits of the step again. It spawns no specialist, and goes on at the gates,
at the verifier, or at the scoped verifier of the fix-up round. Each case calls
`delegate.main` against a real temporary git repository, with the scripted
adapter in the place of the harness.
"""

import pytest

from .support import (
    append_events, event_names, git, journal_path, make_repo, read_journal, read_journal_file, resume_main, run_main, ticket_of, tree,
)
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
    # The gates do not run again, and the brief gives the green output that the journal holds for the fix-up commit (#157).
    gates = verifier.prompt.split("\n## Gates\n", 1)[1].split("\n## ", 1)[0]
    assert f"`{fixed}`" in gates and "fixup-1.txt" in gates and "Do not run these gates again" in gates
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


def test_an_assure_retry_that_builds_the_step_also_builds_the_dependants_that_the_step_made_the_engine_skip(
    tmp_path, capsys, scripted
):
    # Ticket b waits on a, and c waits on b. The verifier of a ends with no verdict, so a fails, and b and c are skipped.
    end_with_no_verdict(scripted, scoped_only=False)
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",)))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    first = read_journal_file(repo, run_id)
    assert [e["ticket"] for e in first if e["event"] == "skip"] == ["b", "c"]
    healthy(scripted, "ACCEPT", "ACCEPT", "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    assert [ticket_of(call) for call in scripted.calls] == ["b", "c"]
    assert [ticket_of(call) for call in scripted.verifier_calls] == ["a", "b", "c"]
    retried = after_retry(read_journal_file(repo, run_id))
    assert [(e["ticket"], e["state"]) for e in retried if e["event"] == "step-end"] == [("a", "built"), ("b", "built"), ("c", "built")]
    assert "skip" not in event_names(retried)
    assert {"a.txt", "b.txt", "c.txt"} <= set(tree(repo, "run/demo"))
    end = retried[-1]
    assert (end["event"], end["result"], end["built"], end["failed"], end["skipped"]) == ("run-end", "built", ["a", "b", "c"], [], [])


def test_an_assure_retry_leaves_skipped_a_dependant_with_another_blocker_that_failed_until_that_blocker_is_built(
    tmp_path, capsys, scripted
):
    # Ticket c waits on a and on b. The verifiers of a and b end with no verdict, so both fail, and c is skipped.
    end_with_no_verdict(scripted, scoped_only=False)
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b") + ticket("c", ("a", "b")))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    # B still failed, so c is skipped only because of b now, and the run fails.
    assert code == 1 and "ticket c: skipped: blocked by b, which failed\n" in err
    assert scripted.calls == []
    skips = [e for e in after_retry(read_journal_file(repo, run_id)) if e["event"] == "skip"]
    assert [(e["ticket"], e["blockers"]) for e in skips] == [("c", ["b"])]
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["built"], end["failed"], end["skipped"]) == ("run-end", ["a"], ["b"], ["c"])
    healthy(scripted, "ACCEPT", "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b")

    assert (code, err) == (0, "")
    assert [ticket_of(call) for call in scripted.calls] == ["c"]
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["result"], end["built"], end["failed"], end["skipped"]) == ("run-end", "built", ["a", "b", "c"], [], [])


def test_an_assure_retry_of_a_step_that_crashed_builds_the_tickets_that_the_run_did_not_reach(tmp_path, capsys, scripted):
    # The verifier of a raises, so the step crashes, the run ends, and b, which does not wait on a, is not reached.
    def crash(request):
        raise RuntimeError("the harness broke")

    scripted.during_verifier = crash
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    (skip,) = [e for e in read_journal_file(repo, run_id) if e["event"] == "skip"]
    assert (skip["ticket"], skip["reason"]) == ("b", "the run ended after ticket a crashed")
    healthy(scripted, "ACCEPT", "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    assert [ticket_of(call) for call in scripted.calls] == ["b"]
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["result"], end["built"], end["failed"], end["skipped"]) == ("run-end", "built", ["a", "b"], [], [])


def test_a_run_that_ends_with_a_skipped_ticket_and_no_failed_step_exits_one(tmp_path, capsys, scripted):
    # The journal of a retry by version 1.7.1: the retry built a, b stayed skipped, and the run ended `built`.
    # A resume with a plan change goes on with that run: it builds the new ticket c, and b stays skipped.
    end_with_no_verdict(scripted, scoped_only=False)
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    append_events(
        journal_path(repo, run_id),
        ("resume", {"run_id": run_id, "built": [], "failed": ["a"], "skipped": ["b"], "open": None}),
        ("retry", {"ticket": "a", "phase": "verifier", "round": 0, "reason": "the verifier ended failed with exit status -9"}),
        ("step-end", {"ticket": "a", "state": "built", "reason": None}),
        ("run-end", {"result": "built", "built": ["a"], "failed": [], "skipped": ["b"]}),
    )
    (repo / "workflow.toml").write_text(HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c"))
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1 and "Traceback" not in err
    assert "ticket b: skipped" in err and "run failed" in out
    assert [ticket_of(call) for call in scripted.calls] == ["c"]
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["result"], end["built"], end["failed"], end["skipped"]) == ("run-end", "failed", ["a", "c"], [], ["b"])


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


@pytest.mark.parametrize("mode", ["assure", "economy"])
def test_a_retry_of_a_run_that_an_invariant_violation_halted_exits_one_names_the_violation_and_changes_nothing(
    tmp_path, capsys, scripted, mode
):
    # In `assure` mode the verifier of ticket a halts the run, and b is skipped. In `economy` mode the chain verifier
    # runs in the worktree of a, the only ticket of the chain.
    text = HEAD + ticket("a") + ticket("b", ("a",)) if mode == "assure" else economy(ticket("a"))
    repo = make_repo(tmp_path, text)

    def write_in_the_real_worktree(request):
        (run_dir,) = (repo / ".git" / "delegate" / "worktrees").iterdir()
        (run_dir / "a" / "oops.txt").write_text("by the verifier\n")

    scripted.during_verifier = write_in_the_real_worktree
    code, run_id = first_run(repo, capsys)
    assert code == 1
    assert "invariant-violation" in event_names(read_journal_file(repo, run_id))

    refused(repo, capsys, run_id, scripted, "a", "invariant violation", "oops.txt", "start a new run")


def test_retry_without_resume_is_a_usage_error(tmp_path, capsys):
    repo = make_repo(tmp_path, HEAD + ticket("a"))

    try:
        code = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--retry", "a")[0]
    except SystemExit as stop:
        code = stop.code

    assert code == 2 and "--retry" in capsys.readouterr().err
    assert not (repo / ".git" / "delegate").exists()


# `economy` mode: the retry goes on with the specialist of the step, or with its check, and then the chain goes on.


def economy(*tickets):
    return HEAD.replace('mode = "assure"', 'mode = "economy"') + "".join(tickets)


def step_events(events, ticket_id, kind):
    return [e for e in events if e["event"] == kind and e.get("ticket") == ticket_id]


def test_an_economy_retry_continues_the_failed_specialist_in_its_worktree_then_builds_the_rest_of_the_chain(tmp_path, capsys, scripted):
    # The specialist of a commits its work but ends capped twice, so the step fails at the continuation limit of 1.
    # Ticket b waits on a, so the first run skips it, and no ticket is built.
    scripted.outcomes = [("capped", 0)]
    repo = make_repo(tmp_path, economy(ticket("a"), ticket("b", ("a",))))
    code, run_id = first_run(repo, capsys)
    assert code == 1 and len(scripted.continuation_calls) == 1
    work_of_a = git(repo, "rev-parse", "run/demo-a").strip()
    worktree_of_a = scripted.calls[0].cwd
    healthy(scripted, "ACCEPT")
    scripted.outcomes = [("finished", 0)]

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    # A continues in its own worktree from its commit; b gets its first brief; one verifier checks the chain.
    (continued,) = scripted.continuation_calls
    assert continued.cwd == worktree_of_a and work_of_a in continued.prompt and "Ticket a" in continued.prompt
    assert [ticket_of(call) for call in scripted.calls] == ["b"]
    assert len(scripted.verifier_calls) == 1
    events = read_journal_file(repo, run_id)
    retried = after_retry(events)
    assert {k: retried[0][k] for k in ("ticket", "phase", "round")} == {"ticket": "a", "phase": "specialist", "round": 0}
    assert "the continuation limit of 1 is reached" in retried[0]["reason"]
    # The spawn of the retry is no continuation of the count, as the spawn of a resume is not, so the limit is not reached.
    assert [e["role"] for e in step_events(retried, "a", "agent-start")] == ["specialist"]
    assert step_events(retried, "a", "continuation") == [] and step_events(retried, "a", "continuation-limit") == []
    assert [e["state"] for e in step_events(events, "a", "step-end")] == ["failed", "built"]
    assert [e["state"] for e in step_events(retried, "b", "step-end")] == ["built"]
    # B starts from the tip of a, so the chain holds both, and the run branch gets the commit of the first run of a.
    (start_of_b,) = step_events(events, "b", "step-start")
    assert start_of_b["base_commit"] == git(repo, "rev-parse", "run/demo-a").strip()
    assert git(repo, "merge-base", "--is-ancestor", work_of_a, "run/demo") == ""
    assert {"a.txt", "b.txt"} <= set(tree(repo, "run/demo"))
    assert [e["phase"] for e in retried if e["event"] == "gate-result"] == ["chain"]
    end = events[-1]
    assert (end["event"], end["result"], end["built"], end["failed"], end["skipped"]) == ("run-end", "built", ["a", "b"], [], [])


def test_an_economy_retry_after_the_run_branch_moved_verifies_only_the_tickets_that_it_builds(tmp_path, capsys, scripted):
    # Ticket a is built and accepted. The specialist of b commits and ends failed twice. Ticket c waits on b.
    scripted.failing_tickets = {"b"}
    repo = make_repo(tmp_path, economy(ticket("a"), ticket("b"), ticket("c", ("b",))))
    code, run_id = first_run(repo, capsys)
    assert code == 1 and "a.txt" in tree(repo, "run/demo") and "b.txt" not in tree(repo, "run/demo")
    accepted = git(repo, "rev-parse", "run/demo").strip()
    healthy(scripted, "ACCEPT")
    scripted.failing_tickets = set()

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b")

    assert (code, err) == (0, "")
    assert [ticket_of(call) for call in scripted.continuation_calls] == ["b"]
    assert [ticket_of(call) for call in scripted.calls] == ["c"]
    # The verifier of the retry sees the tickets b and c, and not the accepted ticket a.
    (verifier,) = scripted.verifier_calls
    assert "Ticket b adds its file." in verifier.prompt and "Ticket c adds its file." in verifier.prompt
    assert "Ticket a adds its file." not in verifier.prompt
    events = read_journal_file(repo, run_id)
    (advance,) = [e for e in after_retry(events) if e["event"] == "run-branch-advance"]
    assert (advance["from_commit"], advance["to_commit"]) == (accepted, git(repo, "rev-parse", "run/demo-c").strip())
    assert {"a.txt", "b.txt", "c.txt"} <= set(tree(repo, "run/demo"))
    assert (events[-1]["event"], events[-1]["result"], events[-1]["failed"]) == ("run-end", "built", [])
    assert sorted(events[-1]["built"]) == ["a", "b", "c"]


def refused(repo, capsys, run_id, scripted, ticket_id, *words):
    """Retry `ticket_id` and check that the command exits 1, names the ticket and the words, and changes nothing."""
    before = journal_path(repo, run_id).read_bytes()
    tips = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", ticket_id)

    assert code == 1 and "Traceback" not in err
    assert f"ticket {ticket_id!r}" in err and all(word in err for word in words), err
    assert journal_path(repo, run_id).read_bytes() == before
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads") == tips
    assert scripted.calls == [] and scripted.continuation_calls == [] and scripted.verifier_calls == []


def test_an_economy_retry_of_a_step_that_a_later_built_ticket_went_past_exits_one_and_changes_nothing(tmp_path, capsys, scripted):
    # Ticket b does not wait on a, so it starts from the base of the run and is built without the commits of a.
    scripted.failing_tickets = {"a"}
    repo = make_repo(tmp_path, economy(ticket("a"), ticket("b")))
    code, run_id = first_run(repo, capsys)
    assert code == 1

    refused(repo, capsys, run_id, scripted, "a", "ticket 'b'", "built")


def test_an_economy_retry_of_a_ticket_that_the_chain_verification_failed_exits_one_and_changes_nothing(tmp_path, capsys, scripted):
    scripted.verdict_sequence, scripted.verdict_findings = ["REJECT"], [FINDING]
    repo = make_repo(tmp_path, economy(ticket("a")))
    code, run_id = first_run(repo, capsys)
    assert code == 1

    refused(repo, capsys, run_id, scripted, "a", "verification of the chain")


def test_an_economy_retry_of_a_step_whose_chain_start_moved_exits_one_and_changes_nothing(tmp_path, capsys, scripted):
    # Ticket b starts from the tip of a. Then the verification of the chain adds a fix-up commit to the branch of a.
    scripted.failing_tickets = {"b"}
    scripted.verdict_sequence, scripted.verdict_findings = ["REJECT", "ACCEPT"], [FINDING]
    repo = make_repo(tmp_path, economy(ticket("a"), ticket("b")))
    code, run_id = first_run(repo, capsys)
    assert code == 1 and "fixup-1.txt" in tree(repo, "run/demo")

    refused(repo, capsys, run_id, scripted, "b", "starts at")


def test_an_economy_retry_after_a_valid_committed_report_spawns_no_specialist_and_checks_the_step_again(tmp_path, capsys, scripted):
    # The report is valid and says committed, but the commit changes a hotspot, so the check of the step fails.
    scripted.files_by_ticket["a"] = {"a.txt": "a\n", "LICENSE": "changed\n"}
    repo = make_repo(tmp_path, economy(ticket("a")))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert code == 1 and "Traceback" not in err
    assert scripted.calls == [] and scripted.continuation_calls == [] and scripted.verifier_calls == []
    retried = after_retry(read_journal_file(repo, run_id))
    assert {k: retried[0][k] for k in ("ticket", "phase", "round")} == {"ticket": "a", "phase": "gates", "round": 0}
    assert "agent-start" not in event_names(retried)
    (step_end,) = step_events(retried, "a", "step-end")
    assert step_end["state"] == "failed" and step_end["reason"].startswith("hotspot finding: LICENSE")
