"""The watch and resume how-to page: a reader who follows it sees a run, follows its journal, and resumes it after a stop.

The scripted adapter stands in for the live harness. Two stops are real: an
interrupt (Ctrl-C) that the adapter raises, and a SIGKILL of a child process
that runs `delegate run`. A killed process cannot release its lock.
"""

import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from delegate import adapters

from .pages import HOW_TO, Block, follow, new_repo, outside_the_package, write_files
from .support import ScriptedAdapter, git

PAGE = HOW_TO / "watch-and-resume-a-run.md"
ROOT = Path(__file__).resolve().parents[1]  # the child imports `tests` from here and the package from src/
SOURCE = ROOT / "src"

FILES = {
    ticket: {f"test_{ticket}.py": f"import unittest\n\n\nclass T(unittest.TestCase):\n    def test_{ticket}(self):\n        self.assertTrue(True)\n"}
    for ticket in ("1", "2")
}


def containing(text: str):
    return lambda block: any(text in command for command in block.body.splitlines())


def a_repo(tmp_path: Path) -> Path:
    repo = new_repo(tmp_path / "repo")
    write_files(PAGE, repo)
    (repo / "seed.txt").write_text("seed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


def run_id_of(steps) -> str:
    (match,) = re.findall(r"^run (\S+)$", steps[0].out, flags=re.MULTILINE)
    return match


class Interrupted(ScriptedAdapter):
    """Raises the interrupt of Ctrl-C at the start of its Nth call."""

    interrupt_at: int = 3
    seen: int = 0

    def run(self, request):
        self.seen += 1
        if self.seen == self.interrupt_at:
            raise KeyboardInterrupt
        return super().run(request)


@pytest.fixture
def registered():
    names = []

    def register(adapter):
        adapters.register("claude-code", adapter)
        names.append("claude-code")

    yield register
    for name in names:
        adapters.unregister(name)


@outside_the_package
def test_the_page_shows_the_state_of_a_finished_run_and_follows_its_journal_to_the_end_and_from_a_position(
    tmp_path, monkeypatch, git_identity, registered
):
    registered(ScriptedAdapter(files_by_ticket=FILES))
    repo = a_repo(tmp_path)

    started = follow(PAGE, repo, monkeypatch, only=containing("delegate run workflow.toml"))
    watched = follow(PAGE, repo, monkeypatch, only=containing("delegate status"))
    from_position = follow(PAGE, repo, monkeypatch, only=containing("--from"), replace={"<position>": "2"})

    assert [(step.code, step.err) for step in started + watched + from_position] == [(0, "")] * (len(started + watched + from_position))
    status = next(step for step in watched if step.command == "delegate status")
    assert "ticket 1: state built, verdict ACCEPT" in status.out and "ticket 2: state built, verdict ACCEPT" in status.out
    watch = next(step for step in watched if step.command.startswith("delegate watch"))
    assert watch.out.splitlines()[0].startswith("1 ") and " run-start " in watch.out.splitlines()[0]
    assert watch.out.splitlines()[-2].endswith("run-end result=built") and watch.out.splitlines()[-1] == "position 26"
    # Going on from position 2 prints the events after it, and no event at or before it.
    printed = [line for line in from_position[0].out.splitlines() if line[:1].isdigit() and " " in line]
    assert printed[0].startswith("3 ") and printed[-1].endswith("run-end result=built")


@outside_the_package
def test_the_page_resumes_a_run_that_ctrl_c_stopped_and_builds_only_the_ticket_that_was_not_done(
    tmp_path, monkeypatch, git_identity, registered
):
    adapter = Interrupted(files_by_ticket=FILES)  # the interrupt comes as the specialist of ticket 2 starts
    registered(adapter)
    repo = a_repo(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        follow(PAGE, repo, monkeypatch, only=containing("delegate run workflow.toml"))
    run_id = next((repo / ".git" / "delegate" / "runs").iterdir()).name

    state = follow(PAGE, repo, monkeypatch, only=containing("delegate status"), skip=("delegate watch",))
    resumed = follow(PAGE, repo, monkeypatch, only=lambda block: containing("--resume")(block) and not containing("--break-lock")(block), replace={"<run-id>": run_id})

    assert "running" in state[0].out.splitlines()[0]  # the run has no end, so it is still "running"
    assert [(step.code, step.err) for step in resumed] == [(0, "")]
    assert [call.cwd.name for call in adapter.calls] == ["1", "2"]  # ticket 1 once before the stop; ticket 2 once after it
    assert "ticket 2: state built, verdict ACCEPT" in follow(PAGE, repo, monkeypatch, only=containing("delegate status"), skip=("delegate watch",))[0].out


KILL = """\
import os, signal, sys
from delegate import adapters
from delegate.cli import delegate
from tests.support import ScriptedAdapter
from tests.test_howto_watch_resume import FILES

class Killer(ScriptedAdapter):
    seen = 0

    def run(self, request):
        type(self).seen += 1
        if type(self).seen == 3:  # the specialist of ticket 2
            os.kill(os.getpid(), signal.SIGKILL)
        return super().run(request)

adapters.register("claude-code", Killer(files_by_ticket=FILES))
sys.exit(delegate.main(["run", "workflow.toml", "--repo", sys.argv[1]]))
"""


@outside_the_package
def test_the_page_resumes_a_run_that_was_killed_with_break_lock_and_a_resume_without_it_is_refused(
    tmp_path, monkeypatch, git_identity, registered, capsys
):
    repo = a_repo(tmp_path)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SOURCE), str(ROOT), os.environ.get("PYTHONPATH")]))}
    child = subprocess.run([sys.executable, "-c", KILL, str(repo)], cwd=repo, env=env, capture_output=True, text=True)
    assert child.returncode == -signal.SIGKILL, child.stderr
    run_id = next((repo / ".git" / "delegate" / "runs").iterdir()).name
    adapter = ScriptedAdapter(files_by_ticket=FILES)
    registered(adapter)

    plain = follow(PAGE, repo, monkeypatch, only=lambda block: containing("--resume")(block) and not containing("--break-lock")(block), replace={"<run-id>": run_id})
    broken = follow(PAGE, repo, monkeypatch, only=containing("--break-lock"), replace={"<run-id>": run_id})

    assert plain[0].code == 1 and "--break-lock" in plain[0].err  # the lock of a killed process stays
    assert [(step.code, step.err) for step in broken] == [(0, "")]
    assert [call.cwd.name for call in adapter.calls] == ["2"]
