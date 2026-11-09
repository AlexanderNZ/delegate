"""The live view of `delegate watch` writes only the lines that changed (#282).

Over SSH each write is slow, so a draw of the same frame writes nothing, and a draw of a frame that has the same size of
terminal as the frame before writes only the lines that changed: for each, the sequence that moves the cursor to the line,
the line, and the sequence that clears to the end of the line. The cursor then goes to the line after the frame. A frame with
fewer lines clears the screen below its last line. A new size of the terminal, and the first frame, draw the full frame.
Each write is one synchronized update.

A case writes the journal of a run in the state directory of a real temporary git repository and calls
`delegate.main(["watch", ...])`, with the system boundaries faked as in `test_watch_liveness`: the terminal, the clock, the
time zone and the wait between two polls. The case reads the output that the terminal gets, one synchronized update at a time.
"""

from __future__ import annotations

import os
import re
import shutil

from .support import CONTROL, bare_repo, without_titles
from .test_watch import twelve_hours_east  # noqa: F401 - a fixture
from .test_watch_frame import SEVEN, Clock, in_the_verifier, terminal_of_a_person, watch  # noqa: F401 - a fixture
from .test_watch_liveness import ROW_OF_70, SPINNER, VERIFIER_START, watch_waits

SYNC_BEGIN = "\x1b[?2026h"
SYNC_END = "\x1b[?2026l"
HOME = "\x1b[H"
CLEAR_SCREEN = "\x1b[2J"
CLEAR_LINE_END = "\x1b[K"
CLEAR_DOWN = "\x1b[J"


def updates(out: str) -> list[str]:
    """The synchronized updates that the live view wrote, each from after its begin sequence to its end sequence."""
    return [update.split(SYNC_END)[0] for update in without_titles(out).split(SYNC_BEGIN)[1:]]


def rows_written(update: str) -> list[int]:
    """The lines that an update moves the cursor to, as the numbers of the lines of the terminal, the first is 1."""
    return [int(row) for row in re.findall(r"\x1b\[(\d+);1H", update)]


def test_a_draw_where_only_the_spinner_changed_writes_the_two_lines_of_the_spinner_and_not_the_frame(tmp_path, monkeypatch):
    _, _, out = watch_waits(tmp_path, monkeypatch, idle=20)

    drawn = updates(out)
    first, redraw = drawn[0], drawn[1]
    # The frame has 19 lines. The spinner is on the row of ticket 70 and on the waiting line, the last line of the frame.
    assert first.count(CLEAR_LINE_END) == 19
    # Each line is moved to, written and cleared to its end; the last move puts the cursor under the frame.
    assert rows_written(redraw) == [ROW_OF_70 + 1, 19, 20]
    assert redraw.count(CLEAR_LINE_END) == 2 and CLEAR_DOWN not in redraw and HOME not in redraw
    assert [cell for cell in CONTROL.sub("", redraw) if cell in "".join(SPINNER)] == [SPINNER[1]] * 2
    assert len(redraw) < len(first) / 4


def test_a_draw_of_the_frame_that_was_drawn_before_writes_nothing(tmp_path, monkeypatch):
    _, seconds, out = watch_waits(tmp_path, monkeypatch, idle=45, waits=2)

    # Two reads of the journal and the last frame: the second read draws the frame of the first, so it writes nothing.
    assert seconds == [1.0, 1.0]
    assert len(updates(out)) == 2


def test_a_new_size_of_the_terminal_draws_the_full_frame_with_no_other_change(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = in_the_verifier(repo, SEVEN[:5])
    Clock(monkeypatch, VERIFIER_START + 45)  # quiet: no spinner, so the frame is the same at each poll
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:
            monkeypatch.setenv("LINES", "30")
        else:
            run.add(9000, "run-end", result="failed", built=[], failed=[], skipped=[])

    code, out = watch(repo, monkeypatch, run.dir.name, sleep=sleep)

    assert code == 1
    drawn = updates(out)
    # The first frame, the same frame on a terminal of 30 lines, and the last frame.
    assert len(drawn) == 3
    assert drawn[1].startswith(HOME) and CLEAR_SCREEN not in drawn[1]
    assert drawn[1].count(CLEAR_LINE_END) == 19 and drawn[1].endswith(CLEAR_DOWN + "\n")
    assert drawn[1].removeprefix(HOME) == drawn[0].split(HOME, 1)[1]


def test_a_frame_with_fewer_lines_than_the_frame_before_clears_the_screen_below_its_last_line(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = in_the_verifier(repo, SEVEN[:5])
    Clock(monkeypatch, VERIFIER_START + 45)

    def sleep(seconds: float) -> None:
        # The step fails: its three sub-rows go, and two lines join the feed.
        run.add(9000, "step-end", ticket="70", state="failed", reason="crashed: OSError: disk full")
        run.add(9000, "run-end", result="failed", built=[], failed=["70"], skipped=[])

    code, out = watch(repo, monkeypatch, run.dir.name, sleep=sleep)

    assert code == 1
    first, last = updates(out)
    assert first.count(CLEAR_LINE_END) == 19
    # The last frame has 17 lines. The cursor goes to the line after them, and the screen below is cleared.
    assert rows_written(last)[-1] == 18
    assert last.endswith("\x1b[18;1H" + CLEAR_DOWN)
    assert last.count(CLEAR_LINE_END) <= 17 and HOME not in last


def test_a_terminal_that_gives_no_height_draws_the_full_frame_at_each_draw(tmp_path, monkeypatch):
    # With no height, the lines of the terminal are not known, so the frame may be taller than the terminal: no line has a place.
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback=(80, 0): os.terminal_size((120, 0)))

    _, _, out = watch_waits(tmp_path, monkeypatch, idle=20)

    drawn = updates(out)
    assert len(drawn) == 9  # the poll, seven steps of the spinner, and the last frame
    assert all(update.startswith((HOME, CLEAR_SCREEN)) for update in drawn) and not any(rows_written(update) for update in drawn)
