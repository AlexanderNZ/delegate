"""The start of a run, the resume of a run and `delegate clean` read all branches with one git process (#347).

A git process takes 73 to 170 ms on a machine with endpoint security, so a check that starts one process for each ticket
makes a workflow with many tickets slow. A shim of `git` on `PATH` is the system boundary: it counts each process and
passes it to the real git. Each case calls `delegate.main` against a real temporary git repository.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from delegate.cli import delegate

from ..support import HEAD, WORKFLOW, committed_repo, git, read_journal_file, run_end, run_start, step_end, step_start, ticket, write_journal

NAMES = [f"t{number:02d}" for number in range(20)]


@pytest.fixture
def git_calls(tmp_path, monkeypatch):
    """A `git` first on PATH that appends its arguments to a log and runs the real git. Call the result for the logged calls."""
    shim, log = tmp_path / "shim", tmp_path / "git-calls.log"
    shim.mkdir()
    (shim / "git").write_text(f'#!{shutil.which("sh")}\nprintf \'%s\\n\' "$*" >> "{log}"\nexec "{shutil.which("git")}" "$@"\n')
    (shim / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")

    def calls() -> list[str]:
        return log.read_text().splitlines() if log.exists() else []

    return calls


def count(calls: list[str], subcommand: str) -> int:
    return sum(1 for call in calls if f" {subcommand} " in f" {call} ")


def workflow_of(tickets: int) -> str:
    return HEAD + "".join(ticket(name) for name in NAMES[:tickets])


def refused_start(tmp_path: Path, capsys, git_calls, tickets: int) -> list[str]:
    """Start a workflow whose run branch exists, so that the check reads every ticket branch before it refuses. Return the git calls."""
    repo = committed_repo(tmp_path / f"repo{tickets}", {"seed.txt": "seed\n", "workflow.toml": workflow_of(tickets)})
    git(repo, "branch", "run/demo")
    before = len(git_calls())
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    err = capsys.readouterr().err
    assert code == 1
    assert "run-branch 'run/demo' exists already" in err
    return git_calls()[before:]


def test_the_start_of_a_20_ticket_workflow_checks_the_branches_with_as_many_git_processes_as_the_start_of_a_2_ticket_workflow(
    tmp_path, capsys, git_calls, scripted
):
    small = refused_start(tmp_path, capsys, git_calls, 2)
    large = refused_start(tmp_path, capsys, git_calls, 20)

    assert len(large) == len(small)
    assert count(large, "show-ref") == 0
    assert count(large, "for-each-ref") == 1


def test_the_start_still_refuses_a_taken_ticket_branch_and_a_missing_base_branch_with_their_messages(tmp_path, capsys, git_calls, scripted):
    repo = committed_repo(tmp_path / "repo", {"seed.txt": "seed\n", "workflow.toml": workflow_of(20)})
    git(repo, "branch", "run/demo-t07")
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    err = capsys.readouterr().err
    assert code == 1
    assert "branch 'run/demo-t07' for ticket 't07' exists already; delete it or choose another run-branch" in err

    git(repo, "branch", "-D", "run/demo-t07")
    git(repo, "branch", "-m", "main", "trunk")
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    err = capsys.readouterr().err
    assert code == 1
    assert f"base-branch 'main' is not a branch of the repository {repo}" in err


def finished_run_with_branches(tmp_path: Path) -> tuple[Path, str]:
    """A repository with the run branch and 20 ticket branches that the run branch holds, and the journal of a run that ended `built`.

    The directory of each ticket worktree is gone, so the clean has no worktree to remove: it checks and deletes the branches.
    """
    repo = committed_repo(tmp_path / "repo", {"seed.txt": "seed\n", "workflow.toml": WORKFLOW})
    for branch in ("run/demo", *(f"run/demo-{name}" for name in NAMES)):
        git(repo, "branch", branch, "main")
    events = [step for name in NAMES for step in (step_start(name), step_end(name))]
    write_journal(repo, "r20", run_start("r20", *NAMES), *events, run_end("built", built=NAMES))
    return repo, "r20"


def test_clean_of_a_20_ticket_run_starts_fewer_than_15_git_processes(tmp_path, capsys, git_calls, scripted):
    repo, run_id = finished_run_with_branches(tmp_path)
    before = len(git_calls())

    code = delegate.main(["clean", run_id, "--repo", str(repo)])
    captured = capsys.readouterr()

    assert (code, captured.err) == (0, "")
    assert git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").split() == ["main", "run/demo"]
    cleanup = [e for e in read_journal_file(repo, run_id) if e["event"] == "cleanup"]
    assert cleanup[-1]["branches_deleted"] == [f"run/demo-{name}" for name in NAMES]
    calls = git_calls()[before:]
    assert len(calls) < 15, calls
    assert count(calls, "show-ref") == 0


ECONOMY = WORKFLOW.replace('mode = "assure"', 'mode = "economy"').replace('gates = ["test -f feature.txt"]', 'gates = ["ls *.txt"]')


def resumed_chain(tmp_path: Path, capsys, git_calls, tickets: int) -> list[str]:
    """Kill an economy run as its chain verifier starts, resume it, and return the git calls of the resume."""
    from ..support import kill_a_run, resume_main

    plan = [("a", ()), ("b", ("a",)), ("c", ("b",)), ("d", ("c",))][:tickets]
    chain = "".join(ticket(name, blocked_by) for name, blocked_by in plan)
    repo = committed_repo(tmp_path / f"chain{tickets}", {"seed.txt": "seed\n", "workflow.toml": ECONOMY.split("[[tickets]]")[0] + chain})
    run_id, _ = kill_a_run(repo, tickets + 1)  # one specialist call for each ticket, then the verifier of the chain
    before = len(git_calls())
    code, _, err = resume_main(repo, capsys, run_id)
    assert code == 0, err
    return git_calls()[before:]


def test_the_resume_of_a_4_ticket_chain_checks_the_ticket_branches_with_as_many_git_processes_as_the_resume_of_a_2_ticket_chain(
    tmp_path, capsys, git_calls, scripted
):
    small = resumed_chain(tmp_path, capsys, git_calls, 2)
    large = resumed_chain(tmp_path, capsys, git_calls, 4)

    # The git processes that the chain verification starts for the branches do not grow with the tickets.
    assert count(large, "merge-base") == count(small, "merge-base")
    assert count(large, "show-ref") == count(small, "show-ref")
