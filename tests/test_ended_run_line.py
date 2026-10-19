"""The ended-run line of `delegate watch` and its error lines take the colours of tokens.md, frame 1l of the design (#119).

The words do not change. On a terminal, the result of the run has the colour of its role: `built` in `ok`, `failed`
in `bad`, `paused` in `warn`, and the result word is bold. The time is faint, and the first problem is in `bad`. An
error line on stderr is in `bad` when stderr is a terminal. With `NO_COLOR`, or with no terminal, no line has an SGR code.

A case writes the journal of a run in the state directory of a real temporary git repository and calls
`delegate.main(["watch", ...])`. The system boundaries are faked: the terminal (a standard output or a standard error
whose `isatty` is true) and `NO_COLOR`.
"""

from __future__ import annotations

import re
import sys

import pytest

from delegate.cli import delegate

from .support import Terminal, bare_repo, run_end, run_start, stale_lock, step_end, step_start, without_titles, write_journal

# An SGR sequence: the paint of a role.
SGR = re.compile(r"\x1b\[[0-9;]*m")

# The time of the line, in the local time zone.
AT = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d")


@pytest.fixture(autouse=True)
def no_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)


def watch(repo, capsys) -> tuple[int, str, str]:
    code = delegate.main(["watch", "--repo", str(repo), "--poll-seconds", "0.02", "--max-minutes", "0.1"])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def on_terminals(repo, capsys, monkeypatch) -> tuple[int, str, str]:
    """The exit code, the stdout and the stderr of `watch` when stdout and stderr are terminals, with no title sequence."""
    out, err = Terminal(), Terminal()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    code, _, _ = watch(repo, capsys)
    return code, without_titles(out.getvalue()), err.getvalue()


def failed_run(repo) -> None:
    write_journal(
        repo, "r1", run_start("r1", "a"), step_start("a"), step_end("a", "failed", "no commit"),
        run_end("failed", failed=["a"]),
    )


def test_on_a_terminal_the_result_of_a_built_run_is_ok_and_bold_and_the_time_is_faint(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), step_end("a"), run_end("built", ["a"]))
    _, plain, _ = watch(repo, capsys)

    code, out, err = on_terminals(repo, capsys, monkeypatch)

    assert (code, err) == (0, "")
    assert SGR.sub("", out) == plain
    assert "ended \x1b[32m\x1b[1mbuilt\x1b[0m\x1b[0m at " in out
    (at,) = AT.findall(plain)
    assert f" at \x1b[2m{at}\x1b[0m" in out


def test_on_a_terminal_the_result_of_a_failed_run_and_its_first_problem_are_bad(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    failed_run(repo)
    _, plain, _ = watch(repo, capsys)

    code, out, err = on_terminals(repo, capsys, monkeypatch)

    assert (code, err) == (1, "")
    assert SGR.sub("", out) == plain
    assert "ended \x1b[31m\x1b[1mfailed\x1b[0m\x1b[0m at " in out
    assert out.endswith("; \x1b[31mfirst problem: ticket a: the step failed: no commit\x1b[0m\n")


def test_on_a_terminal_the_word_paused_of_a_paused_run_is_warn(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    write_journal(
        repo, "r1", run_start("r1", "a", "b"), step_start("a"), step_end("a"),
        ("pause-requested", {"ticket": "b", "requested_at": None}), ("paused", {"ticket": "b", "before": "step"}),
    )
    _, plain, _ = watch(repo, capsys)

    code, out, err = on_terminals(repo, capsys, monkeypatch)

    assert (code, err) == (7, "")
    assert SGR.sub("", out) == plain
    assert "the newest run r1 \x1b[33mpaused\x1b[0m at \x1b[2m" in out


def test_an_error_line_on_a_terminal_is_bad_and_has_the_same_words(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    stopped = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"))
    stale_lock(stopped)
    _, _, plain = watch(repo, capsys)

    code, out, err = on_terminals(repo, capsys, monkeypatch)

    assert (code, out) == (2, "")
    assert plain.startswith("delegate watch: no run is active; the newest run r1 stopped at ")
    assert err == f"\x1b[31m{plain.rstrip()}\x1b[0m\n"


def test_an_error_line_for_an_unknown_run_on_a_terminal_is_bad(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    failed_run(repo)
    monkeypatch.setattr(sys, "stderr", err := Terminal())

    code = delegate.main(["watch", "nosuchrun", "--repo", str(repo)])

    assert code == 2
    assert re.fullmatch(r"\x1b\[31mdelegate watch: .*nosuchrun.*\x1b\[0m\n", err.getvalue())


def test_an_error_line_to_a_pipe_has_no_sgr_code_also_when_stdout_is_a_terminal(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    stopped = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"))
    stale_lock(stopped)
    monkeypatch.setattr(sys, "stdout", Terminal())

    code, _, err = watch(repo, capsys)

    assert code == 2
    assert err.startswith("delegate watch: no run is active; ")
    assert "\x1b" not in err


def test_with_no_color_set_the_lines_on_terminals_have_no_sgr_code(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    failed_run(repo)
    monkeypatch.setenv("NO_COLOR", "1")

    code, out, err = on_terminals(repo, capsys, monkeypatch)

    assert (code, err) == (1, "")
    assert out.startswith("no run is active; the newest run r1 ended failed at ")
    assert "\x1b" not in out
