"""`delegate run --resume RUN_ID --retry TICKET`: go on with one failed step at the first phase that did not complete.

A failed step has a `step-end`, so a plain resume never builds it again, and a
run that ended cannot be resumed at all. `--retry` uses the worktree and the
commits of the step again. In `assure` mode it spawns no specialist, and goes on at the gates,
at the verifier, or at the scoped verifier of the fix-up round. After a report of the specialist that says partial or
blocked for a cause that is not a hotspot, it continues the specialist (#284). Each case calls
`delegate.main` against a real temporary git repository, with the scripted
adapter in the place of the harness.
"""

import json

import pytest

from delegate import adapters
from delegate.adapters.git import GitVersionControl
from delegate.adapters.shell import ShellGateRunner
from delegate.run.engine import run_workflow
from delegate.run.workflow import load_workflow
from delegate.shared.tiers import load_tiers

from .support import state_dir
from .support import (
    append_events, event_names, git, journal_path, make_repo, read_journal, read_journal_file, resume_main, run_main, status_row,
    ticket_of, tree, valid_report,
)
from .test_run_multi import HEAD, ticket
from .test_run_pause import RUN_PAUSED, pause, pause_during_the_specialist_of, run_id_of, status_json, status_lines

FINDING = "FINDING-5120 a.txt holds no header row"
SCOPED = "## Findings under verification"


def first_run(repo, capsys):
    """Run `workflow.toml` of `repo` to its end. Return the exit code and the run id."""
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    return code, next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]


def as_an_earlier_version_ended_the_crash(repo, run_id, unreached):
    """Make the journal of a run that crashed what the engine of 1.19.2 and before wrote: no `crashed`, but a `step-end`
    `failed`, a `skip` for each ticket in `unreached`, and a `run-end` `failed`. A crash now leaves the step open (#281).
    Git holds the same state after both versions, so only the end of the journal differs.
    """
    path = journal_path(repo, run_id)
    events = read_journal_file(repo, run_id)
    crashed = events.pop()
    assert crashed["event"] == "crashed"
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    ticket_id = crashed["ticket"]
    built = [e["ticket"] for e in events if e["event"] == "step-end" and e["state"] == "built"]
    reason = f"the run ended after ticket {ticket_id} crashed"
    append_events(
        path,
        ("step-end", {"ticket": ticket_id, "state": "failed", "reason": f"crashed: {crashed['error']}: {crashed['message']}"}),
        *[("skip", {"ticket": t, "blockers": [], "reason": reason}) for t in unreached],
        ("run-end", {"result": "failed", "built": built, "failed": [ticket_id], "skipped": list(unreached)}),
    )


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
    # The gates are red after the first fix-up, so the second fix-up round gets their output; they stay red after it (#277).
    assert code == 1 and len(scripted.fixup_calls) == 2
    flag.touch()
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    assert scripted.calls == [] and scripted.fixup_calls == [] and scripted.continuation_calls == []
    (verifier,) = scripted.verifier_calls
    assert verifier.prompt.startswith(SCOPED)
    retried = after_retry(read_journal_file(repo, run_id))
    assert {k: retried[0][k] for k in ("phase", "round")} == {"phase": "gates", "round": 2}
    assert [(e["phase"], e["round"], e["green"]) for e in retried if e["event"] == "gate-result"] == [("fixup", 2, True)]
    assert {"fixup-1.txt", "fixup-2.txt"} <= set(tree(repo, "run/demo"))


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
    # The resume records the new skip of c before the step of a, as the dry run says it (#298).
    events = read_journal_file(repo, run_id)
    resumed = events[[index for index, event in enumerate(events) if event["event"] == "resume"][-1] :]
    skips = [e for e in resumed if e["event"] == "skip"]
    assert [(e["ticket"], e["blockers"]) for e in skips] == [("c", ["b"])]
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["built"], end["failed"], end["skipped"]) == ("run-end", ["a"], ["b"], ["c"])
    healthy(scripted, "ACCEPT", "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b")

    assert (code, err) == (0, "")
    assert [ticket_of(call) for call in scripted.calls] == ["c"]
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["result"], end["built"], end["failed"], end["skipped"]) == ("run-end", "built", ["a", "b", "c"], [], [])


def pause_at_the_specialist_of_b_after_a_retry_of_a(repo, capsys, scripted, run_id):
    """Retry a, and pause the run while the specialist of b works. The pause stops the run before the gates of b."""
    healthy(scripted, "ACCEPT")
    pause_during_the_specialist_of(repo, capsys, scripted, "b")
    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")
    assert code == RUN_PAUSED, err
    assert [ticket_of(call) for call in scripted.calls] == ["b"]
    scripted.on_specialist = None


def test_a_resume_after_a_retry_that_paused_builds_the_dependants_of_the_tickets_that_the_retry_built(tmp_path, capsys, scripted):
    # Ticket b waits on a, c on b, and e on a, c and d. The verifiers of a and d end with no verdict, so a and d fail,
    # and b, c and e are skipped. A retry of a builds a and pauses at b. The retry of d then builds b, and the skip
    # rule applies again to c and e (#245): their blockers are built, so their steps run.
    scripted.files_by_ticket["e"] = {"e.txt": "e\n"}
    end_with_no_verdict(scripted, scoped_only=False)
    tickets = ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",)) + ticket("d") + ticket("e", ("a", "c", "d"))
    repo = make_repo(tmp_path, HEAD + tickets)
    code, run_id = first_run(repo, capsys)
    assert code == 1
    assert [e["ticket"] for e in read_journal_file(repo, run_id) if e["event"] == "skip"] == ["b", "c", "e"]
    pause_at_the_specialist_of_b_after_a_retry_of_a(repo, capsys, scripted, run_id)
    healthy(scripted, "ACCEPT", "ACCEPT", "ACCEPT", "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "d")

    assert code == 0, err
    assert err == ""
    assert [ticket_of(call) for call in scripted.calls] == ["c", "e"]
    assert [ticket_of(call) for call in scripted.verifier_calls] == ["b", "c", "d", "e"]
    assert {"a.txt", "b.txt", "c.txt", "d.txt", "e.txt"} <= set(tree(repo, "run/demo"))
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["result"], end["failed"], end["skipped"]) == ("run-end", "built", [], [])
    assert sorted(end["built"]) == ["a", "b", "c", "d", "e"]


def test_a_resume_leaves_skipped_a_ticket_whose_blocker_still_failed_and_gives_the_reason_of_that_blocker(
    tmp_path, capsys, scripted
):
    # Ticket b waits on a, c waits on b and on d, and f waits on d. A, d and e fail, so the first run skips c because
    # of b and d, and f because of d. A retry of a pauses at b. That resume already records the new skip of c, because
    # of d only, before its first step, as its dry run says (#298). The retry of e builds b, and c stays skipped because
    # of d (#245). The reasons of c and f are the same as in the journal, so that resume writes no new `skip`.
    scripted.files_by_ticket["e"] = {"e.txt": "e\n"}
    end_with_no_verdict(scripted, scoped_only=False)
    tickets = ticket("a") + ticket("b", ("a",)) + ticket("c", ("b", "d")) + ticket("d") + ticket("e") + ticket("f", ("d",))
    repo = make_repo(tmp_path, HEAD + tickets)
    code, run_id = first_run(repo, capsys)
    assert code == 1
    (first,) = [e for e in read_journal_file(repo, run_id) if e["event"] == "skip" and e["ticket"] == "c"]
    assert first["reason"] == "blocked by b, which was skipped; d, which failed"
    pause_at_the_specialist_of_b_after_a_retry_of_a(repo, capsys, scripted, run_id)
    healthy(scripted, "ACCEPT", "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "e")

    assert code == 1 and "ticket c: skipped: blocked by d, which failed\n" in err
    assert "ticket f: skipped: blocked by d, which failed\n" in err
    assert scripted.calls == [] and [ticket_of(call) for call in scripted.verifier_calls] == ["b", "e"]
    events = read_journal_file(repo, run_id)
    resumed = events[[index for index, event in enumerate(events) if event["event"] == "resume"][-1] :]
    assert [e for e in resumed if e["event"] == "skip"] == []
    newest = [e for e in events if e["event"] == "skip" and e["ticket"] == "c"][-1]
    assert (newest["blockers"], newest["reason"]) == (["d"], "blocked by d, which failed")
    end = events[-1]
    assert (end["event"], end["result"], end["failed"], end["skipped"]) == ("run-end", "failed", ["d"], ["c", "f"])
    assert sorted(end["built"]) == ["a", "b", "e"]


def test_a_ticket_that_a_resume_runs_again_and_then_skips_again_with_its_old_reason_shows_as_skipped_after_the_resume(
    tmp_path, capsys, scripted
):
    # Ticket b waits on a, c on b, and d on c. A and x fail, so the first run skips b, c and d. A retry of a builds a and
    # pauses in the specialist of b. The retry of x takes c and d out of the skipped tickets, but the step of b fails.
    # The engine skips c and d again. The new reason of d is the same as its old reason: the resume must still write a
    # `skip` of d after its `unskip`, or the status shows d as pending after the run ended (#298).
    scripted.files_by_ticket["x"] = {"x.txt": "x\n"}
    end_with_no_verdict(scripted, scoped_only=False)
    tickets = ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",)) + ticket("d", ("c",)) + ticket("x")
    repo = make_repo(tmp_path, HEAD + tickets)
    code, run_id = first_run(repo, capsys)
    assert code == 1
    (first,) = [e for e in read_journal_file(repo, run_id) if e["event"] == "skip" and e["ticket"] == "d"]
    assert first["reason"] == "blocked by c, which was skipped"
    pause_at_the_specialist_of_b_after_a_retry_of_a(repo, capsys, scripted, run_id)
    healthy(scripted, "ACCEPT")
    end_with_no_verdict_of(scripted, "b")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "x")

    assert code == 1
    assert "ticket d: skipped: blocked by c, which was skipped\n" in err
    events = read_journal_file(repo, run_id)
    resumed = events[[index for index, event in enumerate(events) if event["event"] == "resume"][-1] :]
    assert [(e["event"], e["ticket"]) for e in resumed if e["event"] in ("unskip", "skip")] == [
        ("unskip", "c"), ("unskip", "d"), ("skip", "c"), ("skip", "d")
    ]
    end = events[-1]
    assert (end["event"], end["result"], end["failed"], end["skipped"]) == ("run-end", "failed", ["b"], ["c", "d"])
    snapshot = status_json(repo, capsys, run_id)
    assert {t["id"]: t["state"] for t in snapshot["tickets"]} == {
        "a": "built", "b": "failed", "c": "skipped", "d": "skipped", "x": "built"
    }
    row = status_row("\n".join(status_lines(repo, capsys, run_id)), "d")
    assert row[2].split()[-1] == "skipped" and row[-1] == "blocked by c, which was skipped"


def end_with_no_verdict_of(scripted, ticket_id):
    """The harness ends the verifier of `ticket_id` at its time limit, with no report. Each other verifier gives its verdict."""

    def time_limit(request):
        killed = ticket_of(request) == ticket_id
        scripted.write_verifier_report = not killed
        scripted.verifier_end_state, scripted.verifier_exit_status = ("failed", -9) if killed else ("finished", 0)

    scripted.during_verifier = time_limit


def look_during_the_verifier_of(repo, capsys, scripted, run_id, ticket_id):
    """While the verifier of `ticket_id` works, read `delegate status --json` and the text of `delegate status`. Return the list
    that gets the two, once for each verifier of `ticket_id`."""
    seen: list[tuple[dict, list[str]]] = []

    def look(request):
        if ticket_of(request) == ticket_id:
            capsys.readouterr()  # the output of the run so far is not the output of `delegate status`
            seen.append((status_json(repo, capsys, run_id), status_lines(repo, capsys, run_id)))

    scripted.during_verifier = look
    return seen


def test_status_during_an_assure_retry_shows_the_dependants_that_the_retry_checks_again_as_pending_and_not_ended(
    tmp_path, capsys, scripted
):
    # Ticket b waits on a, and c waits on b. A fails, so the first run skips b and c. The retry of a takes b and c out of
    # the skipped tickets (#298). While the verifier of a works, the step of b has not started, and no skip applies to b
    # or c: the status shows them as pending, with no skip reason, and the progress line does not count them as ended.
    end_with_no_verdict(scripted, scoped_only=False)
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",)))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    healthy(scripted, "ACCEPT", "ACCEPT", "ACCEPT")
    seen = look_during_the_verifier_of(repo, capsys, scripted, run_id, "a")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, ""), err
    ((snapshot, lines),) = seen
    assert [(t["id"], t["state"]) for t in snapshot["tickets"]] == [("a", "running"), ("b", "pending"), ("c", "pending")]
    assert snapshot["tickets_ended"] == 0
    assert "0 of 3 ended" in "\n".join(lines)
    assert status_row("\n".join(lines), "b")[2].split()[-1] == "pending"
    assert "blocked by" not in "\n".join(lines)


def test_status_during_an_assure_retry_shows_a_dependant_with_another_failed_blocker_as_skipped_with_the_reason_of_that_blocker(
    tmp_path, capsys, scripted
):
    # Ticket d waits on a and on x. A and x fail, so the first run skips b and d. The retry of a builds a, but x still
    # failed: d stays skipped, and its reason names only x. B runs again.
    end_with_no_verdict(scripted, scoped_only=False)
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("x") + ticket("b", ("a",)) + ticket("d", ("a", "x")))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    (first,) = [e for e in read_journal_file(repo, run_id) if e["event"] == "skip" and e["ticket"] == "d"]
    assert first["reason"] == "blocked by a, which failed; x, which failed"
    healthy(scripted, "ACCEPT", "ACCEPT")
    seen = look_during_the_verifier_of(repo, capsys, scripted, run_id, "a")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert code == 1 and "ticket d: skipped: blocked by x, which failed\n" in err
    ((snapshot, lines),) = seen
    states = {t["id"]: t["state"] for t in snapshot["tickets"]}
    assert states == {"a": "running", "x": "failed", "b": "pending", "d": "skipped"}
    assert snapshot["tickets_ended"] == 2
    row = status_row("\n".join(lines), "d")
    assert row[2].split()[-1] == "skipped" and row[-1] == "blocked by x, which failed"
    # The journal holds one new `skip` of d, with the blocker of now.
    events = read_journal_file(repo, run_id)
    resumed = events[[index for index, event in enumerate(events) if event["event"] == "resume"][-1] :]
    assert [(e["ticket"], e["blockers"], e["reason"]) for e in resumed if e["event"] == "skip"] == [
        ("d", ["x"], "blocked by x, which failed")
    ]


def test_the_journal_of_a_resume_names_each_ticket_that_runs_again_as_the_dry_run_of_the_resume_does(tmp_path, capsys, scripted):
    # B waits on a, c on b, and d on a and x. A and x fail, so the first run skips b, c and d. The dry run of the retry of
    # a says what the resume does with b, c and d. The journal of the resume then names b and c, with the same words.
    end_with_no_verdict(scripted, scoped_only=False)
    tickets = ticket("a") + ticket("x") + ticket("b", ("a",)) + ticket("c", ("b",)) + ticket("d", ("a", "x"))
    repo = make_repo(tmp_path, HEAD + tickets)
    code, run_id = first_run(repo, capsys)
    assert code == 1
    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--dry-run")
    assert (code, err) == (0, "")
    section = skipped_section(out)
    assert section == [
        "  ticket b: runs again if a is built earlier in this resume",
        "  ticket c: runs again if b is built earlier in this resume",
        "  ticket d: stays skipped: blocked by x, which failed",
    ]
    healthy(scripted, "ACCEPT", "ACCEPT", "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert code == 1, err
    events = read_journal_file(repo, run_id)
    resumed = events[[index for index, event in enumerate(events) if event["event"] == "resume"][-1] :]
    unskipped = [f"  ticket {e['ticket']}: {e['outlook']}" for e in resumed if e["event"] == "unskip"]
    assert unskipped == [line for line in section if ": runs again" in line]
    # The record comes before the first step of the resume.
    names = event_names(resumed)
    assert names.index("unskip") < names.index("retry")


SKIPPED_SECTION = "skipped tickets:"


def a_retry_of_a_that_paused_before_b(tmp_path, capsys, scripted):
    """A run that a retry of a built a, and that paused before the step of b. Return the repo and the run id.

    Ticket b waits on a, c on b, and e on d. Ticket x waits on nothing. The verifiers of a and d end with no verdict, so a
    and d fail, and the skip rule skips b, c and e. A pause during the specialist of x stops the first run with no
    `run-end`. A retry of a builds a, and a pause during its verifier stops the run before the step of b. No journal
    event records that b and c are ready now.
    """
    for t in "ex":
        scripted.files_by_ticket[t] = {f"{t}.txt": f"{t}\n"}
    tickets = ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",)) + ticket("d") + ticket("e", ("d",)) + ticket("x")
    repo = make_repo(tmp_path, HEAD + tickets)
    end_with_no_verdict(scripted, scoped_only=False)
    pause_during_the_specialist_of(repo, capsys, scripted, "x")
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == RUN_PAUSED, err
    run_id = run_id_of(repo)
    assert [e["ticket"] for e in read_journal_file(repo, run_id) if e["event"] == "skip"] == ["b", "c", "e"]
    healthy(scripted, "ACCEPT")
    scripted.on_specialist = None

    def pause_at_the_verifier_of_a(request):
        if ticket_of(request) == "a":
            assert pause(repo, capsys, run_id)[0] == 0

    scripted.during_verifier = pause_at_the_verifier_of_a
    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")
    assert code == RUN_PAUSED, err
    assert scripted.calls == [] and [ticket_of(call) for call in scripted.verifier_calls] == ["a"]
    assert read_journal_file(repo, run_id)[-1]["event"] == "paused"
    scripted.during_verifier = None
    return repo, run_id


def skipped_section(out: str) -> list[str]:
    """The lines of the section on the skipped tickets that the dry run of a resume prints, with no header."""
    lines = out.splitlines()
    assert SKIPPED_SECTION in lines, out
    return [line for line in lines[lines.index(SKIPPED_SECTION) + 1 :] if line.startswith("  ")]


def test_the_dry_run_of_a_resume_says_that_a_ticket_runs_again_when_a_retry_built_its_blocker_and_stopped_before_it(
    tmp_path, capsys, scripted
):
    # The journal still holds the `skip` of b from the first run. The resume applies the skip rule again (#245), so b
    # runs. The dry run says it before the resume (#260), and the resume then does what the dry run said.
    repo, run_id = a_retry_of_a_that_paused_before_b(tmp_path, capsys, scripted)
    before, verifiers = journal_path(repo, run_id).read_bytes(), len(scripted.verifier_calls)

    code, out, err = resume_main(repo, capsys, run_id, "--dry-run")

    assert (code, err) == (0, "")
    assert "  ticket b: runs again: each blocker is built" in skipped_section(out)
    assert journal_path(repo, run_id).read_bytes() == before
    assert scripted.calls == [] and len(scripted.verifier_calls) == verifiers
    healthy(scripted, "ACCEPT", "ACCEPT", "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1 and "ticket e: skipped: blocked by d, which failed\n" in err
    assert [ticket_of(call) for call in scripted.calls] == ["b", "c"]
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["failed"], end["skipped"]) == ("run-end", ["d"], ["e"])
    assert sorted(end["built"]) == ["a", "b", "c", "x"]


def test_the_dry_run_of_a_resume_says_that_a_ticket_whose_blocker_still_failed_stays_skipped_and_names_the_blocker(
    tmp_path, capsys, scripted
):
    repo, run_id = a_retry_of_a_that_paused_before_b(tmp_path, capsys, scripted)

    code, out, err = resume_main(repo, capsys, run_id, "--dry-run")

    assert (code, err) == (0, "")
    assert "  ticket e: stays skipped: blocked by d, which failed" in skipped_section(out)


def test_the_dry_run_of_a_resume_says_that_the_dependant_of_a_ticket_that_runs_again_runs_again(tmp_path, capsys, scripted):
    # C waits on b, which is not built yet. B runs earlier in the same resume, so c runs too when b is built.
    repo, run_id = a_retry_of_a_that_paused_before_b(tmp_path, capsys, scripted)

    code, out, err = resume_main(repo, capsys, run_id, "--dry-run")

    assert (code, err) == (0, "")
    assert skipped_section(out) == [
        "  ticket b: runs again: each blocker is built",
        "  ticket c: runs again if b is built earlier in this resume",
        "  ticket e: stays skipped: blocked by d, which failed",
    ]


def test_the_dry_run_of_a_resume_says_that_a_ticket_that_a_crash_left_unreached_stays_skipped_until_a_retry_of_that_step(
    tmp_path, capsys, scripted
):
    # The verifier of a raises, so the step crashes. The engine of an earlier version then ended the run, and b is not
    # reached. A plan change adds c.
    def crash(request):
        raise RuntimeError("the harness broke")

    scripted.during_verifier = crash
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))
    code, run_id = first_run(repo, capsys)
    as_an_earlier_version_ended_the_crash(repo, run_id, ["b"])
    (repo / "workflow.toml").write_text(HEAD + ticket("a") + ticket("b") + ticket("c"))

    code, out, err = resume_main(repo, capsys, run_id, "--dry-run")

    assert (code, err) == (0, "")
    assert skipped_section(out) == [
        "  ticket b: stays skipped until a retry of the step that crashed: the run ended after ticket a crashed"
    ]

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--dry-run")

    assert (code, err) == (0, "")
    assert skipped_section(out) == ["  ticket b: runs again"]


def test_a_crash_in_the_step_of_a_retry_names_the_retry_again_and_a_second_retry_builds_the_step(tmp_path, capsys, scripted):
    # The step keeps its old `step-end` failed after the crash, so a plain resume would not go on with it (#281).
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    end_with_no_verdict(scripted, scoped_only=False)
    code, run_id = first_run(repo, capsys)
    assert code == 1

    def crash(request):
        raise RuntimeError("the harness broke")

    scripted.during_verifier = crash
    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert code == 4
    crashed = read_journal_file(repo, run_id)[-1]
    assert (crashed["event"], crashed["ticket"], crashed["phase"], crashed["retry"]) == ("crashed", "a", "verifier", True)
    assert f"crashed at the verifier of ticket a; go on with delegate run --resume {run_id} --retry a" in out
    assert delegate_status_note(repo, capsys).endswith(f"; go on with delegate run --resume {run_id} --retry a")
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["result"], end["built"], end["failed"]) == ("run-end", "built", ["a"], [])


def delegate_status_note(repo, capsys):
    """The note of `delegate status --json` for the newest run of `repo`."""
    from delegate.cli import delegate

    assert delegate.main(["status", "--repo", str(repo), "--json"]) == 0
    return json.loads(capsys.readouterr().out)["note"]


def test_an_assure_retry_of_a_step_that_crashed_builds_the_tickets_that_the_run_did_not_reach(tmp_path, capsys, scripted):
    # The verifier of a raises, so the step crashes. The engine of an earlier version then ended the run, and b, which
    # does not wait on a, is not reached. A retry of that journal still goes on as before (#281).
    def crash(request):
        raise RuntimeError("the harness broke")

    scripted.during_verifier = crash
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))
    code, run_id = first_run(repo, capsys)
    as_an_earlier_version_ended_the_crash(repo, run_id, ["b"])
    (skip,) = [e for e in read_journal_file(repo, run_id) if e["event"] == "skip"]
    assert (skip["ticket"], skip["reason"]) == ("b", "the run ended after ticket a crashed")
    healthy(scripted, "ACCEPT", "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    assert [ticket_of(call) for call in scripted.calls] == ["b"]
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["result"], end["built"], end["failed"], end["skipped"]) == ("run-end", "built", ["a", "b"], [], [])


def test_a_run_that_ends_with_a_skipped_ticket_and_no_failed_step_exits_one(tmp_path, capsys, scripted):
    # The journal of a retry by version 1.7.1: the step of a crashed, so the run did not reach b. The retry built a, b
    # stayed skipped, and the run ended `built`. A resume with a plan change goes on with that run: it builds the new
    # ticket c, and b stays skipped. The skip rule did not skip b, so a resume does not apply the rule to b again (#245).
    def crash(request):
        raise RuntimeError("the harness broke")

    scripted.during_verifier = crash
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))
    code, run_id = first_run(repo, capsys)
    as_an_earlier_version_ended_the_crash(repo, run_id, ["b"])
    append_events(
        journal_path(repo, run_id),
        ("resume", {"run_id": run_id, "built": [], "failed": ["a"], "skipped": ["b"], "open": None}),
        ("retry", {"ticket": "a", "phase": "verifier", "round": 0, "reason": "the verifier ended failed with exit status -9"}),
        ("step-end", {"ticket": "a", "state": "built", "reason": None}),
        ("run-end", {"result": "built", "built": ["a"], "failed": [], "skipped": ["b"]}),
    )
    (repo / "workflow.toml").write_text(HEAD + ticket("a") + ticket("b") + ticket("c"))
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


# #284: in `assure` mode the specialist reports partial or blocked for a cause that is not a hotspot: for example, the
# permission mode of the host refused the edit of a file. The retry continues the specialist in the worktree of the step.
REFUSED_EDIT = "the permission mode of the host refused the edit of gradle/wrapper/gradle-wrapper.properties"


def specialist_stops(tmp_path, capsys, scripted, status, files):
    """An `assure` run: the specialist commits `files` (no commit when empty), and reports `status` with `REFUSED_EDIT`.

    Return the repository, the run id, the stdout of the run, and the commits of the branch of the ticket after the run.
    """
    scripted.files_by_ticket["a"] = files
    scripted.report_text = lambda request, head: json.dumps(
        valid_report("a", "run/demo-a", head, status=status, blocked_reason=REFUSED_EDIT)
    )
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
    assert code == 1 and len(scripted.calls) == 1 and scripted.verifier_calls == []
    scripted.report_text = None
    scripted.continuation_files = [{"gradle.txt": "wrapper\n"}]
    healthy(scripted, "ACCEPT")
    return repo, run_id, out, git(repo, "rev-list", "run/demo-a").split()


def test_an_assure_retry_after_a_partial_specialist_report_continues_the_specialist_in_its_worktree_and_builds_the_step(
    tmp_path, capsys, scripted
):
    repo, run_id, out, first = specialist_stops(tmp_path, capsys, scripted, "partial", {"a.txt": "a\n"})
    worktree = state_dir(repo) / "worktrees" / run_id / "a"

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, ""), err
    (continued,) = scripted.continuation_calls
    assert continued.cwd == worktree and scripted.calls == []
    retried = after_retry(read_journal_file(repo, run_id))
    assert {k: retried[0][k] for k in ("ticket", "phase", "round")} == {"ticket": "a", "phase": "specialist", "round": 0}
    # The continuation limit counts from 0 again: the spawn of the retry is no continuation.
    assert "continuation" not in event_names(retried)
    # The commits of the first session stay on the branch, under the commit of the continuation.
    assert git(repo, "merge-base", "--is-ancestor", first[0], "run/demo") == ""
    assert {"a.txt", "gradle.txt"} <= set(tree(repo, "run/demo"))
    assert len(scripted.verifier_calls) == 1
    end = retried[-1]
    assert (end["event"], end["result"], end["built"]) == ("run-end", "built", ["a"])


@pytest.mark.parametrize(("status", "files"), [("partial", {"a.txt": "a\n"}), ("blocked", {})], ids=["partial-with-commit", "blocked-no-commit"])
def test_the_continuation_brief_of_an_assure_retry_says_the_coordinator_retried_and_gives_the_blocked_reason_of_the_report(
    tmp_path, capsys, scripted, status, files
):
    repo, run_id, out, first = specialist_stops(tmp_path, capsys, scripted, status, files)

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, ""), err
    (continued,) = scripted.continuation_calls
    continuation = continued.prompt.split("## Continuation", 1)[1]
    assert REFUSED_EDIT in continuation and f"status {status!r}" in continuation
    assert "the coordinator retried the step" in continuation


def test_the_end_of_an_assure_run_after_a_partial_specialist_report_names_the_plain_retry_and_its_dry_run_gives_the_phase_specialist(
    tmp_path, capsys, scripted
):
    repo, run_id, first_out, first = specialist_stops(tmp_path, capsys, scripted, "partial", {"a.txt": "a\n"})
    short = run_id.rsplit("-", 1)[-1]
    assert [line for line in first_out.splitlines() if line.startswith("retry with ")] == [
        f"retry with delegate run --resume {short} --retry a"
    ]
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--dry-run")

    assert (code, err) == (0, ""), err
    assert "retry a: phase specialist" in out.splitlines()
    assert journal_path(repo, run_id).read_bytes() == before
    assert scripted.calls == [] and scripted.continuation_calls == []


class FastForwardRefused:
    """The real git backend, except that `fast_forward_branch` does not move the branch: another writer moved it first."""

    def __init__(self) -> None:
        self._inner = GitVersionControl()

    def __getattr__(self, name):
        if name == "fast_forward_branch":
            return lambda *args, **kwargs: False
        return getattr(self._inner, name)


def test_an_assure_retry_of_a_step_that_failed_after_its_verifier_accepted_it_is_still_refused(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    tiers = load_tiers()
    workflow = load_workflow(repo / "workflow.toml", tiers, adapters.registered_names())
    result = run_workflow(workflow, repo / "workflow.toml", repo, tiers, scripted, FastForwardRefused(), ShellGateRunner())
    events = read_journal_file(repo, result.run_id)
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["ACCEPT"]
    assert [e["state"] for e in events if e["event"] == "step-end"] == ["failed"]

    refused(repo, capsys, result.run_id, scripted, "a", "its verifier accepted it")


@pytest.mark.parametrize("mode", ["assure", "economy"])
def test_a_retry_of_a_run_that_an_invariant_violation_halted_exits_one_names_the_violation_and_changes_nothing(
    tmp_path, capsys, scripted, mode
):
    # In `assure` mode the verifier of ticket a halts the run, and b is skipped. In `economy` mode the chain verifier
    # runs in the worktree of a, the only ticket of the chain.
    text = HEAD + ticket("a") + ticket("b", ("a",)) if mode == "assure" else economy(ticket("a"))
    repo = make_repo(tmp_path, text)

    def write_in_the_real_worktree(request):
        (run_dir,) = (state_dir(repo) / "worktrees").iterdir()
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
    assert not state_dir(repo).exists()


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


def test_a_retry_after_a_reject_of_the_verifier_of_the_last_round_is_refused_when_red_gates_used_a_round(tmp_path, capsys, scripted):
    # The gates are red after the first fix-up and green after the second; the scoped verifier of round 2 rejects (#277).
    scripted.verdict_sequence, scripted.verdict_findings = ["REJECT", "REJECT"], [FINDING]
    repo = make_repo(tmp_path, gate_workflow("test ! -f fixup-1.txt || test -f fixup-2.txt"))
    code, run_id = first_run(repo, capsys)
    assert code == 1 and len(scripted.fixup_calls) == 2 and len(scripted.verifier_calls) == 2

    refused(repo, capsys, run_id, scripted, "a", "rejected it after the last fix-up round")


def test_an_economy_retry_of_a_step_that_a_later_built_ticket_went_past_exits_one_and_changes_nothing(tmp_path, capsys, scripted):
    # Ticket b does not wait on a, so it starts from the base of the run and is built without the commits of a.
    scripted.failing_tickets = {"a"}
    repo = make_repo(tmp_path, economy(ticket("a"), ticket("b")))
    code, run_id = first_run(repo, capsys)
    assert code == 1

    refused(repo, capsys, run_id, scripted, "a", "ticket 'b'", "built")


def test_an_economy_retry_of_the_tip_after_the_last_chain_reject_gets_a_fresh_verifier_and_new_fix_up_rounds(tmp_path, capsys, scripted):
    # The verifier of the chain rejects in each round, so the chain fails after its last fix-up round (#269).
    scripted.verdict_sequence, scripted.verdict_findings = ["REJECT"], [FINDING]
    repo = make_repo(tmp_path, economy(ticket("a")))
    code, run_id = first_run(repo, capsys)
    assert code == 1 and len(scripted.fixup_calls) == 2
    healthy(scripted, "REJECT", "ACCEPT")
    scripted.fixup_files = {"fixup-of-the-retry.txt": "fix\n"}  # the files of the first run are on the branch already

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    # The two fix-up rounds before the retry do not count: the REJECT of the retry gets a fix-up round, and the run is built.
    assert (code, err) == (0, ""), err
    assert scripted.calls == [] and scripted.continuation_calls == []
    assert len(scripted.fixup_calls) == 1 and len(scripted.verifier_calls) == 2
    events = read_journal_file(repo, run_id)
    assert {k: after_retry(events)[0][k] for k in ("ticket", "phase", "revived")} == {"ticket": "a", "phase": "chain", "revived": []}
    assert (events[-1]["event"], events[-1]["result"], events[-1]["built"]) == ("run-end", "built", ["a"])


def test_the_end_of_an_economy_run_whose_chain_verification_rejected_names_the_plain_retry_of_the_tip_once(tmp_path, capsys, scripted):
    # The verifier of the chain a, b rejects in each round with a finding that names no ticket, so the chain fails a and b.
    scripted.verdict_sequence, scripted.verdict_findings = ["REJECT"], [FINDING]
    repo = make_repo(tmp_path, economy(ticket("a"), ticket("b")))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 1
    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]

    retries = [line for line in out.splitlines() if line.startswith("retry with ")]

    # No report names a hotspot, so the retry of the tip needs no commit of the coordinator (#269).
    short = run_id.rsplit("-", 1)[-1]
    assert retries == [f"retry with delegate run --resume {short} --retry b"]
    argv = retries[0].removeprefix("retry with delegate run ").split()
    code, out, err = run_main(capsys, *argv, "--repo", str(repo), "--dry-run")
    assert (code, err) == (0, "")
    assert "retry b: phase chain, revives a" in out.splitlines()


# The chain gate of `flaky_chain` is red while this file exists in the directory of the test.
FLAKY = "flaky-gate"


def flaky_chain(tmp_path, capsys, scripted):
    """Run an economy chain a, b whose chain gate is red in the first run only. Return the repository and the run id.

    The fix-up specialist changes nothing and reports partial, as in the run of #269: the gate is flaky.
    """
    (tmp_path / FLAKY).write_text("the chain gate is red while this file exists\n")
    scripted.fixup_files, scripted.fixup_commit = {}, False
    scripted.fixup_report_text = lambda request, head: json.dumps(
        valid_report("b", "run/demo-b", head, status="partial", blocked_reason="the gates are green in the worktree; no change is necessary")
    )
    workflow = economy(ticket("a"), ticket("b")).replace('gates = ["ls *.txt"]', f'gates = ["test ! -f {tmp_path / FLAKY}"]')
    repo = make_repo(tmp_path, workflow)
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 1
    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
    # The report says partial, but it names no hotspot: the retry line of the tip has no commit of the coordinator.
    short = run_id.rsplit("-", 1)[-1]
    assert [line for line in out.splitlines() if line.startswith("retry with ")] == [
        f"retry with delegate run --resume {short} --retry b"
    ]
    first = read_journal_file(repo, run_id)
    assert first[-1]["event"] == "run-end" and sorted(first[-1]["failed"]) == ["a", "b"]
    (tmp_path / FLAKY).unlink()
    healthy(scripted, "ACCEPT")
    return repo, run_id


def test_an_economy_retry_of_the_tip_with_no_commit_runs_the_chain_gates_again_and_a_fresh_verifier_after_a_flaky_gate(
    tmp_path, capsys, scripted
):
    repo, run_id = flaky_chain(tmp_path, capsys, scripted)
    tip = git(repo, "rev-parse", "run/demo-b").strip()

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b")

    assert (code, err) == (0, "")
    events = read_journal_file(repo, run_id)
    retried = after_retry(events)
    assert [(e["phase"], e["commit"], e["green"]) for e in retried if e["event"] == "gate-result"] == [("chain", tip, True)]
    assert len(scripted.verifier_calls) == 1
    assert git(repo, "rev-parse", "run/demo").strip() == tip
    assert {"a.txt", "b.txt"} <= set(tree(repo, "run/demo"))
    end = events[-1]
    assert (end["event"], end["result"], sorted(end["built"]), end["failed"]) == ("run-end", "built", ["a", "b"], [])


def test_an_economy_retry_of_the_tip_with_no_commit_records_the_phase_chain_and_the_revived_tickets_and_starts_no_specialist(
    tmp_path, capsys, scripted
):
    repo, run_id = flaky_chain(tmp_path, capsys, scripted)

    resume_main(repo, capsys, run_id, "--retry", "b")

    retried = after_retry(read_journal_file(repo, run_id))
    assert {k: retried[0][k] for k in ("ticket", "phase", "revived")} == {"ticket": "b", "phase": "chain", "revived": ["a"]}
    assert "coordinator_commit" not in retried[0]
    assert scripted.calls == [] and scripted.continuation_calls == [] and scripted.fixup_calls == []
    assert [e["role"] for e in retried if e["event"] == "agent-start"] == ["verifier"]


def test_an_economy_retry_with_no_commit_of_a_ticket_of_the_chain_that_is_not_the_tip_is_refused_and_names_the_tip(
    tmp_path, capsys, scripted
):
    repo, run_id = flaky_chain(tmp_path, capsys, scripted)

    refused(repo, capsys, run_id, scripted, "a", "ticket 'b' is at the tip of the chain", "--retry b")


def test_an_economy_retry_of_the_tip_with_no_commit_is_refused_when_a_chain_fix_up_changed_a_hotspot(tmp_path, capsys, scripted):
    # The chain gate is red, and the fix-up specialist changes the hotspot LICENSE, so the chain fails with a hotspot finding.
    scripted.fixup_files = {"LICENSE": "changed by the fix-up\n"}
    workflow = economy(ticket("a"), ticket("b")).replace('gates = ["ls *.txt"]', 'gates = ["test -f no-such-file"]')
    repo = make_repo(tmp_path, workflow)
    code, run_id = first_run(repo, capsys)
    assert code == 1 and "hotspot-finding" in event_names(read_journal_file(repo, run_id))

    refused(repo, capsys, run_id, scripted, "b", "hotspot finding: LICENSE")


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
