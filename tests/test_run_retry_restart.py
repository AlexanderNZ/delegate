"""`delegate run --resume RUN_ID --retry TICKET` with a new text for the failed ticket starts that ticket again from its base (#204).

The retry makes the branch and the worktree of the ticket again from the base of the step, and spawns a new
specialist with the new text, in both modes. The old commits are not on the new branch: the engine keeps them on
the branch `<branch>-failed-<n>`, and `delegate clean` removes it. A retry with the same text goes on as before.

Each case calls `delegate.main` against a real temporary git repository, with the scripted adapter in the place of
the harness.
"""

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, git, make_repo, read_journal_file, resume_main, run_main, state_dir, ticket_of
from .test_run_multi import HEAD, ticket
from .test_run_retry_text import NEW_B, OLD_B, RULING, failed_run, workflow_of, write


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abc"})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def commits(repo, branch: str) -> list[str]:
    return git(repo, "rev-list", branch).split()


def branches(repo) -> list[str]:
    return git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").split()


def economy_workflow(b_text: str = OLD_B) -> str:
    return HEAD.replace('mode = "assure"', 'mode = "economy"') + ticket("a") + ticket("b").replace(OLD_B, b_text) + ticket("c", ("b",))


def failed_economy_run(tmp_path, capsys, scripted):
    """An `economy` run whose ticket a is built, whose ticket b failed in its specialist, and whose ticket c was skipped."""
    scripted.failing_tickets = {"b"}
    repo = make_repo(tmp_path, economy_workflow())
    code, _, _ = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 1
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    scripted.failing_tickets = set()
    for calls in (scripted.calls, scripted.continuation_calls, scripted.fixup_calls, scripted.verifier_calls):
        calls.clear()
    return repo, run_dir.name


def test_an_assure_retry_with_a_new_text_spawns_a_new_specialist_on_a_new_branch_that_holds_no_old_commit(tmp_path, capsys, scripted):
    repo, run_id = failed_run(tmp_path, capsys, scripted)
    old_commits = commits(repo, "run/demo-b")
    old_tip = old_commits[0]
    scripted.files_by_ticket["b"] = {"b.txt": "b built again\n"}
    write(repo, workflow_of(NEW_B))

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b", "--reviewed", "c")

    assert (code, err) == (0, "")
    (spawned,) = [call for call in scripted.calls if ticket_of(call) == "b"]
    assert RULING in spawned.prompt
    assert RULING in [call for call in scripted.verifier_calls if ticket_of(call) == "b"][0].prompt
    # The new branch holds none of the old commits of the step. The engine kept them under another name.
    assert old_tip not in commits(repo, "run/demo-b")
    assert git(repo, "rev-parse", "run/demo-b-failed-1").strip() == old_tip
    events = read_journal_file(repo, run_id)
    (retry,) = [e for e in events if e["event"] == "retry"]
    assert (retry["ticket"], retry["phase"], retry["round"], retry["restart"], retry["failed_branch"]) == (
        "b", "specialist", 0, True, "run/demo-b-failed-1",
    )
    starts = [e for e in events if e["event"] == "step-start" and e["ticket"] == "b"]
    assert [e["base_commit"] for e in starts][1] == git(repo, "rev-parse", "main").strip()
    assert events.index(retry) < events.index(starts[1])
    assert [e["state"] for e in events if e["event"] == "step-end" and e["ticket"] == "b"] == ["failed", "built"]
    assert "b built again" in git(repo, "show", "run/demo:b.txt")


def test_an_economy_retry_with_a_new_text_starts_the_ticket_again_and_the_next_ticket_starts_from_the_new_tip(tmp_path, capsys, scripted):
    repo, run_id = failed_economy_run(tmp_path, capsys, scripted)
    old_tip = git(repo, "rev-parse", "run/demo-b").strip()
    tip_of_a = git(repo, "rev-parse", "run/demo-a").strip()
    scripted.files_by_ticket["b"] = {"b.txt": "b built again\n"}
    write(repo, economy_workflow(NEW_B))

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b", "--reviewed", "c")

    assert (code, err) == (0, "")
    (spawned,) = [call for call in scripted.calls if ticket_of(call) == "b"]
    assert RULING in spawned.prompt
    assert scripted.continuation_calls == []
    assert old_tip not in commits(repo, "run/demo-b")
    assert git(repo, "rev-parse", "run/demo-b-failed-1").strip() == old_tip
    events = read_journal_file(repo, run_id)
    (retry,) = [e for e in events if e["event"] == "retry"]
    assert retry["restart"] is True and retry["failed_branch"] == "run/demo-b-failed-1"
    starts = {t: [e for e in events if e["event"] == "step-start" and e["ticket"] == t] for t in "abc"}
    assert starts["b"][1]["base_commit"] == tip_of_a
    # The ticket after the retried ticket starts from the new tip, which holds the new work and not the old.
    (start_of_c,) = starts["c"]
    assert start_of_c["base_commit"] == git(repo, "rev-parse", "run/demo-b").strip()
    assert old_tip not in commits(repo, "run/demo-c")
    assert events[-1]["result"] == "built"


def test_a_retry_with_the_same_text_uses_the_old_worktree_and_commits_and_spawns_no_specialist(tmp_path, capsys, scripted):
    repo, run_id = failed_run(tmp_path, capsys, scripted)
    old_tip = git(repo, "rev-parse", "run/demo-b").strip()
    worktree = next(e["worktree"] for e in read_journal_file(repo, run_id) if e["event"] == "step-start" and e["ticket"] == "b")

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b")

    assert (code, err) == (0, "")
    assert [ticket_of(call) for call in scripted.calls] == ["c"]  # the ticket that b blocked, and no specialist for b
    events = read_journal_file(repo, run_id)
    (retry,) = [e for e in events if e["event"] == "retry"]
    assert "restart" not in retry and "failed_branch" not in retry
    assert [e["worktree"] for e in events if e["event"] == "step-start" and e["ticket"] == "b"] == [worktree]
    assert old_tip in commits(repo, "run/demo")
    assert [name for name in branches(repo) if "failed" in name] == []


def test_the_old_branch_stays_when_the_run_ends_built_and_delegate_clean_removes_it(tmp_path, capsys, scripted):
    repo, run_id = failed_run(tmp_path, capsys, scripted)
    old_tip = git(repo, "rev-parse", "run/demo-b").strip()
    scripted.files_by_ticket["b"] = {"b.txt": "b built again\n"}
    write(repo, workflow_of(NEW_B))
    code, _, err = resume_main(repo, capsys, run_id, "--retry", "b", "--reviewed", "c")
    assert (code, err) == (0, "")
    assert events_end(repo, run_id) == "built"
    # The run branch does not hold the old work of b, so the old branch stays until the coordinator has read it.
    assert git(repo, "rev-parse", "run/demo-b-failed-1").strip() == old_tip

    code = delegate.main(["clean", "--ended", "--repo", str(repo)])
    out = capsys.readouterr().out

    assert code == 0 and "run/demo-b-failed-1" in out
    assert "run/demo-b-failed-1" not in branches(repo)
    assert "run/demo-b" not in branches(repo)  # the other ticket branches go too, as they did before
    assert "run/demo" in branches(repo)


def events_end(repo, run_id) -> str:
    return str(read_journal_file(repo, run_id)[-1]["result"])


def test_a_second_restart_keeps_the_first_old_branch_and_names_the_next_one_with_the_next_number(tmp_path, capsys, scripted):
    repo, run_id = failed_run(tmp_path, capsys, scripted)
    first_tip = git(repo, "rev-parse", "run/demo-b").strip()
    scripted.files_by_ticket["b"] = {"b.txt": "b built again\n"}
    scripted.failing_tickets = {"b"}
    write(repo, workflow_of(NEW_B))
    code, _, _ = resume_main(repo, capsys, run_id, "--retry", "b", "--reviewed", "c")
    assert code == 1
    second_tip = git(repo, "rev-parse", "run/demo-b").strip()
    scripted.failing_tickets = set()
    scripted.files_by_ticket["b"] = {"b.txt": "b built a third time\n"}
    write(repo, workflow_of(NEW_B + " Again."))

    code, _, err = resume_main(repo, capsys, run_id, "--retry", "b", "--reviewed", "c")

    assert (code, err) == (0, "")
    assert git(repo, "rev-parse", "run/demo-b-failed-1").strip() == first_tip
    assert git(repo, "rev-parse", "run/demo-b-failed-2").strip() == second_tip


def test_the_fixup_rounds_of_the_old_step_do_not_count_against_the_limit_of_the_restarted_step(tmp_path, capsys, scripted):
    # The verifier of b rejects each time: two fix-up rounds, then the step fails after the last round.
    scripted.verdict_sequence = ["ACCEPT", "REJECT", "REJECT", "REJECT"]
    repo = make_repo(tmp_path, workflow_of())
    code, _, _ = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 1
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    run_id = run_dir.name
    assert len(scripted.fixup_calls) == 2
    for calls in (scripted.calls, scripted.continuation_calls, scripted.fixup_calls, scripted.verifier_calls):
        calls.clear()
    # Now the verifier rejects once and then accepts. The restarted step needs one fix-up round, so it needs the whole limit again.
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.files_by_ticket["b"] = {"b.txt": "b built again\n"}
    write(repo, workflow_of(NEW_B))

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b", "--reviewed", "c")

    assert (code, err) == (0, "")
    assert [ticket_of(call) for call in scripted.calls] == ["b", "c"]
    assert len(scripted.fixup_calls) == 1
    events = read_journal_file(repo, run_id)
    assert [e["state"] for e in events if e["event"] == "step-end" and e["ticket"] == "b"] == ["failed", "built"]
    assert events[-1]["result"] == "built"


def test_status_names_the_branch_that_keeps_the_old_work_of_a_restarted_ticket(tmp_path, capsys, scripted):
    repo, run_id = failed_run(tmp_path, capsys, scripted)
    scripted.files_by_ticket["b"] = {"b.txt": "b built again\n"}
    write(repo, workflow_of(NEW_B))
    code, _, err = resume_main(repo, capsys, run_id, "--retry", "b", "--reviewed", "c")
    assert (code, err) == (0, "")

    code = delegate.main(["status", run_id, "--repo", str(repo)])
    out = capsys.readouterr().out

    assert code == 0
    (line,) = [line for line in out.splitlines() if line.startswith("kept: ")]
    assert "run/demo-b-failed-1" in line and "ticket b" in line and "delegate clean" in line
