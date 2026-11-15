"""The helpers of the tests of the frames of `delegate watch`: the journals of a run, the terminal of a person, and the call of the command (#354)."""

from __future__ import annotations

import io
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from delegate.cli import delegate

from .support import Terminal, append_events, bare_repo, fake_clock, frames, run_end, run_start, screens, step_end, step_start, without_titles, write_journal
from .support_watch_live import PYTEST, Run, START


class Keyboard(io.StringIO):
    """A standard input that is a terminal. Each line of `typed` is one answer of the reader."""

    def isatty(self) -> bool:
        return True


def ends_at_the_first_wait(monkeypatch, *journals):
    """At the first wait of the poll loop, each journal gets the end of its running ticket and a `run-end`."""
    done: list[bool] = []

    def wait(seconds: float) -> None:
        if not done:
            done.append(True)
            for journal, ticket in journals:
                append_events(journal, step_end(ticket), run_end("built", [ticket]))

    fake_clock().on_sleep = wait


def on_a_terminal(repo, monkeypatch, stdin, *extra, stdout=None):
    """Run `delegate watch` with no run id, with the given standard input, and standard output to a terminal of 200 columns.

    Return the exit code, the output with no title sequence, and the error output.
    """
    out, err = stdout if stdout is not None else Terminal(), io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("LINES", "50")
    monkeypatch.setenv("LC_ALL", "en_NZ.UTF-8")
    code = delegate.main(["watch", "--repo", str(repo), *POLL, *extra])
    return code, without_titles(out.getvalue()), err.getvalue()


# The time limit ends a watch that follows a run with no end, so a case fails and does not wait.
POLL = ["--poll-seconds", "0.02", "--max-minutes", "0.1"]


def local_start(path, offset: timedelta) -> str:
    """The time of the run-start event of a journal, in the zone `offset` east of UTC, as `YYYY-MM-DD HH:MM`."""
    first = json.loads(path.read_text().splitlines()[0])
    assert first["event"] == "run-start"
    return (datetime.fromisoformat(first["time"]).replace(tzinfo=None) + offset).strftime("%Y-%m-%d %H:%M")


def two_active_runs(repo):
    """Two runs that go on, on two run branches, each with no lock yet. Their journals were last changed 2 hours ago."""
    first = write_journal(
        repo, "r-assure", run_start("r-assure", "a", "b", run_branch="run/one"), step_start("a"), step_end("a"), step_start("b"),
    )
    second = write_journal(repo, "r-economy", run_start("r-economy", "x", mode="economy", run_branch="run/two"), step_start("x"))
    then = time.time() - 7200 - 10  # 10 seconds more, so a slow case still shows 2h00m
    for journal in (first, second):
        os.utime(journal, (then, then))
    return first, second


# The ticket ids and the blockers of the frames of the design (screen-map.md): 72 after 66, 67 after 72, 70 after 69,
# and 71 after 70.
BLOCKED_BY: dict[str, tuple[str, ...]] = {"72": ("66",), "67": ("72",), "70": ("69",), "71": ("70",)}


SEVEN = ["66", "72", "67", "68", "69", "70", "71"]


@pytest.fixture(autouse=True)
def terminal_of_a_person(monkeypatch):
    """A UTF-8 locale, colour on, and the local time zone 13 hours east of UTC: the run starts at 16:00 local time."""
    monkeypatch.setenv("LC_ALL", "en_NZ.UTF-8")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TZ", "NZT-13")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class Clock:
    """The clock of the commands (the `clock` fixture, `support.FakeClock`), in seconds after START. A case moves it."""

    def __init__(self, seconds: float) -> None:
        self._fake = fake_clock()
        self.seconds = seconds

    @property
    def seconds(self) -> float:
        return self._seconds

    @seconds.setter
    def seconds(self, value: float) -> None:
        self._seconds = value
        self._fake.at = (START + timedelta(seconds=value)).timestamp()


def built(run: Run, ticket: str, start: float) -> None:
    """A ticket that is built in 600 seconds: the specialist, the gate pytest, and a verifier that accepts."""
    run.step_start(start, ticket)
    run.agent(start, start + 300, ticket, "specialist", run.stream(f"{ticket}.specialist.stream.jsonl"))
    run.report(start + 300, ticket)
    run.gate(start + 300, start + 360, ticket, PYTEST)
    run.verify(start + 360, start + 600, ticket)
    run.add(start + 600, "step-end", ticket=ticket, state="built", reason=None)


def verifier_starts(run: Run, start: float, ticket: str, round_number: int = 0) -> None:
    """The verifier of a ticket starts. Its event stream is not there yet: the harness has not opened it."""
    run.add(start, "verify-start", ticket=ticket, round=round_number, agent="python-verifier", tier="verifier", model="m",
            commit="1" * 40, copy=str(run.dir / "copy"))
    run.add(start, "agent-start", ticket=ticket, role="verifier", round=round_number,
            event_stream=str(run.dir / "reports" / f"{ticket}.verifier-{round_number}.stream.jsonl"))


def specialist_and_gates(run: Run, ticket: str, start: float) -> None:
    run.step_start(start, ticket)
    run.agent(start, start + 300, ticket, "specialist", run.stream(f"{ticket}.specialist.stream.jsonl"))
    run.report(start + 300, ticket)
    run.gate(start + 300, start + 360, ticket, PYTEST)


def in_the_verifier(repo: Path, built_first: list[str]) -> Run:
    """The tickets of `built_first` are built, 600 seconds each, and ticket 70 is in the verifier since 360 seconds."""
    tickets = [*built_first, "70", "71"]
    run = Run(repo, *tickets, run_id="20261016T030000Z-abcd", blocked_by=BLOCKED_BY)
    for index, ticket in enumerate(built_first):
        built(run, ticket, index * 600)
    start = len(built_first) * 600
    specialist_and_gates(run, "70", start)
    verifier_starts(run, start + 360, "70")
    return run


def watch(repo: Path, monkeypatch, run_id: str, *extra: str, out: io.StringIO | None = None, columns: int = 120, rows: int = 24,
          sleep=None) -> tuple[int, str]:
    """Run `delegate watch <run_id>` on a terminal of `columns` and `rows`. A wait that `sleep` does not handle fails the case."""
    out = Terminal() if out is None else out
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    def no_wait(seconds: float) -> None:
        raise AssertionError("watch waited, but the run has no more events")

    fake_clock().on_sleep = sleep or no_wait
    code = delegate.main(["watch", run_id, "--repo", str(repo), *extra])
    return code, out.getvalue()


def ends_at_first_poll(run: Run):
    """A wait that ends the run, so the watch draws the frame of the poll, then the last frame."""

    def sleep(seconds: float) -> None:
        run.add(9000, "run-end", result="failed", built=[], failed=[], skipped=[])

    return sleep


def painted_frames(out: str) -> list[list[str]]:
    """The frames as the terminal gets them, with the paint of each role, and with no other control sequence."""
    return screens(out)


# The frames of the spinner (tokens.md), and its ASCII frames for a locale that is not UTF-8.
SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


# The verifier of ticket 70 starts at 3360 seconds (`in_the_verifier`), and its stream is not there yet, so the time
# since its last activity is the time since its start. The row of 70 is the line 8 of a frame.
VERIFIER_START = 3360


ROW_OF_70 = 8


def watch_waits(tmp_path, monkeypatch, idle: float, waits: int = 1, *extra: str) -> tuple[list[list[str]], list[float], str]:
    """Watch a run whose verifier had no activity for `idle` seconds. The wait number `waits` ends the run.

    Return the frames, the seconds of each wait, and the output as the terminal gets it.
    """
    repo = bare_repo(tmp_path)
    run = in_the_verifier(repo, SEVEN[:5])
    Clock(VERIFIER_START + idle)
    seconds: list[float] = []

    def sleep(wait: float) -> None:
        seconds.append(wait)
        if len(seconds) == waits:
            run.add(9000, "run-end", result="failed", built=[], failed=[], skipped=[])

    code, out = watch(repo, monkeypatch, run.dir.name, *extra, sleep=sleep)
    assert code == 1
    return frames(out), seconds, out


# The cell where each track starts: `#` 3, rail 2, `plan` 6, `state` 10, `phase` 13, `after` 8, `used` 7, `new` 5,
# `cache` 6, and the live tail, with 2 cells between two tracks.
START_CELL: dict[str, int] = {
    "#": 0, "rail": 5, "plan": 9, "state": 17, "phase": 29, "after": 44, "used": 54, "new": 63, "cache": 70, "tail": 78,
}


WIDTH: dict[str, int] = {"#": 3, "used": 7, "new": 5, "cache": 6}


def line_of(**cells: str) -> str:
    """The line that has each text on its design track: a number ends at the last cell of its track, other text starts
    at the first cell. The keyword `rail` is the rail, and `tail` the live tail."""
    line = ""
    for name, text in sorted(cells.items(), key=lambda item: START_CELL[item[0]]):
        cell = START_CELL[name] + WIDTH[name] - len(text) if name in WIDTH else START_CELL[name]
        line = line.ljust(cell) + text
    return line
