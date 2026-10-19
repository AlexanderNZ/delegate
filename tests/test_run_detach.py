"""`delegate run --detach` starts the run in a new session, prints the run id and the log path, and exits at once.

A harness can stop a background task at a time limit, and a run that only has
`nohup` and `&` can stay in the process group of the tool shell (issue #76).
Each case runs `main(argv)` against a real temporary git repository. The
detached run is a real process. The `claude` process is the system boundary: a
stand-in on PATH records its session id, which it gets from the run process
that starts it.
"""

from __future__ import annotations

import contextlib
import os
import re
import time
from pathlib import Path

import pytest

from . import claude_fake
from .support import WORKFLOW, event_names, git, journal_path, live_lock, make_repo, read_journal_file, run_main

CLAUDE_CODE = WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"')

# The longest time that a test waits for a detached run to end.
WAIT_SECONDS: float = 120.0


def printed(out: str) -> tuple[str, Path]:
    """The run id and the log path that `delegate run --detach` printed. Each must be on its own line, once."""
    runs = re.findall(r"^run (\S+)$", out, flags=re.MULTILINE)
    logs = re.findall(r"^log (.+)$", out, flags=re.MULTILINE)
    assert (len(runs), len(logs)) == (1, 1), out
    return runs[0], Path(logs[0])


def wait_for_ends(log: Path, ends: int) -> str:
    """The text of the log when it holds the last line of `ends` runs of `delegate run`. Fail after `WAIT_SECONDS`."""
    deadline = time.monotonic() + WAIT_SECONDS
    text = ""
    while time.monotonic() < deadline:
        text = log.read_text() if log.is_file() else ""
        if len(re.findall(r"^journal ", text, flags=re.MULTILINE)) >= ends:
            return text
        time.sleep(0.2)
    raise AssertionError(f"the detached run did not end in {WAIT_SECONDS} seconds; the log holds:\n{text}")


def test_detach_exits_at_once_prints_the_run_id_and_the_run_goes_on_in_a_new_session(tmp_path, monkeypatch, capsys):
    # The stand-in waits 2 seconds before it records its call, so a command that waited for the run would see a call.
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl", start_seconds=2)
    repo = make_repo(tmp_path, CLAUDE_CODE)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--detach")

    assert fake.calls() == [], "the command waited for the run"
    assert (code, err) == (0, "")
    run_id, log = printed(out)
    assert log.resolve() == journal_path(repo, run_id).parent.resolve() / "run.log"
    text = wait_for_ends(log, 1)
    (call,) = fake.calls()
    assert call["sid"] != os.getsid(0), "the run process is in the session of the command that started it"
    assert f"run {run_id}" in text.splitlines()  # the log holds the output of the run process
    assert read_journal_file(repo, run_id)[0]["run_id"] == run_id


def test_detach_with_resume_goes_on_with_the_same_run_in_a_new_session_and_appends_to_its_log(tmp_path, monkeypatch, capsys):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    # In economy mode, a retry of a step with no report starts the specialist again, so the resumed run starts the stand-in.
    repo = make_repo(tmp_path, CLAUDE_CODE.replace('mode = "assure"', 'mode = "economy"'))
    _, out, _ = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--detach")
    run_id, log = printed(out)
    wait_for_ends(log, 1)  # the stand-in does no work, so the step of ticket a fails, and the run ends

    code, out, err = run_main(capsys, "--resume", run_id, "--retry", "a", "--repo", str(repo), "--detach")

    assert (code, err) == (0, "")
    assert printed(out) == (run_id, log)
    text = wait_for_ends(log, 2)
    assert [line for line in text.splitlines() if line.startswith("run ")] == [f"run {run_id}"] * 2
    assert "retry" in event_names(read_journal_file(repo, run_id))
    calls = fake.calls()
    assert len(calls) >= 2, "the resumed run did not start the specialist again"
    assert all(call["sid"] != os.getsid(0) for call in calls)


def test_detach_with_resume_of_an_unknown_run_exits_1_and_starts_nothing(tmp_path, capsys):
    repo = make_repo(tmp_path, CLAUDE_CODE)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--resume", "20261018T000000Z-0000", "--repo", str(repo), "--detach")

    assert (code, out) == (1, "")
    assert "20261018T000000Z-0000" in err and "not found" in err
    assert not (repo / ".git" / "delegate" / "runs").exists()


def test_detach_with_a_workflow_problem_exits_1_at_once_and_starts_nothing(tmp_path, capsys):
    repo = make_repo(tmp_path, CLAUDE_CODE.replace('adapter = "claude-code"', 'adapter = "no-such-harness"'))

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--detach")

    assert (code, out) == (1, "")
    assert "no-such-harness" in err
    assert not (repo / ".git" / "delegate" / "runs").exists()


def _take_the_run_branch(repo: Path) -> contextlib.AbstractContextManager[None]:
    git(repo, "branch", "run/demo")  # a branch that an earlier run left
    return contextlib.nullcontext()


def _remove_the_base_branch(repo: Path) -> contextlib.AbstractContextManager[None]:
    (repo / "workflow.toml").write_text((repo / "workflow.toml").read_text().replace('base-branch = "main"', 'base-branch = "nope"'))
    return contextlib.nullcontext()


def _lock_the_run_branch(repo: Path) -> contextlib.AbstractContextManager[None]:
    # The process of the test holds the lock for another run, so the lock names a process that exists.
    return live_lock(journal_path(repo, "20261018T000000Z-0000"), "run/demo")


@pytest.mark.parametrize(
    ("make_the_fault", "error"),
    [
        (_take_the_run_branch, "run-branch 'run/demo' exists already"),
        (_remove_the_base_branch, "base-branch 'nope' is not a branch of the repository"),
        (_lock_the_run_branch, "run branch run/demo is in use by run 20261018T000000Z-0000"),
    ],
    ids=["run branch exists", "no base branch", "run branch locked"],
)
def test_detach_with_a_check_that_fails_exits_1_with_the_error_of_a_run_in_the_foreground_and_starts_no_process(
    tmp_path, monkeypatch, capsys, make_the_fault, error,
):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, CLAUDE_CODE)

    with make_the_fault(repo):
        code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--detach")
        foreground = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, out) == (1, "")
    assert error in err and err.startswith("delegate run: ")
    assert (foreground[0], foreground[2]) == (1, err), "the detached start and the run in the foreground give different errors"
    # No run process started: the run has no directory, so it has no log and no journal, and no agent was called.
    assert not (repo / ".git" / "delegate" / "runs").exists()
    assert fake.calls() == []


def test_detach_and_dry_run_cannot_go_together(tmp_path, capsys):
    repo = make_repo(tmp_path, CLAUDE_CODE)

    with pytest.raises(SystemExit) as stop:
        run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--detach", "--dry-run")

    assert stop.value.code == 2
    assert "--detach and --dry-run cannot go together" in capsys.readouterr().err
    assert not (repo / ".git" / "delegate").exists()
