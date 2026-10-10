"""`delegate run --resume`: rebuild the state from the journal and restart at the first step that is not complete.

A killed run is a real one: a child process runs `delegate run` with a scripted
adapter and SIGKILLs itself at a chosen adapter call. It leaves what a kill
leaves, an open journal, a lock, and a worktree. A resume of such a run needs
`--break-lock`, because the lock belongs to a process that no longer exists. A case that needs another
journal state cuts a finished journal, which is the file a kill at that point
would leave. Each case calls `delegate.main` against a real temporary git
repository.
"""

import json
import shutil

from delegate.cli import delegate

from .support import (
    event_names, finished_run, git, journal_path, kill_a_run, make_repo, read_journal, read_journal_file, resume_main, run_main, ticket_of, tree,
)
from .test_run_multi import HEAD, ticket

THREE = HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c")


def cut_journal_after(repo, run_id, event, ticket_id=None, nth=1):
    """Keep the journal up to the nth matching event. This is the file that a kill right after the event leaves."""
    path = journal_path(repo, run_id)
    kept, seen = [], 0
    for line in path.read_text().splitlines(keepends=True):
        kept.append(line)
        parsed = json.loads(line)
        if parsed["event"] == event and (ticket_id is None or parsed.get("ticket") == ticket_id):
            seen += 1
            if seen == nth:
                break
    else:
        raise AssertionError(f"fewer than {nth} {event} events")
    path.write_text("".join(kept))


def test_a_stopped_run_builds_the_remaining_tickets_on_resume_and_an_accepted_ticket_gets_no_new_adapter_invocation(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id, _ = kill_a_run(repo, 3)  # killed as the specialist of ticket b starts
    assert tree(repo, "run/demo").count("a.txt") == 1 and "b.txt" not in tree(repo, "run/demo")

    code, out, err = resume_main(repo, capsys, run_id, "--break-lock")

    assert (code, err) == (0, "")
    assert [ticket_of(c) for c in scripted.calls] == ["b", "c"]
    assert [ticket_of(c) for c in scripted.verifier_calls] == ["b", "c"]
    assert {"a.txt", "b.txt", "c.txt"} <= set(tree(repo, "run/demo"))
    events = read_journal_file(repo, run_id)
    run_end = next(e for e in events if e["event"] == "run-end")
    assert (run_end["result"], run_end["built"], run_end["failed"], run_end["skipped"]) == ("built", ["a", "b", "c"], [], [])
    assert [e["ticket"] for e in events if e["event"] == "verdict"] == ["a", "b", "c"]


def test_a_resume_appends_to_the_journal_and_never_rewrites_a_line(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id, _ = kill_a_run(repo, 3)
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = resume_main(repo, capsys, run_id, "--break-lock")

    assert (code, err) == (0, "")
    after = journal_path(repo, run_id).read_bytes()
    assert after.startswith(before) and len(after) > len(before)
    events = read_journal_file(repo, run_id)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert event_names(events).count("run-start") == 1 and event_names(events).count("run-end") == 1
    resumed = next(e for e in events if e["event"] == "resume")
    assert (resumed["run_id"], resumed["built"], resumed["open"]) == (run_id, ["a"], "b")
    assert f"journal {journal_path(repo, run_id)}" in out.splitlines()


def test_a_step_whose_specialist_finished_does_not_spawn_the_specialist_again_and_keeps_its_commit(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))
    run_id, _ = kill_a_run(repo, 2)  # killed as the verifier of ticket a starts, after the specialist reported
    committed = git(repo, "rev-parse", "run/demo-a").strip()

    code, out, err = resume_main(repo, capsys, run_id, "--break-lock")

    assert (code, err) == (0, "")
    assert [ticket_of(c) for c in scripted.calls] == ["b"]
    assert scripted.continuation_calls == [] and scripted.fixup_calls == []
    assert [ticket_of(c) for c in scripted.verifier_calls] == ["a", "b"]
    assert git(repo, "rev-parse", "run/demo-a").strip() == committed
    assert git(repo, "rev-list", "--count", f"main..{committed}").strip() == "1"
    events = read_journal_file(repo, run_id)
    assert [e["ticket"] for e in events if e["event"] == "step-start"] == ["a", "b"]
    assert event_names(events)[-1] == "run-end"


def test_a_step_that_was_stopped_before_any_commit_runs_its_specialist_in_the_same_worktree(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id, _ = kill_a_run(repo, 1)  # killed as the first specialist starts
    started = next(e for e in read_journal_file(repo, run_id) if e["event"] == "step-start")

    code, out, err = resume_main(repo, capsys, run_id, "--break-lock")

    assert (code, err) == (0, "")
    (call,) = scripted.calls
    assert str(call.cwd) == started["worktree"]
    assert "## Continuation" not in call.prompt
    assert "a.txt" in tree(repo, "run/demo")


def test_a_step_whose_worktree_is_gone_gets_it_back_on_the_same_branch(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id, _ = kill_a_run(repo, 1)
    started = next(e for e in read_journal_file(repo, run_id) if e["event"] == "step-start")
    git(repo, "worktree", "remove", "--force", started["worktree"])

    code, out, err = resume_main(repo, capsys, run_id, "--break-lock")

    assert (code, err) == (0, "")
    assert str(scripted.calls[0].cwd) == started["worktree"] and scripted.calls[0].cwd.is_dir()
    assert "a.txt" in tree(repo, "run/demo")


def test_a_step_with_commits_but_no_valid_report_in_the_journal_continues_its_specialist_with_the_commits(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id = finished_run(repo, capsys)
    cut_journal_after(repo, run_id, "step-start")
    git(repo, "update-ref", "refs/heads/run/demo", "main")  # the run branch as the kill left it
    shutil.rmtree(journal_path(repo, run_id).parent / "verifier")  # the verifier had not started

    scripted.calls.clear(), scripted.verifier_calls.clear()
    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    (call,) = scripted.continuation_calls
    assert "interrupted" in call.prompt and "scripted work" in call.prompt
    assert scripted.calls == []


def test_a_step_whose_run_branch_advance_is_in_the_journal_is_built_with_no_adapter_invocation(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id = finished_run(repo, capsys)
    tip = git(repo, "rev-parse", "run/demo").strip()
    cut_journal_after(repo, run_id, "run-branch-advance")
    scripted.calls.clear(), scripted.verifier_calls.clear()

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert scripted.calls == [] and scripted.verifier_calls == [] and scripted.continuation_calls == []
    assert git(repo, "rev-parse", "run/demo").strip() == tip
    events = read_journal_file(repo, run_id)
    assert event_names(events)[-2:] == ["step-end", "run-end"]
    assert (events[-2]["state"], events[-1]["result"]) == ("built", "built")


def test_a_run_that_has_ended_cannot_be_resumed(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id = finished_run(repo, capsys)
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1 and "Traceback" not in err
    assert run_id in err and "ended" in err
    assert journal_path(repo, run_id).read_bytes() == before


def test_an_unknown_run_id_exits_one_and_names_the_id(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))

    code, out, err = resume_main(repo, capsys, "20260101T000000Z-zzzz")

    assert code == 1 and "20260101T000000Z-zzzz" in err and "Traceback" not in err


def test_a_workflow_that_differs_from_the_run_is_refused_and_the_message_names_the_field(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id, _ = kill_a_run(repo, 3)
    (repo / "workflow.toml").write_text(HEAD + ticket("a") + ticket("b", ("a",)))
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = resume_main(repo, capsys, run_id, "--break-lock")

    assert code == 1 and "tickets" in err and run_id in err
    assert journal_path(repo, run_id).read_bytes() == before
    assert scripted.calls == []


def test_a_resume_can_name_the_workflow_file_on_the_command_line(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id, _ = kill_a_run(repo, 3)
    moved = tmp_path / "elsewhere.toml"
    moved.write_text((repo / "workflow.toml").read_text())
    (repo / "workflow.toml").unlink()

    code, out, err = run_main(capsys, str(moved), "--resume", run_id, "--repo", str(repo), "--break-lock")

    assert (code, err) == (0, "")
    assert [ticket_of(c) for c in scripted.calls] == ["b", "c"]




def usage_error(*argv):
    try:
        return delegate.main(["run", *argv])
    except SystemExit as stop:
        return stop.code


def test_run_with_neither_a_workflow_nor_a_resume_is_a_usage_error_that_says_what_is_needed(tmp_path, capsys):
    repo = make_repo(tmp_path, THREE)

    code = usage_error("--repo", str(repo))

    assert code == 2 and "--resume" in capsys.readouterr().err


def test_resume_and_dry_run_together_are_a_usage_error(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id, _ = kill_a_run(repo, 3)
    before = journal_path(repo, run_id).read_bytes()

    code = usage_error("--resume", run_id, "--dry-run", "--repo", str(repo))

    assert code == 2 and "--dry-run" in capsys.readouterr().err
    assert journal_path(repo, run_id).read_bytes() == before


def test_the_continuations_in_the_journal_count_against_the_limit_after_a_stop(tmp_path, capsys, scripted):
    # A specialist that always ends capped: the first run makes 2 continuations (the limit of assure) and fails.
    scripted.outcomes = [("capped", 0)]
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 1
    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
    cut_journal_after(repo, run_id, "continuation")  # stopped after the first continuation began

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1 and "continuation limit of 2" in err
    events = read_journal_file(repo, run_id)
    assert [e["count"] for e in events if e["event"] == "continuation"] == [1, 2]
    assert [e["count"] for e in events if e["event"] == "continuation-limit"] == [2]


def test_a_verifier_round_that_a_stop_left_open_goes_on_with_the_next_round_number(tmp_path, capsys, scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id = finished_run(repo, capsys)
    cut_journal_after(repo, run_id, "verify-start", nth=2)  # stopped as the verifier of the fix-up round began
    git(repo, "update-ref", "refs/heads/run/demo", "main")
    scripted.verifier_calls.clear(), scripted.calls.clear(), scripted.fixup_calls.clear()
    scripted.verdict_sequence = ["ACCEPT"]

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert scripted.calls == [] and scripted.fixup_calls == [] and len(scripted.verifier_calls) == 1
    starts = [e for e in read_journal_file(repo, run_id) if e["event"] == "verify-start"]
    assert [e["round"] for e in starts] == [0, 1, 2]
    assert "a.txt" in tree(repo, "run/demo")


def test_the_verifier_runs_in_the_journal_count_against_the_fix_up_limit_after_a_stop(tmp_path, capsys, scripted):
    scripted.verdict_sequence = ["REJECT"]
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 1
    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
    cut_journal_after(repo, run_id, "verify-start", nth=3)  # stopped in the last round the limit allows
    scripted.verifier_calls.clear(), scripted.fixup_calls.clear()

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1 and "ticket a:" in err and "fix-up rounds" in err
    assert scripted.verifier_calls == [] and scripted.fixup_calls == []
    events = read_journal_file(repo, run_id)
    assert [e["state"] for e in events if e["event"] == "step-end"] == ["failed"]
    assert event_names(events)[-1] == "run-end"
