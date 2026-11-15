"""`delegate watch` on a terminal makes one snapshot for each poll, and the spinner repaints only its own cells (#351).

The cost of a frame is a count, not a time, so no case is timed. A case counts the snapshots that a watch makes, and the
tables that it composes, with a spy on the public function of each, and the system boundaries are faked as in
`tests/test_watch_liveness.py`: the terminal, the clock, the time zone, the locale and the wait between two polls.

- Before the change, one poll made two snapshots: one for the frame and one for the title.
- Before the change, each of the 7 spins of the wait between two polls composed the table again.
"""

from __future__ import annotations

import re

import pytest

from delegate.run import status, status_table

from ..support import CONTROL, bare_repo, without_titles
from ..support_watch_frame import SEVEN, Clock, in_the_verifier, terminal_of_a_person, watch, ROW_OF_70, SPINNER, VERIFIER_START  # noqa: F401 - a fixture

# The commands of each case use the fake clock of the case (#297): its helpers set it.
pytestmark = pytest.mark.usefixtures("clock")

SYNC_BEGIN = "\x1b[?2026h"
SYNC_END = "\x1b[?2026l"
TITLE = re.compile("\x1b\\]2;([^\x07]*)\x07")


def counted(monkeypatch, module, name: str) -> list[int]:
    """A list that gets one item at each call of `module.name`, which still does its work."""
    calls: list[int] = []
    real = getattr(module, name)

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, spy)
    return calls


def watch_polls(tmp_path, monkeypatch, idle: float, polls: int) -> tuple[list[float], str]:
    """Watch a run whose verifier had no activity for `idle` seconds, until the wait number `polls` ends the run."""
    repo = bare_repo(tmp_path)
    run = in_the_verifier(repo, SEVEN[:5])
    Clock(VERIFIER_START + idle)
    seconds: list[float] = []

    def sleep(wait: float) -> None:
        seconds.append(wait)
        if len(seconds) == polls:
            run.add(9000, "run-end", result="failed", built=[], failed=[], skipped=[])

    code, out = watch(repo, monkeypatch, run.dir.name, sleep=sleep)
    assert code == 1
    return seconds, out


def test_a_poll_makes_one_snapshot_for_the_frame_and_the_title(tmp_path, monkeypatch):
    snapshots = counted(monkeypatch, status, "snapshot_of")

    seconds, out = watch_polls(tmp_path, monkeypatch, idle=45, polls=3)

    # Quiet agent: no spinner, so each wait is one second. Three polls (the third wait ends the run): each makes one
    # snapshot for the frame and the title. The `run-end` line of the feed makes one, and the last frame makes one.
    assert seconds == [1.0] * 3
    assert len(snapshots) == 3 + 1 + 1
    # The title still has the percentage and the ETA of the run.
    titles = TITLE.findall(out)
    assert titles and re.fullmatch(r"delegate watch · \S+ · \d+% · ETA \S+", titles[0])


def test_a_spin_between_two_polls_does_not_compose_the_table_again(tmp_path, monkeypatch):
    tables = counted(monkeypatch, status_table, "view_lines")
    snapshots = counted(monkeypatch, status, "snapshot_of")

    seconds, _ = watch_polls(tmp_path, monkeypatch, idle=20, polls=8)

    assert seconds == [0.125] * 8 and sum(seconds) == 1
    # The poll composes the table once, and the last frame once: the 7 spins of the wait compose nothing. The snapshots are
    # the one of the poll, the one of the `run-end` line of the feed, and the one of the last frame.
    assert len(tables) == 2
    assert len(snapshots) == 1 + 1 + 1


def test_a_spin_writes_only_the_lines_of_the_spinner_with_the_next_frame_of_the_spinner(tmp_path, monkeypatch):
    _, out = watch_polls(tmp_path, monkeypatch, idle=20, polls=8)

    updates = [update.split(SYNC_END)[0] for update in without_titles(out).split(SYNC_BEGIN)[1:]]
    # The poll draws the frame, then 7 spins draw (the last part of the wait has no draw), then the last frame.
    assert len(updates) == 9
    spins = updates[1:8]
    for number, update in enumerate(spins, start=1):
        # The row of ticket 70 and the waiting line (the last line of the frame), and the move that puts the cursor under the frame.
        assert [int(row) for row in re.findall(r"\x1b\[(\d+);1H", update)] == [ROW_OF_70 + 1, 19, 20]
        assert [cell for cell in CONTROL.sub("", update) if cell in "".join(SPINNER)] == [SPINNER[number % len(SPINNER)]] * 2
