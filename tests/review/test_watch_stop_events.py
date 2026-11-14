"""`delegate watch` ends at each event that stops a run, and decides the end of a run as `delegate status` does (#325).

The run state (ADR 0009) names five events that stop a run: `run-end`, `paused`, `halted`, `crashed` and `setup-problem`.
A watch of a run that the check of the setup stopped ends at once with the exit code and the note of a stopped run. A
journal that holds `journal-repaired` after its `run-end` is an ended run in `watch` and in `status`.

A case writes the journal of a run in the state directory of a real temporary git repository and calls
`delegate.main(["watch", ...])` or `delegate.main(["status", ...])`. The clock of the commands is the fake clock of the case.
"""

from __future__ import annotations

import pytest

from delegate.cli import delegate

from ..support import (
    append_events, bare_repo, fake_clock, live_lock, run_end, run_start, stale_lock, step_end, step_start, write_journal,
)

pytestmark = pytest.mark.usefixtures("clock")

NOTE = "the setup check stopped the run before the first agent session: the gate command is not found"
RESUME = "go on with delegate run --resume r1"


def setup_problem() -> tuple[str, dict[str, object]]:
    return "setup-problem", {"problems": ["the gate command is not found"]}


def command(capsys, *argv: str) -> tuple[int, str, str]:
    code = delegate.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_a_watch_of_a_run_that_the_setup_check_stopped_exits_at_once_with_the_note_of_the_stop(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"), setup_problem())

    with live_lock(path):  # the run process has not ended yet: the watch follows the journal
        code, out, err = command(capsys, "watch", "--repo", str(repo), "--poll-seconds", "0.02", "--max-minutes", "5", "--raw")

    assert code == 2  # not 6, the time limit
    assert "setup-problem" in out
    assert NOTE in err
    assert RESUME in err


def test_a_watch_ends_at_the_stop_by_the_setup_check_that_arrives_after_it_starts(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"))

    def arrive(seconds: float) -> None:
        fake_clock().on_sleep = None
        append_events(path, setup_problem())

    fake_clock().on_sleep = arrive

    with live_lock(path):
        code, out, err = command(capsys, "watch", "--repo", str(repo), "--poll-seconds", "0.02", "--max-minutes", "5", "--json")

    assert code == 2
    assert '"event": "setup-problem"' in out
    assert NOTE in err


def test_the_newest_run_that_the_setup_check_stopped_gives_the_line_of_a_stopped_run_with_the_note(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"), setup_problem())
    stale_lock(path)

    code, out, err = command(capsys, "watch", "--repo", str(repo))

    assert (code, out) == (2, "")
    assert err.startswith("delegate watch: no run is active; the newest run r1 stopped at ")
    assert err.rstrip().endswith(f": {NOTE}; change the setup, then {RESUME}")


@pytest.fixture
def repaired_after_the_end(tmp_path):
    """A repository whose run built its ticket, and whose journal holds `journal-repaired` after the `run-end`."""
    repo = bare_repo(tmp_path)
    write_journal(
        repo, "r1", run_start("r1", "a"), step_start("a"), step_end("a"), run_end("built", ["a"]),
        ("journal-repaired", {"bytes_removed": 12}),
    )
    return repo


def test_a_journal_with_journal_repaired_after_the_run_end_ends_in_watch_with_the_result_of_the_run(repaired_after_the_end, capsys):
    code, out, err = command(capsys, "watch", "--repo", str(repaired_after_the_end))

    assert (code, err) == (0, "")
    assert out.startswith("no run is active; the newest run r1 ended built at ")


def test_a_journal_with_journal_repaired_after_the_run_end_shows_in_status_the_result_of_the_run(repaired_after_the_end, capsys):
    code, out, err = command(capsys, "status", "--repo", str(repaired_after_the_end))

    assert (code, err) == (0, "")
    assert "built" in out
    assert "stopped" not in out


def test_no_module_other_than_the_run_state_lists_the_stop_events_as_literals():
    """A list of three or more stop events in a literal is a second copy: a new stop event would reach it late (#325)."""
    import ast
    from pathlib import Path

    import delegate
    from delegate.run import run_state

    root = Path(delegate.__file__).parent
    copies = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "run_state.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                names = {item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)}
                if len(names & set(run_state.STOP_EVENTS)) >= 3:
                    copies.append(f"{path.relative_to(root)}:{node.lineno}")

    assert copies == []
