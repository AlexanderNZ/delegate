"""Ctrl-C ends each `delegate` subcommand with the exit status 130 and one line, not a traceback (#109).

Each case calls `delegate.main(argv)` and raises `KeyboardInterrupt` at a system
boundary, as Ctrl-C on a terminal does: the wait between two polls of `watch`
(`time.sleep`), the write of a frame to the terminal, the harness adapter of
`run` (the scripted adapter), or the git process of a command
(`subprocess.run`). The journals and the repositories are real temporary ones.
"""

from __future__ import annotations

import io
import subprocess
import sys
import time

import pytest

from delegate.cli import delegate

from .support import (
    TITLE, Terminal, bare_repo, event_names, frames, journal_path, live_lock, make_repo, read_journal, read_journal_file,
    resume_main, run_start, step_start, ticket_of, write_journal,
)
from .test_run_multi import HEAD, ticket

# The exit status of a process that SIGINT ended, as a shell gives it.
INTERRUPTED = 130

# The control sequence that shows the cursor of the terminal, and the end of a frame of the live view.
SHOW_CURSOR = "\x1b[?25h"
FRAME_END = "\x1b[J"


def ctrl_c(*args, **kwargs):
    raise KeyboardInterrupt


def main(argv: list[str]) -> int:
    """Call `delegate.main(argv)`. A `KeyboardInterrupt` that leaves it would print a traceback, so it fails the case."""
    try:
        return delegate.main(argv)
    except KeyboardInterrupt:
        pytest.fail(f"Ctrl-C left delegate {argv[0]} as a KeyboardInterrupt, which prints a traceback")


def a_run_that_goes_on(repo) -> None:
    """The journal of the run r1: the step of ticket a started, and the run goes on. The last position is 2."""
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"))


def watch(monkeypatch, capsys, out: io.StringIO, *argv: str) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.setenv("LINES", "40")
    monkeypatch.delenv("DELEGATE_NO_TITLE", raising=False)
    code = main(["watch", *argv])
    return code, out.getvalue(), capsys.readouterr().err


@pytest.mark.parametrize("extra", [(), ("--from", "1")])
def test_ctrl_c_in_watch_to_a_pipe_exits_130_with_the_position_to_go_on_from(tmp_path, monkeypatch, capsys, extra):
    repo = bare_repo(tmp_path)
    a_run_that_goes_on(repo)
    monkeypatch.setattr(time, "sleep", ctrl_c)

    code, out, err = watch(monkeypatch, capsys, io.StringIO(), "r1", "--repo", str(repo), *extra)

    assert code == INTERRUPTED
    assert "Traceback" not in err
    assert err == "watch: stopped at position 2; the run goes on. Resume with delegate watch r1 --from 2\n"
    # The events before the interrupt are printed, and the last line of a watch that exits for another reason is not.
    assert "step-start" in out and "position" not in out


def test_ctrl_c_in_watch_with_no_run_id_names_the_newest_run_in_the_line(tmp_path, monkeypatch, capsys):
    repo = bare_repo(tmp_path)
    a_run_that_goes_on(repo)
    monkeypatch.setattr(time, "sleep", ctrl_c)

    # The run is active: the process of a run that goes on holds the lock of its run branch.
    with live_lock(journal_path(repo, "r1")):
        code, _, err = watch(monkeypatch, capsys, io.StringIO(), "--repo", str(repo))

    assert code == INTERRUPTED
    assert err == "watch: stopped at position 2; the run goes on. Resume with delegate watch r1 --from 2\n"


def test_ctrl_c_in_the_live_view_restores_the_cursor_the_end_of_the_frame_and_the_title(tmp_path, monkeypatch, capsys):
    repo = bare_repo(tmp_path)
    a_run_that_goes_on(repo)
    monkeypatch.setattr(time, "sleep", ctrl_c)

    code, out, err = watch(monkeypatch, capsys, Terminal(), "r1", "--repo", str(repo))

    assert code == INTERRUPTED
    assert "Traceback" not in err
    assert err == "watch: stopped at position 2; the run goes on. Resume with delegate watch r1 --from 2\n"
    # The output ends with the end of a whole frame and the sequence that shows the cursor.
    assert out.endswith(FRAME_END + "\n" + SHOW_CURSOR)
    assert any("#a   started" in line for line in frames(out)[-1])
    # The title no longer gives the progress: it gives the reason that the watch ended.
    assert TITLE.findall(out)[-1] == "delegate watch · r1 · interrupted"


class _InterruptedTerminal(Terminal):
    """A terminal on which Ctrl-C comes while the first frame is written: the frame is cut after its first part."""

    def __init__(self) -> None:
        super().__init__()
        self.cut = False

    def write(self, text: str) -> int:
        if not self.cut and text.startswith("\x1b[2J"):
            self.cut = True
            super().write(text[: len(text) // 2])
            raise KeyboardInterrupt
        return super().write(text)


def test_ctrl_c_while_a_frame_is_written_draws_a_whole_frame_before_the_line(tmp_path, monkeypatch, capsys):
    repo = bare_repo(tmp_path)
    a_run_that_goes_on(repo)
    monkeypatch.setattr(time, "sleep", lambda seconds: pytest.fail("watch waited after Ctrl-C"))

    code, out, err = watch(monkeypatch, capsys, _InterruptedTerminal(), "r1", "--repo", str(repo))

    assert code == INTERRUPTED
    assert err == "watch: stopped at position 2; the run goes on. Resume with delegate watch r1 --from 2\n"
    assert out.endswith(FRAME_END + "\n" + SHOW_CURSOR)
    assert any("#a   started" in line for line in frames(out)[-1])


@pytest.mark.parametrize("command", [["status", "r1"], ["pause", "r1"], ["status", "--json"]])
def test_ctrl_c_in_another_subcommand_exits_130_with_one_line(tmp_path, monkeypatch, capsys, command):
    repo = bare_repo(tmp_path)
    a_run_that_goes_on(repo)
    monkeypatch.setattr(subprocess, "run", ctrl_c)  # Ctrl-C comes while the command waits for git

    code = main([*command, "--repo", str(repo)])

    err = capsys.readouterr().err
    assert code == INTERRUPTED
    assert "Traceback" not in err
    assert err == "delegate: interrupted\n"


def test_ctrl_c_in_a_foreground_run_releases_the_lock_records_interrupted_and_resume_goes_on(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))

    def interrupt_in_b(request):
        if ticket_of(request) == "b":
            raise KeyboardInterrupt  # Ctrl-C while the specialist of b works

    scripted.on_specialist = interrupt_in_b

    code = main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()
    out, err = captured.out, captured.err

    assert code == INTERRUPTED
    assert "Traceback" not in err
    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
    assert err == f"delegate run: interrupted; the run {run_id} stopped. Go on with delegate run --resume {run_id}\n"
    assert list((repo / ".git" / "delegate" / "locks").glob("*.lock")) == []
    events = read_journal_file(repo, run_id)
    assert event_names(events)[-1] == "interrupted"
    assert "run-end" not in event_names(events)

    scripted.on_specialist = None
    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    events = read_journal_file(repo, run_id)
    (end,) = [e for e in events if e["event"] == "run-end"]
    assert (end["result"], end["built"]) == ("built", ["a", "b"])
