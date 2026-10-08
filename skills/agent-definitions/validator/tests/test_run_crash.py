"""A crash in a step: an exception from the adapter or from git is recorded, and it ends the run.

Each case calls `delegate.main` against a real temporary git repository with a
scripted adapter that fails on demand.
"""

from .support import event_names, git, make_repo, read_journal, read_journal_file, run_main, ticket_of, tree
from .test_run_multi import HEAD, ticket


CRASH = HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c")


def raise_in_b(request):
    if ticket_of(request) == "b":
        raise RuntimeError("harness binary missing")


def break_base_branch(request):
    if ticket_of(request) == "b":
        git(request.cwd, "update-ref", "-d", "refs/heads/main")


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
    scripted.on_specialist = break_base_branch

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
