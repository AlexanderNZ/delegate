"""The liveness of an agent in the live view of `delegate watch`, and `active <time> ago` in a snapshot (#147).

`active 2s` read as a duration ("active for 2 seconds"), but it is the time since the last activity of the agent. So
the live view on a terminal shows a sign of life in three states, by the seconds since the last activity of the agent:

- under 30 seconds, a spinner in `now`, which steps at each redraw of the frame;
- from 30 seconds to the stall time, `quiet <time>` in `dim`, with no animation;
- past the stall time, `⚠ idle <time>` in `warn`, as before.

Between two reads of the journal, the frame draws the spinner again from the snapshot that it has: the wait between two
reads is the same. With `NO_COLOR`, there is no animation, and the line shows the text of a snapshot:
`active <time> ago`. The run picker of `watch` and of `delegate status` is a snapshot, so it shows that text too.

A case writes the journal of a run in the state directory of a real temporary git repository and calls
`delegate.main([...])`. The system boundaries are faked, as in `test_watch_frame`: the terminal, the clock
(`time.time`), the time zone, the locale, `NO_COLOR`, and the wait between two polls (`time.sleep`).
"""

from __future__ import annotations

import os
import re
import time

import pytest

from delegate.cli import delegate

from .support import bare_repo, frames, live_lock
from .test_watch import twelve_hours_east  # noqa: F401 - a fixture
from .test_run_picker import Keyboard, ends_at_the_first_wait, on_a_terminal
from .test_watch_active import two_active_runs
from .test_watch_frame import SEVEN, Clock, in_the_verifier, watch

# The frames of the spinner (tokens.md), and its ASCII frames for a locale that is not UTF-8.
SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
ASCII_SPINNER = ["|", "/", "-", "\\"]

# The verifier of ticket 70 starts at 3360 seconds (`in_the_verifier`), and its stream is not there yet, so the time
# since its last activity is the time since its start. The row of 70 is the line 8 of a frame.
VERIFIER_START = 3360
ROW_OF_70 = 8

# An SGR sequence: the paint of a role.
SGR = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def terminal_of_a_person(monkeypatch):
    """A UTF-8 locale, colour on, and the local time zone 13 hours east of UTC."""
    monkeypatch.setenv("LC_ALL", "en_NZ.UTF-8")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TZ", "NZT-13")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def watch_waits(tmp_path, monkeypatch, idle: float, waits: int = 1, *extra: str) -> tuple[list[list[str]], list[float], str]:
    """Watch a run whose verifier had no activity for `idle` seconds. The wait number `waits` ends the run.

    Return the frames, the seconds of each wait, and the output as the terminal gets it.
    """
    repo = bare_repo(tmp_path)
    run = in_the_verifier(repo, SEVEN[:5])
    Clock(monkeypatch, VERIFIER_START + idle)
    seconds: list[float] = []

    def sleep(wait: float) -> None:
        seconds.append(wait)
        if len(seconds) == waits:
            run.add(9000, "run-end", result="failed", built=[], failed=[], skipped=[])

    code, out = watch(repo, monkeypatch, run.dir.name, *extra, sleep=sleep)
    assert code == 1
    return frames(out), seconds, out


def tail(line: str) -> str:
    """The last cell of a line: the text after its last run of 2 spaces, or after its last ` · `."""
    return re.split(r" {2,}| · ", line)[-1]


def test_under_30_seconds_the_live_tail_and_the_waiting_line_show_a_spinner_that_steps_at_each_redraw(tmp_path, monkeypatch):
    drawn, seconds, out = watch_waits(tmp_path, monkeypatch, idle=20)

    # One poll: the frame of the poll, a redraw after each step of the wait but the last, and the last frame.
    assert len(drawn) == 9
    spinning = drawn[:-1]
    assert [tail(frame[ROW_OF_70]) for frame in spinning] == SPINNER[:8]
    assert [tail(frame[-1]) for frame in spinning] == SPINNER[:8]
    assert spinning[0][-1] == "16:56  #70  verifier running · 20s · 0 new · 0 cache · ⠋"
    # The redraws change only the spinner cells: the other lines are the lines of the poll.
    assert all(frame[:ROW_OF_70] == spinning[0][:ROW_OF_70] and frame[ROW_OF_70 + 1:-1] == spinning[0][ROW_OF_70 + 1:-1] for frame in spinning)
    # The spinner is `now`, and the wait between two reads of the journal stays 1 second.
    assert "\x1b[36m⠋\x1b[0m" in out
    assert seconds == [0.125] * 8 and sum(seconds) == 1


def test_from_30_seconds_the_live_tail_and_the_waiting_line_show_quiet_in_dim_and_do_not_move(tmp_path, monkeypatch):
    drawn, seconds, out = watch_waits(tmp_path, monkeypatch, idle=45, waits=2)

    # Two polls and the last frame: no redraw between two polls.
    assert len(drawn) == 3
    assert drawn[0] == drawn[1]
    assert drawn[0][ROW_OF_70].endswith("  quiet 45s")
    assert drawn[0][-1] == "16:56  #70  verifier running · 45s · 0 new · 0 cache · quiet 45s"
    assert "\x1b[2mquiet 45s\x1b[0m" in out
    assert not set(SPINNER) & set(out)
    assert seconds == [1.0, 1.0]


def test_past_the_stall_time_the_live_tail_shows_the_idle_warning_and_does_not_move(tmp_path, monkeypatch):
    drawn, seconds, out = watch_waits(tmp_path, monkeypatch, idle=400, waits=2)

    assert len(drawn) == 3
    assert drawn[0] == drawn[1]
    assert drawn[0][ROW_OF_70].endswith("  ⚠ idle 6m40s")
    assert drawn[0][-1].endswith(" · ⚠ idle 6m40s")
    assert "\x1b[33m⚠ idle 6m40s\x1b[0m" in out
    assert not set(SPINNER) & set(out)
    assert seconds == [1.0, 1.0]


def test_within_the_stall_minutes_an_agent_quiet_for_6m40s_shows_quiet(tmp_path, monkeypatch):
    drawn, _, _ = watch_waits(tmp_path, monkeypatch, 400, 1, "--stall-minutes", "10")

    assert drawn[0][ROW_OF_70].endswith("  quiet 6m40s")
    assert drawn[0][-1].endswith(" · quiet 6m40s")


def test_with_a_locale_that_is_not_utf8_the_spinner_has_the_ascii_frames(tmp_path, monkeypatch):
    monkeypatch.setenv("LC_ALL", "C")

    drawn, _, _ = watch_waits(tmp_path, monkeypatch, idle=20)

    assert [tail(frame[ROW_OF_70]) for frame in drawn[:-1]] == ASCII_SPINNER * 2


def test_with_no_color_there_is_no_spinner_and_the_line_reads_active_ago(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")

    drawn, seconds, out = watch_waits(tmp_path, monkeypatch, idle=20, waits=2)

    assert len(drawn) == 3
    assert drawn[0] == drawn[1]
    assert drawn[0][ROW_OF_70].endswith("  active 20s ago")
    assert drawn[0][-1] == "16:56  #70  verifier running · 20s · 0 new · 0 cache · active 20s ago"
    assert not set(SPINNER) & set(out)
    assert seconds == [1.0, 1.0]


def test_with_no_color_an_agent_quiet_for_45s_reads_active_45s_ago(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")

    drawn, _, _ = watch_waits(tmp_path, monkeypatch, idle=45)

    assert drawn[0][ROW_OF_70].endswith("  active 45s ago")


def two_runs_active_2_seconds_ago(repo, monkeypatch):
    """Two active runs whose journals changed 2 seconds before the clock of the case."""
    first, second = two_active_runs(repo)
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    for journal in (first, second):
        os.utime(journal, (now - 2, now - 2))
    return first, second


def test_status_with_two_active_runs_reads_active_2s_ago(tmp_path, monkeypatch, capsys):
    repo = bare_repo(tmp_path)
    first, second = two_runs_active_2_seconds_ago(repo, monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code = delegate.main(["status", "--repo", str(repo)])
    shown = capsys.readouterr().out

    assert code == 0
    rows = shown.splitlines()[2:]
    assert [row.split()[1] for row in rows] == ["r-economy", "r-assure"]
    assert all(row.endswith(" specialist · active 2s ago") for row in rows), rows


def test_the_run_picker_of_watch_reads_active_2s_ago_in_now(tmp_path, monkeypatch, twelve_hours_east):  # noqa: F811 - the fixture
    repo = bare_repo(tmp_path)
    first, second = two_runs_active_2_seconds_ago(repo, monkeypatch)
    ends_at_the_first_wait(monkeypatch, (first, "b"), (second, "x"))

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code, out, _ = on_a_terminal(repo, monkeypatch, Keyboard("\n"))

    assert code == 0
    picker = out.split("watch which? [1] ")[0].splitlines()
    assert [SGR.sub("", row).endswith(" specialist · active 2s ago") for row in picker[2:]] == [True, True]
    assert "\x1b[36mactive 2s ago\x1b[0m" in picker[2]
