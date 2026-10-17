"""The title of the terminal: on a terminal, `delegate` sets the title of the tab with the OSC 2 sequence.

A terminal names a tab by its foreground process when the program sets no title, so a tab that runs `delegate watch`
showed `python3.13` (issue #80). Each case calls `delegate.main(argv)` against a real temporary git repository. The
system boundaries are faked: the terminal (a standard output whose `isatty` is true), the clock (`time.time`), the
time zone (`TZ`), and the wait between two polls of `watch` (`time.sleep`), which appends the next events of a live run.
"""

from __future__ import annotations

import io
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from delegate.cli import delegate

from .support import TITLE, Terminal, bare_repo, journal_path, make_repo, without_titles

START = datetime(2026, 10, 16, 3, 0, 0, tzinfo=timezone.utc)


def titles(out: str) -> list[str]:
    """The titles that the output sets, in order."""
    return TITLE.findall(out)


@pytest.fixture
def builds(scripted):
    """The scripted adapter, which writes `feature.txt` for each ticket, so the gate of the workflow is green."""
    scripted.files_by_ticket = {}
    return scripted


@pytest.fixture
def local_zone(monkeypatch):
    """The local time zone of the process is 13 hours east of UTC: the run starts at 03:00 UTC, 16:00 local time."""
    monkeypatch.setenv("TZ", "NZT-13")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class Clock:
    """The clock of the process, in seconds after START. A case moves it as the run goes on."""

    def __init__(self, monkeypatch, seconds: float) -> None:
        self.seconds = seconds
        monkeypatch.setattr(time, "time", lambda: (START + timedelta(seconds=self.seconds)).timestamp())


class Run:
    """The journal of one run with three tickets, written line by line with the time of each event."""

    def __init__(self, repo: Path, run_id: str = "20261016T030000Z-ab12") -> None:
        self.path = journal_path(repo, run_id)
        self.path.parent.mkdir(parents=True)
        self.seq = 0
        self.add(0, "run-start", run_id=run_id, workflow="/w/workflow.toml", mode="assure", adapter="scripted",
                 base_branch="main", run_branch="run/demo", tickets=["a", "b", "c"])

    def add(self, seconds: float, event: str, **fields: object) -> None:
        self.seq += 1
        with self.path.open("a") as handle:
            time_ = (START + timedelta(seconds=seconds)).isoformat()
            handle.write(json.dumps({"seq": self.seq, "time": time_, "event": event, **fields}) + "\n")

    def step(self, start: float, end: float | None, ticket: str) -> None:
        self.add(start, "step-start", ticket=ticket, stack="python", branch=f"run/demo-{ticket}", worktree=f"/w/{ticket}",
                 base_commit="0" * 40, agent="python-specialist", tier="strong", model="m")
        if end is not None:
            self.add(end, "step-end", ticket=ticket, state="built", reason=None)


def watch(repo: Path, monkeypatch, *extra: str, out: io.StringIO, waits: list) -> int:
    """Run `delegate watch` with standard output `out`. Each wait calls the next function of `waits`."""
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("LINES", "50")

    def wait(seconds: float) -> None:
        assert waits, "watch waited, but the run has no more events"
        waits.pop(0)()

    monkeypatch.setattr(time, "sleep", wait)
    return delegate.main(["watch", "--repo", str(repo), *extra])


@pytest.mark.parametrize("extra", [pytest.param((), id="live-view"), pytest.param(("--raw",), id="raw-lines")])
def test_watch_on_a_terminal_shows_the_progress_and_the_local_eta_in_the_title_and_the_result_at_the_end(
    tmp_path, monkeypatch, local_zone, extra
):
    repo = bare_repo(tmp_path)
    run = Run(repo)
    # Ticket a took 600 seconds, and b runs since 600. At 900: 1 of 3 ended, so 33%. The ETA is
    # 900 + 600 * 2 - 300 = 1800 seconds after the start: 03:30 UTC, 16:30 local time.
    run.step(0, 600, "a")
    run.step(600, None, "b")
    clock = Clock(monkeypatch, 900)

    def nothing_changes() -> None:
        clock.seconds = 950

    def ticket_b_ends() -> None:
        # Ticket b took 900 seconds, and c runs since 1500. At 1600: 66%. The mean is 750, so the ETA is
        # 1600 + 750 - 100 = 2250 seconds after the start: 03:37:30 UTC, 16:37 local time.
        run.add(1500, "step-end", ticket="b", state="built", reason=None)
        run.step(1500, None, "c")
        clock.seconds = 1600

    def the_run_ends() -> None:
        run.add(2000, "step-end", ticket="c", state="built", reason=None)
        run.add(2000, "run-end", result="built", built=["a", "b", "c"], failed=[], skipped=[])

    out = Terminal()
    code = watch(repo, monkeypatch, *extra, out=out, waits=[nothing_changes, ticket_b_ends, the_run_ends])

    assert code == 0
    # A poll whose snapshot gives the same title sets no title again.
    assert titles(out.getvalue()) == [
        "delegate watch · ab12 · 33% · ETA 16:30",
        "delegate watch · ab12 · 66% · ETA 16:37",
        "delegate watch · ab12 · built",
    ]
    # A title is not visible on the terminal, and the output holds no other change.
    assert without_titles(out.getvalue()).splitlines()[-1] == f"position {run.seq}"


@pytest.mark.parametrize(
    ("extra", "result"),
    [
        pytest.param(("--until", "verdict"), "verdict", id="verdict"),
        pytest.param((), "failed", id="run-failed"),
    ],
)
def test_the_last_title_of_watch_names_why_the_watch_ended(tmp_path, monkeypatch, local_zone, extra, result):
    repo = bare_repo(tmp_path)
    run = Run(repo)
    run.step(0, None, "a")
    if result == "verdict":
        run.add(10, "verdict", ticket="a", round=0, mode="full", verdict="ACCEPT", findings=[], unverified=[], report="/r/verdict.json")
    else:
        run.add(10, "run-end", result="failed", built=[], failed=["a"], skipped=[])
    Clock(monkeypatch, 20)

    out = Terminal()
    watch(repo, monkeypatch, *extra, out=out, waits=[])

    assert titles(out.getvalue())[-1] == f"delegate watch · ab12 · {result}"


def test_a_watch_before_the_first_ticket_ends_has_no_eta_yet(tmp_path, monkeypatch, local_zone):
    repo = bare_repo(tmp_path)
    run = Run(repo)
    run.step(0, None, "a")
    Clock(monkeypatch, 60)

    def the_run_ends() -> None:
        run.add(100, "run-end", result="built", built=["a"], failed=[], skipped=[])

    out = Terminal()
    watch(repo, monkeypatch, out=out, waits=[the_run_ends])

    assert titles(out.getvalue()) == ["delegate watch · ab12 · 0% · ETA -", "delegate watch · ab12 · built"]


def test_run_on_a_terminal_names_the_short_run_id_in_the_title(tmp_path, monkeypatch, builds):
    repo = make_repo(tmp_path)
    out = Terminal()
    monkeypatch.setattr(sys, "stdout", out)

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])

    assert code == 0
    run_id = re.search(r"^run (\S+)$", without_titles(out.getvalue()), re.MULTILINE).group(1)
    short = run_id.rsplit("-", 1)[1]
    assert titles(out.getvalue()) == ["delegate run", f"delegate run · {short}"]


@pytest.mark.parametrize(
    ("argv", "title"),
    [
        pytest.param(["status"], "delegate status", id="status"),
        pytest.param(["status", "--json"], "delegate status", id="status-json"),
        pytest.param(["run", "--dry-run"], "delegate run", id="run-dry-run"),
        pytest.param(["validate"], "delegate validate", id="validate"),
    ],
)
def test_every_other_command_on_a_terminal_sets_the_title_delegate_and_the_command(tmp_path, monkeypatch, scripted, argv, title):
    repo = make_repo(tmp_path)
    Run(repo)
    out = Terminal()
    monkeypatch.setattr(sys, "stdout", out)
    paths = {"status": ["--repo", str(repo)], "run": [str(repo / "workflow.toml"), "--repo", str(repo)], "validate": [str(tmp_path / "none")]}

    delegate.main([*argv, *paths[argv[0]]])

    assert titles(out.getvalue()) == [title]


def test_a_title_holds_no_control_character_of_the_run_id(tmp_path, monkeypatch, local_zone):
    """A run id is the name of a directory, so it can hold a BEL, which would end the sequence early."""
    repo = bare_repo(tmp_path)
    run = Run(repo, run_id="r1\x07")
    run.add(10, "run-end", result="built", built=[], failed=[], skipped=[])
    Clock(monkeypatch, 20)

    out = Terminal()
    watch(repo, monkeypatch, "r1\x07", out=out, waits=[])

    assert titles(out.getvalue()) == ["delegate watch · r1 · built"]


def a_watch(repo: Path, monkeypatch, out: io.StringIO) -> None:
    run = Run(repo)
    run.add(10, "run-end", result="built", built=[], failed=[], skipped=[])
    Clock(monkeypatch, 20)
    watch(repo, monkeypatch, out=out, waits=[])


def a_status(repo: Path, monkeypatch, out: io.StringIO) -> None:
    Run(repo)
    monkeypatch.setattr(sys, "stdout", out)
    assert delegate.main(["status", "--repo", str(repo)]) == 0


def a_run(repo: Path, monkeypatch, out: io.StringIO) -> None:
    monkeypatch.setattr(sys, "stdout", out)
    assert delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)]) == 0


@pytest.mark.parametrize("command", [pytest.param(a_watch, id="watch"), pytest.param(a_status, id="status"), pytest.param(a_run, id="run")])
@pytest.mark.parametrize(
    ("terminal", "no_title"),
    [pytest.param(False, None, id="not-a-terminal"), pytest.param(True, "1", id="DELEGATE_NO_TITLE=1")],
)
def test_no_escape_sequence_when_standard_output_is_not_a_terminal_or_the_title_is_off(
    tmp_path, monkeypatch, builds, command, terminal, no_title
):
    repo = make_repo(tmp_path)
    if no_title is not None:
        monkeypatch.setenv("DELEGATE_NO_TITLE", no_title)
    else:
        monkeypatch.delenv("DELEGATE_NO_TITLE", raising=False)
    out = Terminal() if terminal else io.StringIO()

    command(repo, monkeypatch, out)

    assert out.getvalue(), "the command printed nothing"
    assert "\x1b]" not in out.getvalue()
    assert "\x07" not in out.getvalue()
