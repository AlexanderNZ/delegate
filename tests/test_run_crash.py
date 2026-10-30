"""A crash in a step: an exception from the adapter or from git is recorded, and it ends the run.

Each case calls `delegate.main` against a real temporary git repository with a
scripted adapter that fails on demand.
"""

import json
from pathlib import Path

from delegate.cli import delegate

from .support import event_names, git, make_repo, read_journal, read_journal_file, resume_main, run_main, ticket_of, tree
from .test_run_economy import ECONOMY
from .test_run_multi import HEAD, ticket


CRASH = HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c")


def raise_in_b(request):
    if ticket_of(request) == "b":
        raise RuntimeError("harness binary missing")


def break_run_branch(request):
    # The run does not read the base branch after `run-start`, so the run branch is the branch that the step needs.
    if ticket_of(request) == "b":
        git(request.cwd, "update-ref", "-d", "refs/heads/run/demo")


def test_an_exception_from_the_adapter_records_a_failed_step_end_then_a_run_end_and_exits_non_zero(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, CRASH)
    scripted.on_specialist = raise_in_b

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1
    events = read_journal(out)
    step_end = next(e for e in events if e["event"] == "step-end" and e["ticket"] == "b")
    assert step_end["state"] == "failed" and "RuntimeError" in step_end["reason"] and "harness binary missing" in step_end["reason"]
    names = event_names(events)
    assert names[-1] == "run-end" and names.index("step-end", names.index("step-start", 3)) < len(names) - 1
    run_end = events[-1]
    assert (run_end["result"], run_end["built"], run_end["failed"], run_end["skipped"]) == ("failed", ["a"], ["b"], ["c"])
    skip = next(e for e in events if e["event"] == "skip" and e["ticket"] == "c")
    assert "b" in skip["reason"]
    assert "ticket b:" in err and "harness binary missing" in err and "ticket c: skipped" in err


def test_an_exception_from_git_during_a_step_records_a_failed_step_end_then_a_run_end_and_exits_non_zero(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, CRASH)
    scripted.on_specialist = break_run_branch

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1
    events = read_journal_file(repo, next((repo / ".git" / "delegate" / "runs").iterdir()).name)
    step_end = next(e for e in events if e["event"] == "step-end" and e["ticket"] == "b")
    assert step_end["state"] == "failed" and "git" in step_end["reason"]
    assert event_names(events)[-1] == "run-end" and events[-1]["result"] == "failed"
    assert "ticket b:" in err


def test_an_exception_from_the_verifier_adapter_records_a_failed_step_end_then_a_run_end(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, CRASH)

    def explode(request):
        raise RuntimeError("verifier harness died")

    scripted.during_verifier = explode

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1
    events = read_journal(out)
    step_end = next(e for e in events if e["event"] == "step-end")
    assert (step_end["ticket"], step_end["state"]) == ("a", "failed") and "verifier harness died" in step_end["reason"]
    assert event_names(events)[-1] == "run-end"
    assert "a.txt" not in tree(repo, "run/demo")


def boom(request):
    raise RuntimeError("boom")


def test_a_crash_of_a_step_keeps_its_traceback_in_a_file_that_the_step_end_names(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    scripted.on_specialist = boom

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1
    run_dir = next((repo / ".git" / "delegate" / "runs").iterdir())
    step_end = next(e for e in read_journal(out) if e["event"] == "step-end")
    assert step_end["reason"] == "crashed: RuntimeError: boom"
    assert Path(step_end["traceback"]) == run_dir / f"crash-{step_end['seq']}.txt"
    text = Path(step_end["traceback"]).read_text()
    assert "Traceback (most recent call last)" in text
    assert "in boom" in text and text.rstrip().endswith("RuntimeError: boom")


def test_a_crash_of_the_chain_verification_of_economy_mode_keeps_its_traceback_in_a_file(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, ECONOMY)
    scripted.files_by_ticket = {"a": {"feature.txt": "a\n"}}  # the gate of the workflow needs this file
    scripted.during_verifier = boom

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1
    run_dir = next((repo / ".git" / "delegate" / "runs").iterdir())
    step_end = [e for e in read_journal(out) if e["event"] == "step-end" and e["state"] == "failed"][-1]
    assert Path(step_end["traceback"]) == run_dir / f"crash-{step_end['seq']}.txt"
    assert Path(step_end["traceback"]).read_text().rstrip().endswith("RuntimeError: boom")


def test_status_of_a_run_that_crashed_shows_the_path_of_the_traceback(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    scripted.on_specialist = boom
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    step_end = next(e for e in read_journal(out) if e["event"] == "step-end")

    assert delegate.main(["status", "--repo", str(repo)]) == 0
    text = capsys.readouterr().out

    assert "crashed: RuntimeError: boom" in text
    assert step_end["traceback"] in text


def test_watch_of_a_run_that_crashed_shows_the_path_of_the_traceback(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    scripted.on_specialist = boom
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    step_end = next(e for e in read_journal(out) if e["event"] == "step-end")

    delegate.main(["watch", "--repo", str(repo)])
    text = capsys.readouterr().out

    assert step_end["traceback"] in text


def test_clean_removes_the_traceback_of_a_crash_that_a_retry_built_over(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    scripted.during_verifier = boom
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
    crash_file = Path(next(e for e in read_journal(out) if e["event"] == "step-end")["traceback"])
    scripted.during_verifier = None
    assert resume_main(repo, capsys, run_id, "--retry", "a")[0] == 0
    assert crash_file.exists()

    assert delegate.main(["clean", run_id, "--repo", str(repo)]) == 0
    cleaned = capsys.readouterr().out

    assert not crash_file.exists()
    assert str(crash_file) in cleaned
    assert str(crash_file) in json.dumps(read_journal_file(repo, run_id)[-1])
