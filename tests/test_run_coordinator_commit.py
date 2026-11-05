"""`delegate run --resume RUN_ID --retry TICKET --coordinator-commit SHA` (#225).

A hotspot path is for the coordinator only, so a specialist cannot fix gates that are red because a hotspot needs a
change: for example a committed render of the agent pair that the code no longer gives. The coordinator commits the
change on top of the branch of the failed ticket, and names the commit to the retry. The hotspot guard does not count
the paths of that commit, and still counts each other commit of the step. The gates and the verifier see the commit as
part of the branch, and the verifier brief names it. The engine refuses a commit that is not on top of the branch, and
a commit that changes a path outside the hotspots.

In `assure` mode the retry also goes on with a step whose specialist, or whose fix-up specialist of a round, reported
partial or blocked because the work needs a hotspot (#264).

Each case calls `delegate.main` against a real temporary git repository, with the scripted adapter in the place of the
harness. The coordinator commits with git in the worktree of the ticket, as a person does.
"""

import json

import pytest

from .support import (
    commit_head, event_names, git, journal_path, make_repo, read_journal, read_journal_file, resume_main, run_main, state_dir,
    tree, valid_report,
)
from .test_run_multi import HEAD, ticket

# The gate is red until the hotspot LICENSE holds the render that the code gives.
RENDER_GATE = 'gates = ["grep -qx rendered LICENSE"]'
HOTSPOTS = 'hotspots = ["LICENSE", "NOTICE"]'
COORDINATOR_SECTION = "## Commit of the coordinator"


def workflow(*tickets: str, mode: str = "assure") -> str:
    head = HEAD.replace('gates = ["ls *.txt"]', RENDER_GATE).replace('hotspots = ["LICENSE", ".github/workflows/*"]', HOTSPOTS)
    return head.replace('mode = "assure"', f'mode = "{mode}"') + "".join(tickets)


def first_run(repo, capsys):
    """Run `workflow.toml` of `repo` to its end. Return the exit code and the run id."""
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    return code, next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]


def worktree_of(repo, run_id, ticket_id):
    return state_dir(repo) / "worktrees" / run_id / ticket_id


def coordinator_commits(worktree, files: dict[str, str]) -> str:
    """Write `files` in the worktree of a ticket, commit them as the coordinator does, and return the commit."""
    for name, content in files.items():
        (worktree / name).write_text(content)
    git(worktree, "add", "-A")
    return commit_head(worktree, "-m", "chore(agents): render the agent pair again")[0]


def healthy(scripted, *verdicts):
    """From now on each verifier ends with a verdict: `verdicts` in turn. Forget the calls of the first run."""
    scripted.verdict_sequence = list(verdicts)
    scripted.fixup_report_text = None
    for calls in (scripted.calls, scripted.continuation_calls, scripted.fixup_calls, scripted.verifier_calls):
        calls.clear()


def after_retry(events):
    return events[event_names(events).index("retry") :]


def section(prompt: str, heading: str) -> str:
    """The text of the section `heading` of a brief, to the next heading of the same level."""
    assert heading in prompt, prompt
    return prompt.split(heading, 1)[1].split("\n## ", 1)[0]


def test_an_assure_retry_with_a_coordinator_commit_of_a_hotspot_runs_the_gates_and_a_verifier_that_sees_the_commit(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path, workflow(ticket("a")))
    code, run_id = first_run(repo, capsys)
    assert code == 1 and len(scripted.continuation_calls) == 2
    render = coordinator_commits(scripted.calls[0].cwd, {"LICENSE": "rendered\n"})
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--coordinator-commit", render)

    assert (code, err) == (0, "")
    assert scripted.calls == [] and scripted.continuation_calls == [] and scripted.fixup_calls == []
    retried = after_retry(read_journal_file(repo, run_id))
    assert {k: retried[0][k] for k in ("ticket", "phase", "coordinator_commit", "coordinator_paths")} == {
        "ticket": "a", "phase": "gates", "coordinator_commit": render, "coordinator_paths": ["LICENSE"],
    }
    assert "hotspot-finding" not in event_names(retried)
    assert [(e["phase"], e["commit"], e["green"]) for e in retried if e["event"] == "gate-result"] == [("build", render, True)]
    (verifier,) = scripted.verifier_calls
    named = section(verifier.prompt, COORDINATOR_SECTION)
    assert f"`{render}`" in named and "`LICENSE`" in named
    assert git(repo, "rev-parse", "run/demo").strip() == render
    assert git(repo, "show", "run/demo:LICENSE") == "rendered\n"
    assert [e["state"] for e in retried if e["event"] == "step-end"] == ["built"]


def test_a_specialist_commit_of_the_step_that_changes_a_hotspot_still_fails_the_step_beside_a_coordinator_commit(
    tmp_path, capsys, scripted
):
    scripted.files_by_ticket["a"] = {"a.txt": "a\n", "NOTICE": "changed by the specialist\n"}
    repo = make_repo(tmp_path, workflow(ticket("a"), mode="economy"))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    render = coordinator_commits(scripted.calls[0].cwd, {"LICENSE": "rendered\n"})
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--coordinator-commit", render)

    assert code == 1 and "Traceback" not in err
    assert "hotspot finding: NOTICE matches the hotspot 'NOTICE'" in err and "LICENSE" not in err
    retried = after_retry(read_journal_file(repo, run_id))
    assert retried[0]["coordinator_commit"] == render
    (finding,) = [e for e in retried if e["event"] == "hotspot-finding"]
    assert finding["matches"] == [{"path": "NOTICE", "pattern": "NOTICE"}]
    assert [e["state"] for e in retried if e["event"] == "step-end"] == ["failed"]
    assert scripted.verifier_calls == [] and "gate-result" not in event_names(retried)


def refused(repo, capsys, run_id, scripted, *argv):
    """Retry with `argv`, check that the command exits 1 and changes nothing, and return its stderr."""
    before = journal_path(repo, run_id).read_bytes()
    tips = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, *argv)

    assert code == 1 and "Traceback" not in err
    assert journal_path(repo, run_id).read_bytes() == before
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads") == tips
    assert scripted.calls == [] and scripted.continuation_calls == [] and scripted.verifier_calls == []
    return err


def test_a_coordinator_commit_that_changes_a_path_outside_the_hotspots_is_refused_and_the_message_names_the_path(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path, workflow(ticket("a")))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    render = coordinator_commits(scripted.calls[0].cwd, {"LICENSE": "rendered\n", "notes.txt": "a note\n"})

    err = refused(repo, capsys, run_id, scripted, "--retry", "a", "--coordinator-commit", render)

    assert "ticket 'a'" in err and render in err
    assert "`notes.txt`" in err and "`LICENSE`" not in err


def test_a_coordinator_commit_that_is_not_the_tip_of_the_branch_of_the_ticket_is_refused(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, workflow(ticket("a")))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    worktree = scripted.calls[0].cwd
    render = coordinator_commits(worktree, {"LICENSE": "rendered\n"})
    coordinator_commits(worktree, {"LICENSE": "rendered again\n"})

    err = refused(repo, capsys, run_id, scripted, "--retry", "a", "--coordinator-commit", render)

    assert "ticket 'a'" in err and render in err and "not on top of the branch run/demo-a" in err


def test_a_coordinator_commit_that_names_no_commit_is_refused(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, workflow(ticket("a")))
    code, run_id = first_run(repo, capsys)
    assert code == 1

    err = refused(repo, capsys, run_id, scripted, "--retry", "a", "--coordinator-commit", "no-such-commit")

    assert "ticket 'a'" in err and "no-such-commit" in err


def test_a_coordinator_commit_with_a_retry_that_starts_the_ticket_again_from_its_base_is_refused(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, workflow(ticket("a")))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    render = coordinator_commits(scripted.calls[0].cwd, {"LICENSE": "rendered\n"})
    workflow_file = repo / "workflow.toml"
    workflow_file.write_text(workflow_file.read_text().replace("Ticket a adds its file.", "Ticket a adds its file, and a test."))

    err = refused(repo, capsys, run_id, scripted, "--retry", "a", "--coordinator-commit", render)

    assert "ticket 'a'" in err and "starts the ticket again from its base" in err


def test_coordinator_commit_without_retry_is_a_usage_error(tmp_path, capsys):
    repo = make_repo(tmp_path, workflow(ticket("a")))

    try:
        code = run_main(capsys, "--resume", "20260101T000000Z-abcd", "--repo", str(repo), "--coordinator-commit", "HEAD")[0]
    except SystemExit as stop:
        code = stop.code

    assert code == 2 and "--coordinator-commit needs --retry" in capsys.readouterr().err
    assert not state_dir(repo).exists()


def test_an_economy_retry_with_a_coordinator_commit_goes_on_with_a_chain_that_its_gates_failed(tmp_path, capsys, scripted):
    # The chain gates are red on the render, and the fix-up specialist reports partial: only the coordinator can render.
    scripted.fixup_report_text = lambda request, head: json.dumps(
        valid_report("b", "run/demo-b", head, status="partial", blocked_reason="LICENSE is a hotspot; the coordinator renders it")
    )
    repo = make_repo(tmp_path, workflow(ticket("a"), ticket("b"), mode="economy"))
    code, run_id = first_run(repo, capsys)
    assert code == 1 and len(scripted.fixup_calls) == 1
    first = read_journal_file(repo, run_id)
    assert first[-1]["event"] == "run-end" and sorted(first[-1]["failed"]) == ["a", "b"]
    render = coordinator_commits(scripted.fixup_calls[0].cwd, {"LICENSE": "rendered\n"})
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b", "--coordinator-commit", render)

    assert (code, err) == (0, "")
    assert scripted.calls == [] and scripted.continuation_calls == [] and scripted.fixup_calls == []
    events = read_journal_file(repo, run_id)
    retried = after_retry(events)
    assert {k: retried[0][k] for k in ("ticket", "phase", "revived", "coordinator_commit", "coordinator_paths")} == {
        "ticket": "b", "phase": "chain", "revived": ["a"], "coordinator_commit": render, "coordinator_paths": ["LICENSE"],
    }
    assert "hotspot-finding" not in event_names(retried)
    assert [(e["phase"], e["commit"], e["green"]) for e in retried if e["event"] == "gate-result"] == [("chain", render, True)]
    (verifier,) = scripted.verifier_calls
    assert f"`{render}`" in section(verifier.prompt, COORDINATOR_SECTION)
    assert git(repo, "rev-parse", "run/demo").strip() == render
    assert {"a.txt", "b.txt", "LICENSE"} <= set(tree(repo, "run/demo"))
    end = events[-1]
    assert (end["event"], end["result"], sorted(end["built"]), end["failed"]) == ("run-end", "built", ["a", "b"], [])


def test_an_economy_chain_retry_with_a_coordinator_commit_names_the_ticket_at_the_tip_of_the_chain(tmp_path, capsys, scripted):
    scripted.fixup_report_text = lambda request, head: json.dumps(
        valid_report("b", "run/demo-b", head, status="partial", blocked_reason="LICENSE is a hotspot")
    )
    repo = make_repo(tmp_path, workflow(ticket("a"), ticket("b"), mode="economy"))
    code, run_id = first_run(repo, capsys)
    assert code == 1
    render = coordinator_commits(worktree_of(repo, run_id, "a"), {"LICENSE": "rendered\n"})

    err = refused(repo, capsys, run_id, scripted, "--retry", "a", "--coordinator-commit", render)

    assert "ticket 'a'" in err and "ticket 'b'" in err and "tip of the chain" in err


def test_a_coordinator_commit_that_the_rebase_writes_again_is_still_not_counted_by_the_next_retry_and_the_brief_names_it(
    tmp_path, capsys, scripted
):
    # Ticket a needs the render; ticket b does not, and is built, so the run branch moves. The gate of the retry of a asks
    # for a pause once, so the run stops after the rebase of a and before its gates. The next retry checks the hotspots
    # again on the rebased branch, where the commit of the coordinator has a new id.
    once = tmp_path / "paused-once"
    gate = (
        "test ! -f a.txt || { grep -qx rendered LICENSE && { test -f "
        f"{once} || {{ touch {once} && touch ../../../runs/$(basename $(dirname $PWD))/pause-request; }}; }}; }}"
    )
    head = workflow().replace(RENDER_GATE, f"gates = [{json.dumps(gate)}]")
    repo = make_repo(tmp_path, head + ticket("a") + ticket("b"))
    code, run_id = first_run(repo, capsys)
    assert code == 1 and "b.txt" in tree(repo, "run/demo") and "a.txt" not in tree(repo, "run/demo")
    render = coordinator_commits(worktree_of(repo, run_id, "a"), {"LICENSE": "rendered\n"})
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--coordinator-commit", render)

    assert code == 3, err
    paused = read_journal_file(repo, run_id)
    (rebase,) = [e for e in after_retry(paused) if e["event"] == "rebase"]
    rebased = rebase["to_commit"]
    assert rebase["from_commit"] == render and rebased != render
    assert paused[-1]["event"] == "paused" and scripted.verifier_calls == []

    # A retry that paused goes on with `--retry` again. The step keeps its commit of the coordinator, so the flag is not needed.
    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, ""), err
    events = read_journal_file(repo, run_id)
    assert "hotspot-finding" not in event_names(events)
    (verifier,) = scripted.verifier_calls
    named = section(verifier.prompt, COORDINATOR_SECTION)
    assert f"`{rebased}`" in named and render not in named
    assert git(repo, "rev-parse", "run/demo").strip() == rebased
    assert {"a.txt", "b.txt", "LICENSE"} <= set(tree(repo, "run/demo"))
    assert (events[-1]["event"], events[-1]["result"], sorted(events[-1]["built"])) == ("run-end", "built", ["a", "b"])


# #264: in `assure` mode the specialist, or the fix-up specialist of a round, reports partial because the work needs a hotspot.
LISTING_GATE = 'gates = ["ls *.txt"]'
NEEDS_RENDER = "LICENSE is a hotspot; the coordinator renders it"


def partial(ticket_id: str):
    """A report function of the scripted adapter: a valid report that says partial, because the work needs a hotspot."""
    return lambda request, head: json.dumps(
        valid_report(ticket_id, f"run/demo-{ticket_id}", head, status="partial", blocked_reason=NEEDS_RENDER)
    )


def fixup_needs_a_hotspot(tmp_path, capsys, scripted):
    """An `assure` run: the gates are green, the verifier rejects, and the fix-up of round 1 reports partial with no commit."""
    scripted.verdict_sequence = ["REJECT"]
    scripted.verdict_findings = ["LICENSE: the render is not current"]
    scripted.fixup_files = {}
    scripted.fixup_report_text = partial("a")
    repo = make_repo(tmp_path, workflow(ticket("a")).replace(RENDER_GATE, LISTING_GATE))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
    assert code == 1 and len(scripted.fixup_calls) == 1 and len(scripted.verifier_calls) == 1
    return repo, run_id, out


def specialist_needs_a_hotspot(tmp_path, capsys, scripted):
    """An `assure` run: the specialist commits its work and reports partial, because the gates need the render of a hotspot."""
    scripted.report_text = partial("a")
    repo = make_repo(tmp_path, workflow(ticket("a")))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
    assert code == 1 and len(scripted.calls) == 1 and scripted.verifier_calls == []
    scripted.report_text = None
    return repo, run_id, out


def test_an_assure_retry_with_a_coordinator_commit_after_a_partial_fixup_runs_the_gates_and_the_scoped_verifier_of_that_round(
    tmp_path, capsys, scripted
):
    repo, run_id, out = fixup_needs_a_hotspot(tmp_path, capsys, scripted)
    worktree = worktree_of(repo, run_id, "a")
    rejected = git(worktree, "rev-parse", "HEAD").strip()
    render = coordinator_commits(worktree, {"LICENSE": "rendered\n"})
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--coordinator-commit", render)

    assert (code, err) == (0, ""), err
    assert scripted.calls == [] and scripted.continuation_calls == [] and scripted.fixup_calls == []
    retried = after_retry(read_journal_file(repo, run_id))
    assert {k: retried[0][k] for k in ("ticket", "phase", "round", "coordinator_commit", "coordinator_paths")} == {
        "ticket": "a", "phase": "gates", "round": 1, "coordinator_commit": render, "coordinator_paths": ["LICENSE"],
    }
    assert "hotspot-finding" not in event_names(retried) and "fixup-start" not in event_names(retried)
    assert [(e["phase"], e["round"], e["commit"], e["green"]) for e in retried if e["event"] == "gate-result"] == [
        ("fixup", 1, render, True)
    ]
    # Not a new round: the scoped verifier of round 1 sees the rejected commit and the findings of round 1.
    (verifier,) = scripted.verifier_calls
    assert verifier.prompt.startswith("## Findings under verification")
    assert "LICENSE: the render is not current" in verifier.prompt and rejected in verifier.prompt
    assert [(e["round"], e["mode"], e["verdict"]) for e in retried if e["event"] == "verdict"] == [(1, "fix-up", "ACCEPT")]
    assert git(repo, "rev-parse", "run/demo").strip() == render
    assert [e["state"] for e in retried if e["event"] == "step-end"] == ["built"]


def test_an_assure_retry_with_a_coordinator_commit_after_a_partial_specialist_report_runs_the_gates_of_round_0(
    tmp_path, capsys, scripted
):
    repo, run_id, out = specialist_needs_a_hotspot(tmp_path, capsys, scripted)
    render = coordinator_commits(worktree_of(repo, run_id, "a"), {"LICENSE": "rendered\n"})
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--coordinator-commit", render)

    assert (code, err) == (0, ""), err
    assert scripted.calls == [] and scripted.continuation_calls == [] and scripted.fixup_calls == []
    retried = after_retry(read_journal_file(repo, run_id))
    assert {k: retried[0][k] for k in ("ticket", "phase", "round", "coordinator_commit")} == {
        "ticket": "a", "phase": "gates", "round": 0, "coordinator_commit": render,
    }
    assert "hotspot-finding" not in event_names(retried)
    assert [(e["phase"], e["round"], e["commit"], e["green"]) for e in retried if e["event"] == "gate-result"] == [
        ("build", 0, render, True)
    ]
    (verifier,) = scripted.verifier_calls
    assert not verifier.prompt.startswith("## Findings under verification")
    assert f"`{render}`" in section(verifier.prompt, COORDINATOR_SECTION)
    assert git(repo, "show", "run/demo:LICENSE") == "rendered\n" and "a.txt" in tree(repo, "run/demo")
    assert [e["state"] for e in retried if e["event"] == "step-end"] == ["built"]


def test_a_partial_specialist_commit_of_a_hotspot_still_fails_the_retry_with_a_coordinator_commit(tmp_path, capsys, scripted):
    scripted.files_by_ticket["a"] = {"a.txt": "a\n", "NOTICE": "changed by the specialist\n"}
    repo, run_id, out = specialist_needs_a_hotspot(tmp_path, capsys, scripted)
    render = coordinator_commits(worktree_of(repo, run_id, "a"), {"LICENSE": "rendered\n"})
    healthy(scripted, "ACCEPT")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--coordinator-commit", render)

    assert code == 1 and "hotspot finding: NOTICE matches the hotspot 'NOTICE'" in err and "LICENSE" not in err
    retried = after_retry(read_journal_file(repo, run_id))
    assert [e["state"] for e in retried if e["event"] == "step-end"] == ["failed"]
    assert scripted.verifier_calls == [] and "gate-result" not in event_names(retried)


@pytest.mark.parametrize("needs_a_hotspot", [fixup_needs_a_hotspot, specialist_needs_a_hotspot], ids=["fix-up", "specialist"])
def test_an_assure_retry_of_a_partial_report_without_a_coordinator_commit_is_refused_and_the_message_names_the_flag(
    tmp_path, capsys, scripted, needs_a_hotspot
):
    repo, run_id, first_out = needs_a_hotspot(tmp_path, capsys, scripted)
    short = run_id.rsplit("-", 1)[-1]
    # The end of the run names the retry that the engine accepts: the retry with a commit of the coordinator.
    assert [line for line in first_out.splitlines() if line.startswith("retry with ")] == [
        f"retry with delegate run --resume {short} --retry a --coordinator-commit <sha>"
    ]

    code, out, dry_err = resume_main(repo, capsys, run_id, "--retry", "a", "--dry-run")
    assert code == 1
    err = refused(repo, capsys, run_id, scripted, "--retry", "a")

    (message,) = [line for line in err.splitlines() if "cannot be retried" in line]
    assert message in dry_err.splitlines()
    assert f"ticket 'a' of run '{run_id}' cannot be retried" in message
    assert NEEDS_RENDER in message and "--retry a --coordinator-commit <sha>" in message

    # With the commit of the coordinator, the dry run accepts the retry.
    render = coordinator_commits(worktree_of(repo, run_id, "a"), {"LICENSE": "rendered\n"})
    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--coordinator-commit", render, "--dry-run")
    assert (code, err) == (0, ""), err
