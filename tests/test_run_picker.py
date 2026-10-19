"""The run picker of frame 2b (#118): `delegate watch` and `delegate status` with no run id and more than one active run.

The spec is `design/design_handoff_delegate_terminal_output/docs/components.md` (Run picker) and `docs/copy-deck.md`
(several active runs). On a terminal, `watch` prints `<n> runs are active in this repository`, a table of the active
runs with the newest first, and the prompt `watch which? [1]`. It reads one line, and Enter takes 1. Then it follows the
run that the reader chose. When standard input or standard output is not a terminal, `watch` prints one line on stderr
that names each active run, and exits 2. `status` prints the same table, with no prompt.

A case calls `delegate.main([...])` against a real temporary git repository, with journals and locks that the engine
classes write. The system boundaries are faked: the terminal (a standard input and a standard output whose `isatty` is
true), the time zone, the locale, `NO_COLOR`, and the wait of the poll loop.
"""

import io
import sys
import time

import pytest

from delegate.cli import delegate

from .support import (
    Terminal, append_events, bare_repo, frames, live_lock, run_end, run_start, step_end, step_start, without_titles,
    write_journal,
)
from .test_watch import twelve_hours_east  # noqa: F401 - a fixture
from .test_watch_active import POLL, local_start, two_active_runs

# The prompt of the picker. The reader types the answer after it, on the same line.
PROMPT = "watch which? [1] "


class Keyboard(io.StringIO):
    """A standard input that is a terminal. Each line of `typed` is one answer of the reader."""

    def isatty(self) -> bool:
        return True


class CtrlC(Keyboard):
    """A terminal on which the reader types Ctrl-C at the prompt."""

    def readline(self, *args) -> str:
        raise KeyboardInterrupt


class Unread(Keyboard):
    """A terminal that a case must not read from: `watch` with one active run and `status` ask nothing."""

    def readline(self, *args) -> str:
        raise AssertionError("the command read standard input")


def since(path, offset) -> str:
    """The `since` cell of a run that started today: the local time of its `run-start` event, as `HH:MM`."""
    return local_start(path, offset)[-5:]


def ends_at_the_first_wait(monkeypatch, *journals):
    """At the first wait of the poll loop, each journal gets the end of its running ticket and a `run-end`."""
    done: list[bool] = []

    def wait(seconds: float) -> None:
        if not done:
            done.append(True)
            for journal, ticket in journals:
                append_events(journal, step_end(ticket), run_end("built", [ticket]))

    monkeypatch.setattr(time, "sleep", wait)


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


def test_on_a_terminal_watch_prints_the_active_runs_newest_first_and_the_prompt_and_enter_follows_the_newest(
    tmp_path, monkeypatch, twelve_hours_east,  # noqa: F811 - the fixture
):
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)
    ends_at_the_first_wait(monkeypatch, (first, "b"), (second, "x"))
    monkeypatch.setenv("NO_COLOR", "1")

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code, out, err = on_a_terminal(repo, monkeypatch, Keyboard("\n"), "--raw")

    picker, followed = out.split(PROMPT)
    assert picker.splitlines() == [
        "2 runs are active in this repository",
        "#  run        run branch  mode     since  progress         now",
        f"1  r-economy  run/two     economy  {since(second, twelve_hours_east)}  0/1 ended · 0%   ▶ x specialist · active 2h00m ago",
        f"2  r-assure   run/one     assure   {since(first, twelve_hours_east)}  1/2 ended · 50%  ▶ b specialist · active 2h00m ago",
    ]
    # Enter takes 1: the newest run. `watch` follows it to its end.
    assert (code, err) == (0, "")
    assert "run_id=r-economy" in followed and "run_id=r-assure" not in followed
    assert followed.splitlines()[-1] == "position 4"


def test_on_a_terminal_the_answer_2_follows_the_second_run_in_the_live_view(
    tmp_path, monkeypatch, twelve_hours_east,  # noqa: F811 - the fixture
):
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)
    ends_at_the_first_wait(monkeypatch, (first, "b"), (second, "x"))

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code, out, err = on_a_terminal(repo, monkeypatch, Keyboard("2\n"))

    assert (code, err) == (0, "")
    assert out.count(PROMPT) == 1
    drawn = frames(out)
    assert drawn and all(frame[0].startswith("run assure  ") for frame in drawn)
    assert out.splitlines()[-1] == "position 6"


def test_on_a_terminal_watch_from_a_position_shows_the_picker_and_goes_on_from_the_position_in_the_chosen_run(
    tmp_path, monkeypatch, twelve_hours_east,  # noqa: F811 - the fixture
):
    # #137: with `--from` and no run id, `watch` chooses the run as with no `--from`.
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)
    ends_at_the_first_wait(monkeypatch, (first, "b"), (second, "x"))
    monkeypatch.setenv("NO_COLOR", "1")

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code, out, err = on_a_terminal(repo, monkeypatch, Keyboard("2\n"), "--raw", "--from", "3")

    picker, followed = out.split(PROMPT)
    assert picker.splitlines()[0] == "2 runs are active in this repository"
    # The events of r-assure after position 3: ticket b starts and ends, and the run is built.
    assert (code, err) == (0, "")
    assert [line.split()[2] for line in followed.splitlines()[:3]] == ["step-start", "step-end", "run-end"]
    assert "ticket=b" in followed.splitlines()[0] and "ticket=x" not in followed
    assert followed.splitlines()[3:] == ["position 6"]


def test_on_a_terminal_the_short_id_is_bold_and_the_header_is_faint(tmp_path, monkeypatch, twelve_hours_east):  # noqa: F811
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)
    ends_at_the_first_wait(monkeypatch, (first, "b"), (second, "x"))

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        _, out, _ = on_a_terminal(repo, monkeypatch, Keyboard("\n"), "--raw")

    heading, header, newest, older = out.split(PROMPT)[0].splitlines()
    assert "\x1b" not in heading
    assert header.startswith("\x1b[2m#\x1b[0m  \x1b[2mrun\x1b[0m")
    assert newest.startswith("\x1b[2m1\x1b[0m  r-\x1b[1meconomy\x1b[0m  run/two  ")
    assert "r-\x1b[1massure\x1b[0m" in older


def test_an_answer_that_names_no_row_asks_again(tmp_path, monkeypatch, twelve_hours_east):  # noqa: F811 - the fixture
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)
    ends_at_the_first_wait(monkeypatch, (first, "b"), (second, "x"))
    monkeypatch.setenv("NO_COLOR", "1")

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code, out, err = on_a_terminal(repo, monkeypatch, Keyboard("3\nr-assure\n 2 \n"), "--raw")

    assert out.count(PROMPT) == 3
    assert err == (
        "delegate watch: 3 is not a number from 1 to 2\n"
        "delegate watch: r-assure is not a number from 1 to 2\n"
    )
    assert code == 0
    followed = out.split(PROMPT)[-1]
    assert "run_id=r-assure" in followed and "run_id=r-economy" not in followed


def test_the_end_of_the_input_at_the_prompt_follows_no_run_and_exits_2(tmp_path, monkeypatch, twelve_hours_east):  # noqa: F811
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code, out, err = on_a_terminal(repo, monkeypatch, Keyboard(""))

    assert (code, err) == (2, "delegate watch: no run chosen\n")
    assert "run-start" not in out and "position" not in out and frames(out) == []


def test_ctrl_c_at_the_prompt_exits_130_with_one_line_and_follows_no_run(tmp_path, monkeypatch, twelve_hours_east):  # noqa: F811
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code, out, err = on_a_terminal(repo, monkeypatch, CtrlC())

    assert (code, err) == (130, "delegate: interrupted\n")
    assert out.endswith(PROMPT + "\n")
    assert "position" not in out and frames(out) == []


@pytest.mark.parametrize(
    ("stdin", "stdout"),
    [
        pytest.param(io.StringIO("1\n"), Terminal(), id="input-not-a-terminal"),
        pytest.param(Keyboard("1\n"), io.StringIO(), id="output-not-a-terminal"),
    ],
)
def test_when_input_or_output_is_not_a_terminal_watch_prints_one_line_on_stderr_and_exits_2(tmp_path, monkeypatch, stdin, stdout):
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code, out, err = on_a_terminal(repo, monkeypatch, stdin, stdout=stdout)

    assert (code, out) == (2, "")
    assert err == (
        "delegate watch: 2 runs are active in this repository; give a run id: "
        "r-economy (run/two, running), r-assure (run/one, running)\n"
    )
    assert stdin.read() == "1\n"  # `watch` read no answer


def test_on_a_terminal_watch_follows_the_one_active_run_with_no_prompt(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    active = write_journal(repo, "older", run_start("older", "a", run_branch="run/older"), step_start("a"))
    write_journal(repo, "newer", run_start("newer", "b", run_branch="run/newer"), run_end("failed", [], ["b"]))
    ends_at_the_first_wait(monkeypatch, (active, "a"))

    with live_lock(active, "run/older"):
        code, out, err = on_a_terminal(repo, monkeypatch, Unread(), "--raw")

    assert (code, err) == (0, "")
    assert "runs are active" not in out and PROMPT not in out
    assert "run_id=older" in out and "run_id=newer" not in out


def test_status_prints_the_same_table_with_no_prompt(tmp_path, monkeypatch, capsys, twelve_hours_east):  # noqa: F811
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("LC_ALL", "en_NZ.UTF-8")
    monkeypatch.setattr(sys, "stdin", Unread())

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        code = delegate.main(["status", "--repo", str(repo)])
        shown = capsys.readouterr()
        ends_at_the_first_wait(monkeypatch, (first, "b"), (second, "x"))
        _, watched, _ = on_a_terminal(repo, monkeypatch, Keyboard("\n"), "--raw")

    assert (code, shown.err) == (0, "")
    assert shown.out.splitlines() == watched.split(PROMPT)[0].splitlines()
    assert shown.out.splitlines()[0] == "2 runs are active in this repository"
    assert [line.split()[1] for line in shown.out.splitlines()[2:]] == ["r-economy", "r-assure"]


def test_a_run_with_a_pause_request_shows_pausing_in_the_line_and_in_the_table(tmp_path, monkeypatch, capsys):
    repo = bare_repo(tmp_path)
    first, second = two_active_runs(repo)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("LC_ALL", "en_NZ.UTF-8")

    with live_lock(first, "run/one"), live_lock(second, "run/two"):
        assert delegate.main(["pause", "r-assure", "--repo", str(repo)]) == 0
        capsys.readouterr()
        code = delegate.main(["status", "--repo", str(repo)])
        shown = capsys.readouterr().out
        _, _, err = on_a_terminal(repo, monkeypatch, io.StringIO(""))

    assert code == 0
    older = shown.splitlines()[3]
    assert older.split()[1] == "r-assure" and older.endswith("pausing · ▶ b specialist · active 2h00m ago")
    assert err.endswith("give a run id: r-economy (run/two, running), r-assure (run/one, pausing)\n")
