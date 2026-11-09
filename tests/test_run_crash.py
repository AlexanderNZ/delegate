"""A crash in a step: an exception from the adapter or from git is recorded, and the step stays open (#281).

A crash writes `crashed`, and no `step-end` and no `run-end`, so `--resume` goes on with the open step, as after a kill.
An invariant violation and a moved run branch still end the run. Each case calls `delegate.main` against a real
temporary git repository with a scripted adapter that fails on demand. A case that needs a failure of git after the
session puts a wrapper around the real git backend, the version-control boundary of the command.
"""

import json
from pathlib import Path

from delegate.adapters.git import GitVersionControl
from delegate.cli import delegate, run_command
from delegate.ports.vcs import VcsError

from .support import state_dir
from .support import event_names, git, journal_path, make_repo, read_journal, read_journal_file, resume_main, run_main, ticket_of, tree
from .test_run_economy import ECONOMY
from .test_run_multi import HEAD, ticket


CRASH = HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c")

# The exit status of `delegate run` after a halt, which a crash now shares, and the exit code of `delegate watch` for it.
EXIT_HALTED = 4
WATCH_EXIT_HALTED = 8


class FailAfterSession:
    """The real git backend. After `arm`, the next call raises VcsError once: a held `index.lock`, a full disk."""

    armed = False

    def __init__(self) -> None:
        self._inner = GitVersionControl()

    def __getattr__(self, name):
        method = getattr(self._inner, name)

        def call(*args, **kwargs):
            if type(self).armed:
                type(self).armed = False
                raise VcsError(f"{name}: fatal: Unable to create 'index.lock': File exists")
            return method(*args, **kwargs)

        return call


def test_a_failure_of_git_after_the_specialist_session_leaves_the_step_open_and_a_resume_builds_it(tmp_path, capsys, scripted, monkeypatch):
    # assure mode. The first session commits its work and writes no report; git then fails before the engine has a
    # report that says committed. Before #281 the run ended, and neither --resume nor --retry could go on with it.
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    monkeypatch.setattr(run_command, "GitVersionControl", FailAfterSession)
    FailAfterSession.armed = False

    def arm(request):
        FailAfterSession.armed = True  # the session runs; the next call of the engine to git fails

    scripted.on_specialist = arm
    scripted.report_text = lambda request, head: None

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
    assert code == EXIT_HALTED
    events = read_journal_file(repo, run_id)
    names = event_names(events)
    assert "run-end" not in names and "step-end" not in names and names[-1] == "crashed"
    assert not any(e["event"] == "report-validation" and e["valid"] for e in events)
    crashed = events[-1]
    assert (crashed["ticket"], crashed["phase"], crashed["error"]) == ("a", "specialist", "EngineError")
    assert "index.lock" in crashed["message"]
    assert Path(crashed["traceback"]) == journal_path(repo, run_id).parent / f"crash-{crashed['seq']}.txt"
    assert Path(crashed["traceback"]).read_text().rstrip().endswith(crashed["message"])
    assert f"crashed at the specialist of ticket a; go on with delegate run --resume {run_id}" in out
    assert any(line.startswith("run stopped") for line in out.splitlines())  # the summary line
    assert "delegate run: crashed at the specialist of ticket a: EngineError: " in err and "index.lock" in err

    scripted.on_specialist = None
    scripted.report_text = None
    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 0, err
    resumed = read_journal_file(repo, run_id)
    assert next(e for e in resumed if e["event"] == "resume")["open"] == "a"
    assert [(e["ticket"], e["state"]) for e in resumed if e["event"] == "step-end"] == [("a", "built")]
    assert resumed[-1]["event"] == "run-end" and resumed[-1]["result"] == "built"
    assert "a.txt" in tree(repo, "run/demo")


def raise_in_b(request):
    if ticket_of(request) == "b":
        raise RuntimeError("harness binary missing")


def break_run_branch(request):
    # The run does not read the base branch after `run-start`, so the run branch is the branch that the step needs.
    if ticket_of(request) == "b":
        git(request.cwd, "update-ref", "-d", "refs/heads/run/demo")


def crashed_of(events):
    """The one `crashed` event of the events."""
    (crashed,) = [e for e in events if e["event"] == "crashed"]
    return crashed


def test_an_exception_from_the_adapter_leaves_the_step_open_skips_nothing_and_a_resume_builds_the_rest(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, CRASH)
    scripted.on_specialist = raise_in_b

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == EXIT_HALTED
    events = read_journal(out)
    names = event_names(events)
    assert names[-1] == "crashed" and "run-end" not in names and "skip" not in names
    assert [(e["ticket"], e["state"]) for e in events if e["event"] == "step-end"] == [("a", "built")]
    crashed = events[-1]
    assert (crashed["ticket"], crashed["phase"], crashed["error"], crashed["message"]) == (
        "b", "specialist", "RuntimeError", "harness binary missing"
    )
    assert "delegate run: crashed at the specialist of ticket b: RuntimeError: harness binary missing" in err
    assert "skipped" not in err
    run_id = events[0]["run_id"]
    scripted.on_specialist = None

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 0, err
    resumed = read_journal_file(repo, run_id)
    assert next(e for e in resumed if e["event"] == "resume")["open"] == "b"
    assert (resumed[-1]["event"], resumed[-1]["built"], resumed[-1]["failed"], resumed[-1]["skipped"]) == ("run-end", ["a", "b", "c"], [], [])
    assert {"a.txt", "b.txt", "c.txt"} <= set(tree(repo, "run/demo"))


def test_an_exception_from_git_during_a_step_writes_crashed_and_no_run_end(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, CRASH)
    scripted.on_specialist = break_run_branch

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == EXIT_HALTED
    events = read_journal_file(repo, next((state_dir(repo) / "runs").iterdir()).name)
    crashed = crashed_of(events)
    assert crashed["ticket"] == "b" and "git" in crashed["message"]
    assert event_names(events)[-1] == "crashed" and "run-end" not in event_names(events)
    assert "crashed at the" in err and "of ticket b:" in err


def test_an_exception_from_the_verifier_adapter_writes_crashed_at_the_verifier_and_the_run_branch_does_not_move(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, CRASH)

    def explode(request):
        raise RuntimeError("verifier harness died")

    scripted.during_verifier = explode

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == EXIT_HALTED
    events = read_journal(out)
    crashed = crashed_of(events)
    assert (crashed["ticket"], crashed["phase"], crashed["message"]) == ("a", "verifier", "verifier harness died")
    assert "step-end" not in event_names(events) and "run-end" not in event_names(events)
    assert "a.txt" not in tree(repo, "run/demo")


def boom(request):
    raise RuntimeError("boom")


def test_a_crash_of_a_step_keeps_its_traceback_in_a_file_that_the_crashed_event_names(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    scripted.on_specialist = boom

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == EXIT_HALTED
    run_dir = next((state_dir(repo) / "runs").iterdir())
    crashed = crashed_of(read_journal(out))
    assert (crashed["error"], crashed["message"]) == ("RuntimeError", "boom")
    assert Path(crashed["traceback"]) == run_dir / f"crash-{crashed['seq']}.txt"
    text = Path(crashed["traceback"]).read_text()
    assert "Traceback (most recent call last)" in text
    assert "in boom" in text and text.rstrip().endswith("RuntimeError: boom")
    assert f"; traceback {crashed['traceback']}" in err


def test_a_crash_of_the_chain_verification_of_economy_mode_leaves_the_chain_open_and_a_resume_verifies_it(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, ECONOMY)
    scripted.files_by_ticket = {"a": {"feature.txt": "a\n"}}  # the gate of the workflow needs this file
    scripted.during_verifier = boom

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == EXIT_HALTED
    run_dir = next((state_dir(repo) / "runs").iterdir())
    events = read_journal(out)
    crashed = crashed_of(events)
    assert (crashed["ticket"], crashed["phase"]) == ("a", "chain")
    assert [(e["ticket"], e["state"]) for e in events if e["event"] == "step-end"] == [("a", "built")]
    assert "run-end" not in event_names(events)
    assert Path(crashed["traceback"]) == run_dir / f"crash-{crashed['seq']}.txt"
    assert Path(crashed["traceback"]).read_text().rstrip().endswith("RuntimeError: boom")
    assert "crashed at the verification of the chain at ticket a" in out
    scripted.during_verifier = None

    code, out, err = resume_main(repo, capsys, run_dir.name)

    assert code == 0, err
    resumed = read_journal_file(repo, run_dir.name)
    assert (resumed[-1]["event"], resumed[-1]["result"]) == ("run-end", "built")
    assert "feature.txt" in tree(repo, "run/demo")


def test_status_of_a_run_that_crashed_shows_it_stopped_with_the_reason_crashed_the_traceback_and_the_resume_command(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    scripted.on_specialist = boom
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    crashed = crashed_of(read_journal(out))
    run_id = read_journal(out)[0]["run_id"]

    assert delegate.main(["status", "--repo", str(repo)]) == 0
    text = capsys.readouterr().out
    assert delegate.main(["status", "--repo", str(repo), "--json"]) == 0
    snapshot = json.loads(capsys.readouterr().out)

    lines = text.splitlines()
    assert lines[0].startswith(f"run {run_id}: stopped ")
    assert lines[1] == (
        f"note: crashed at the specialist of ticket a: RuntimeError: boom (traceback {crashed['traceback']}); "
        f"go on with delegate run --resume {run_id}"
    )
    assert (snapshot["state"], snapshot["note"]) == ("stopped", lines[1].removeprefix("note: "))


def test_watch_of_a_run_that_crashed_shows_it_stopped_with_the_traceback_and_the_resume_command(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    scripted.on_specialist = boom
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    crashed = crashed_of(read_journal(out))
    run_id = read_journal(out)[0]["run_id"]

    code = delegate.main(["watch", "--repo", str(repo)])
    captured = capsys.readouterr()

    assert code == WATCH_EXIT_HALTED
    assert captured.err == ""
    line = captured.out.strip().splitlines()[-1]
    assert line.startswith(f"no run is active; the newest run {run_id} stopped at ")
    assert f": crashed at the specialist of ticket a: RuntimeError: boom (traceback {crashed['traceback']})" in line
    assert line.endswith(f"; go on with delegate run --resume {run_id}")


def test_watch_of_the_journal_of_a_run_that_crashed_ends_at_the_crash_with_the_code_of_a_halt(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    scripted.on_specialist = boom
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = read_journal(out)[0]["run_id"]

    code = delegate.main(["watch", run_id, "--repo", str(repo), "--follow"])
    printed = capsys.readouterr().out.splitlines()

    assert code == WATCH_EXIT_HALTED
    assert " crashed ticket=a phase=specialist error=RuntimeError message=boom traceback=" in printed[-2]
    assert printed[-1] == f"position {printed[-2].split()[0]}"


def test_clean_removes_the_traceback_of_a_crash_that_a_resume_built_over(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    scripted.during_verifier = boom
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
    crash_file = Path(crashed_of(read_journal(out))["traceback"])
    scripted.during_verifier = None
    assert resume_main(repo, capsys, run_id)[0] == 0
    assert crash_file.exists()

    assert delegate.main(["clean", run_id, "--repo", str(repo)]) == 0
    cleaned = capsys.readouterr().out

    assert not crash_file.exists()
    assert str(crash_file) in cleaned
    assert str(crash_file) in json.dumps(read_journal_file(repo, run_id)[-1])
