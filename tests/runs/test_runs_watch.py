"""`delegate runs --watch` redraws the view in place (#313): the frames W8, W9 and E15–E22 of the design, on a terminal.

The spec is the ticket: the frame replaces itself at the poll rate of `delegate watch`, fits the terminal and follows a new
size of it, has no spinner, ends with Ctrl-C (130 and `delegate: interrupted`), needs a standard output that is a terminal
(exit 2), and reads each journal again only from its last position. A case writes the journals of runs with
`journals.write_run`, calls `main(argv)` with a `Terminal` as standard output, and gives the wait between two redraws a
function (`fake_clock().on_sleep`) that changes a journal, the terminal, or ends the command with Ctrl-C. The case reads
the screen that the terminal shows after each synchronized update.
"""

from __future__ import annotations

import builtins
import io
import json
import sys
from pathlib import Path

import pytest

from delegate.cli import delegate

from ..support import Terminal, fake_clock, final_screen, frames, without_titles
from .journals import built, clock_time, run_end, runs, working, write_run

SHOW_CURSOR = "\x1b[?25h"
INTERRUPTED = 130


def ctrl_c() -> None:
    raise KeyboardInterrupt


def watching(monkeypatch, capsys, *argv: str, columns: int = 80, lines: int = 1000, out: Terminal | None = None) -> tuple[int, str, str]:
    """Run `delegate runs --watch` on a terminal. Return the exit code, what the terminal got, and stderr."""
    terminal = Terminal() if out is None else out
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(lines))
    capsys.readouterr()
    try:
        code = delegate.main(["runs", "--watch", *argv])
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C left `delegate runs --watch` as a KeyboardInterrupt, which prints a traceback")
    return code, terminal.getvalue(), capsys.readouterr().err


def append(journal: Path, name: str, **fields: object) -> None:
    """Append one event to a journal, as the engine does."""
    seq = len(journal.read_text().splitlines()) + 1
    with journal.open("a") as handle:
        handle.write(json.dumps({"seq": seq, "time": clock_time(14, 15).isoformat(), "event": name, **fields}) + "\n")


def cells_of(frame: list[str], short: str) -> list[str]:
    """The cells of the row of the run with this short id in a frame."""
    (line,) = [line for line in frame if f" {short} " in line]
    return line.split()


def a_working_run(root) -> Path:
    return write_run(root, "delegate", "3c98", clock_time(13, 0), [working("1", clock_time(14, 0))], lock=True)


def test_a_new_event_in_a_journal_shows_in_the_second_redraw(machine, monkeypatch, capsys):
    journal = a_working_run(machine)
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:
            append(journal, "step-end", ticket="1", state="built", reason=None)  # ticket 1 of 2 ends
        else:
            ctrl_c()

    fake_clock().on_sleep = sleep

    code, out, err = watching(monkeypatch, capsys)

    first, second = frames(out)
    assert "0/2" in cells_of(first, "3c98") and "1/2" in cells_of(second, "3c98")
    assert "1/2" not in cells_of(first, "3c98") and "0/2" not in cells_of(second, "3c98")
    assert waits == [1.0, 1.0]  # the poll rate of `delegate watch`
    assert (code, err) == (INTERRUPTED, "delegate: interrupted\n")


def test_a_redraw_with_no_change_writes_no_new_frame(machine, monkeypatch, capsys):
    a_working_run(machine)
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 3:
            ctrl_c()

    fake_clock().on_sleep = sleep

    _, out, _ = watching(monkeypatch, capsys)

    # Three draws of one frame: the view has no spinner, so the screen changes once.
    assert len(waits) == 3 and len(frames(out)) == 1


def test_a_run_that_appears_between_two_redraws_gets_a_row(machine, monkeypatch, capsys):
    a_working_run(machine)
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:
            write_run(machine, "web-app", "1195", clock_time(13, 5), [working("1", clock_time(14, 0))], lock=True)
        else:
            ctrl_c()

    fake_clock().on_sleep = sleep

    _, out, _ = watching(monkeypatch, capsys)

    first, second = frames(out)
    assert not any(" 1195 " in line for line in first) and cells_of(second, "1195")[:2] == ["│", "web-app"]


def test_a_new_width_of_the_terminal_changes_the_columns_of_the_next_redraw(machine, monkeypatch, capsys):
    write_run(machine, "delegate", "3c98", clock_time(13, 0), [working("1", clock_time(14, 0))], lock=True)
    write_run(machine, "web-app", "1195", clock_time(13, 1), [working("1", clock_time(14, 0))], lock=True)
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:
            monkeypatch.setenv("COLUMNS", "60")  # the table is 95 cells wide with each column: `new` and `cache` drop at 94 or less
        else:
            ctrl_c()

    fake_clock().on_sleep = sleep

    _, out, _ = watching(monkeypatch, capsys, columns=100)

    first, second = frames(out)
    header = lambda frame: [line for line in frame if line.startswith("host")][0].split()  # noqa: E731
    assert "cache" in header(first) and "cache" not in header(second)
    assert all(len(line) <= 60 for line in second)


def test_a_redraw_reads_each_journal_from_its_last_position_and_not_from_the_start(machine, monkeypatch, capsys):
    journal = a_working_run(machine)
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:
            # The first line of the journal gets a wrong `seq` of the same length. A read from the start refuses that line.
            data = journal.read_bytes()
            assert data.startswith(b'{"seq": 1,')
            journal.write_bytes(b'{"seq": 9,' + data[len(b'{"seq": 1,'):])
            append(journal, "step-end", ticket="1", state="built", reason=None)
        else:
            ctrl_c()

    fake_clock().on_sleep = sleep

    _, out, err = watching(monkeypatch, capsys)

    _, second = frames(out)
    assert "1/2" in cells_of(second, "3c98")  # the new event is read, and the old lines are not read again
    assert err == "delegate: interrupted\n"  # no line `skipped the journal`


def test_ctrl_c_shows_the_cursor_leaves_the_last_frame_and_exits_130_with_one_line(machine, monkeypatch, capsys):
    a_working_run(machine)
    fake_clock().on_sleep = lambda seconds: ctrl_c()

    code, out, err = watching(monkeypatch, capsys)

    assert code == INTERRUPTED
    assert err == "delegate: interrupted\n"
    assert without_titles(out).endswith(SHOW_CURSOR)
    assert any(" 3c98 " in line for line in final_screen(out))  # the last frame stays on the screen
    assert "\x1b[?1049h" not in out  # no alternate screen


def test_watch_with_standard_output_that_is_not_a_terminal_exits_2_with_one_line_on_stderr(machine, capsys):
    a_working_run(machine)
    fake_clock().on_sleep = lambda seconds: pytest.fail("the command waited with no terminal")

    code = delegate.main(["runs", "--watch"])

    out, err = capsys.readouterr()
    assert code == 2 and out == ""
    assert err == "delegate runs: --watch needs a terminal\n"


def test_watch_and_json_cannot_be_used_together(machine, monkeypatch, capsys):
    with pytest.raises(SystemExit) as stop:
        watching(monkeypatch, capsys, "--json")

    assert stop.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_a_journal_that_cannot_be_read_is_named_on_stderr_once_before_the_first_frame(machine, monkeypatch, capsys):
    a_working_run(machine)
    bad = write_run(machine, "web-app", "1195", clock_time(13, 5), [working("1", clock_time(14, 0))])
    bad.write_text(bad.read_text() + "not json\n")
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 3:
            ctrl_c()

    fake_clock().on_sleep = sleep

    _, out, err = watching(monkeypatch, capsys)

    assert err.count("delegate runs: skipped the journal") == 1 and str(bad) in err
    assert err.endswith("delegate: interrupted\n")
    assert [line for line in frames(out)[0] if " 3c98 " in line] and not any(" 1195 " in line for line in frames(out)[0])


def test_a_journal_that_breaks_during_the_watch_is_named_on_stderr_after_the_last_frame(machine, monkeypatch, capsys):
    journal = a_working_run(machine)
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:
            journal.write_text(journal.read_text() + "not json\n")
        elif len(waits) == 3:
            ctrl_c()

    fake_clock().on_sleep = sleep

    _, out, err = watching(monkeypatch, capsys)

    # The row goes at the redraw after the break, and the line of stderr comes once, after the frames, before the line of Ctrl-C.
    assert [len(frames(out)), err.count("skipped the journal")] == [2, 1]
    assert err.startswith("delegate runs: skipped the journal") and err.endswith("delegate: interrupted\n")
    assert not any(" 3c98 " in line for line in final_screen(out))


def test_timeline_with_watch_redraws_the_lanes_in_place(machine, monkeypatch, capsys):
    """A ticket that ends between two redraws shows as one more `●` in the lane of the second frame (#314)."""
    journal = a_working_run(machine)
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:
            append(journal, "step-end", ticket="1", state="built", reason=None)
        else:
            ctrl_c()

    fake_clock().on_sleep = sleep

    code, out, err = watching(monkeypatch, capsys, "--timeline", columns=120)

    first, second = frames(out)
    lane = next(line for line in first if " 3c98 " in line)
    assert "▶" in lane and "●" not in lane
    assert sum(line.count("●") for line in second if " 3c98 " in line) == 1
    assert any(line.startswith("last 6h, to the latest ETA") for line in second)
    assert (code, err) == (INTERRUPTED, "delegate: interrupted\n")


class OpenedFiles:
    """The files that the code opens through `open`, `io.open` and so `Path.open`, `Path.read_text` and `Path.read_bytes`: the boundary of the file system."""

    def __init__(self, monkeypatch) -> None:
        self.paths: list[str] = []
        real = io.open

        def spy(file, *args, **kwargs):
            self.paths.append(str(file))
            return real(file, *args, **kwargs)

        monkeypatch.setattr(io, "open", spy)
        monkeypatch.setattr(builtins, "open", spy)

    def of_kind(self, name: str) -> list[str]:
        return [path for path in self.paths if path.endswith(name)]


def test_a_redraw_with_no_change_opens_no_journal_and_no_record_file(machine, monkeypatch, capsys):
    a_working_run(machine)
    write_run(machine, "web-app", "1195", clock_time(10, 0), [*built("1", clock_time(10, 5), clock_time(10, 20)), run_end(clock_time(10, 30))], tickets=("1",))
    write_run(machine, "old-app", "0a0a", clock_time(10, 0, days_back=3), [run_end(clock_time(10, 30, days_back=3))], tickets=("1",))
    assert runs(capsys)[0] == 0  # writes the records of the two listed runs, so the first redraw of the watch reads them
    opened = OpenedFiles(monkeypatch)
    seen: list[list[str]] = []
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        seen.append(list(opened.paths))  # the files opened up to the end of this redraw
        if len(waits) == 3:
            ctrl_c()

    fake_clock().on_sleep = sleep

    watching(monkeypatch, capsys)

    first, _, third = seen
    assert len(opened.of_kind("journal.jsonl")) >= 3 and len(opened.of_kind(".json")) >= 2  # the first redraw reads them: the count is a count
    assert [path for path in third[len(first):] if path.endswith(("journal.jsonl", ".json"))] == []  # the second and the third redraw read none


def test_a_view_reads_each_record_file_of_the_index_once(machine, monkeypatch, capsys):
    a_working_run(machine)
    write_run(machine, "web-app", "1195", clock_time(10, 0), [*built("1", clock_time(10, 5), clock_time(10, 20)), run_end(clock_time(10, 30))], tickets=("1",))
    code, _, _ = runs(capsys)  # writes the two records
    records = sorted((machine / "index").glob("*.json"))
    assert code == 0 and len(records) == 2
    opened = OpenedFiles(monkeypatch)

    code, _, _ = runs(capsys)

    assert code == 0
    assert sorted(opened.of_kind(".json")) == sorted(str(path) for path in records)  # one listing: two listings read each record twice
