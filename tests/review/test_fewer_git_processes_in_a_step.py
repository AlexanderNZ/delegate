"""A one-ticket `assure` run starts fewer git processes in each step (#348).

A git process takes 73 to 170 ms on a machine with endpoint security, so each call that a step can leave out is time
of the run. A shim of `git` on `PATH` is the system boundary: it counts each process and passes it to the real git.
The case calls `delegate.main` against a real temporary git repository.
"""

from __future__ import annotations

import os
import shutil

import pytest

from delegate import adapters
from delegate.cli import delegate

from ..support import WORKFLOW, ScriptedAdapter, git, make_repo, read_journal

# The git processes that this run started before #348, counted with the same shim on the tree of the commit before the
# change. They include the 6 processes of the scripted adapter, which writes and commits the work of the specialist for
# each of the three sessions.
BEFORE = 100

# The cut that the change must give.
CUT = 25

THREE_GATES = 'gates = ["test -f feature.txt", "ls seed.txt", "cat seed.txt"]'


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


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def test_a_one_ticket_assure_run_with_two_rejects_and_three_gates_starts_at_least_25_git_processes_fewer_than_before(
    tmp_path, capsys, git_calls, scripted
):
    scripted.verdict_sequence = ["REJECT", "REJECT", "ACCEPT"]
    scripted.verdict_findings = ["the header row is missing"]
    repo = make_repo(tmp_path, WORKFLOW.replace('gates = ["test -f feature.txt"]', THREE_GATES))
    started_before = len(git_calls())

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()
    started = len(git_calls()) - started_before

    assert (code, captured.err) == (0, "")
    # The run is the same run: three verdicts in order, and the run branch holds the last fix-up commit.
    events = [e for e in read_journal(captured.out)]
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["REJECT", "REJECT", "ACCEPT"]
    assert git(repo, "rev-parse", "run/demo") == git(repo, "rev-parse", "run/demo-a")
    print(f"git processes of the run: {started}; before the change: {BEFORE}; fewer: {BEFORE - started}")
    assert started <= BEFORE - CUT, f"{started} git processes; before the change {BEFORE}, so at most {BEFORE - CUT} are allowed"


def test_a_new_worktree_gets_its_settings_and_its_push_guard_with_no_git_config_process(tmp_path, capsys, git_calls, scripted):
    repo = make_repo(tmp_path)
    started_before = len(git_calls())

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()

    assert (code, captured.err) == (0, "")
    commands = [call.split(" -c core.fsmonitor=false ", 1)[-1] for call in git_calls()[started_before:]]
    assert [command for command in commands if command.startswith("config ")] == []
