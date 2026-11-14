"""`delegate runs`: the `alert` and `do` columns and the legend under the table (#309).

A case writes the journals of runs with `journals.write_run` and calls `main(argv)`. The terminal is 120 columns wide, so
the legend shows. The expected words come from the ticket and the copy deck (W8.alert.*, W8.do.*), not from the code.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from .journals import built, clock_time, run_end, runs, working, write_run

IDLE_ONLY = "⚠ idle 10m00s"


@pytest.fixture(autouse=True)
def wide(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "120")


def fixup_started(ticket: str, round_number: int, at):
    return at, "fixup-start", {
        "ticket": ticket, "round": round_number, "rejected_commit": "3", "findings_file": "/f", "agent": "s", "tier": "strong", "model": "m",
    }


def row_of(out: str, short: str) -> str:
    (line,) = [line for line in out.splitlines() if f"  {short}  " in line]
    return line


def cells_of(line: str) -> list[str]:
    """The cells of a line of the table: the text between runs of 2 spaces or more. A blank cell has no entry."""
    return [cell for cell in re.split(r" {2,}", line.strip()) if cell]


def failed_end(at):
    return at, "run-end", {"result": "failed", "built": [], "failed": ["1"], "skipped": []}


def every_kind(root) -> None:
    """Six runs: an idle agent, a fix-up round, paused, failed, built, and a run that works with nothing wrong."""
    write_run(root, "alpha", "iiii", clock_time(13, 0), [working("1", clock_time(14, 0)), agent_idle("1", clock_time(14, 10))],
              adapter="claude-code", lock=True)
    write_run(root, "beta", "ffff", clock_time(13, 1), [working("1", clock_time(14, 0)), fixup_started("1", 1, clock_time(14, 15))],
              adapter="claude-code", lock=True)
    write_run(root, "gamma", "pppp", clock_time(13, 2), [(clock_time(13, 40), "paused", {"ticket": "1", "before": "step"})])
    started = clock_time(13, 3)
    write_run(root, "delta", "llll", started, [working("1", started), failed_end(clock_time(13, 30))], tickets=("1",))
    write_run(root, "epsilon", "bbbb", clock_time(13, 4), [*built("1", clock_time(13, 4), clock_time(13, 30)), run_end(clock_time(13, 30))], tickets=("1",))
    write_run(root, "zeta", "rrrr", clock_time(13, 5), [working("1", clock_time(14, 18))], lock=True)


def agent_idle(ticket: str, at):
    return at, "agent-start", {"ticket": ticket, "role": "specialist", "round": 0, "event_stream": f"/s/{ticket}.stream.jsonl"}


def test_each_run_shows_its_alert_and_the_next_command_and_a_run_with_nothing_wrong_shows_two_blank_cells(machine, capsys):
    every_kind(machine)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[4].split()[-4:] == ["cache", "trend", "alert", "do"]
    assert cells_of(row_of(out, "iiii"))[-2:] == [IDLE_ONLY, "watch"]
    assert cells_of(row_of(out, "ffff"))[-2:] == ["fix-up 1/2", "watch"]
    assert cells_of(row_of(out, "pppp"))[-2:] == ["0/2", "resume"]  # the ETA, the new and the cache cells are blank, and so is the alert
    assert cells_of(row_of(out, "llll"))[-1] == "retry"
    assert cells_of(row_of(out, "bbbb"))[-1] == "merge"
    assert cells_of(row_of(out, "rrrr"))[-2:] == ["0/2", "-"]  # no alert and no command: the line ends at the ETA, and the two cells are blank


def test_the_json_of_each_run_has_the_alert_and_do_or_null_and_the_format_stays_1(machine, capsys):
    every_kind(machine)

    code, out, _ = runs(capsys, "--json")

    found = json.loads(out)
    cells = {run["run_id"][-4:]: (run["alert"], run["do"]) for run in found["runs"]}
    assert (code, found["format"]) == (0, 1)
    assert cells == {
        "iiii": (IDLE_ONLY, "watch"), "ffff": ("fix-up 1/2", "watch"), "pppp": (None, "resume"), "llll": (None, "retry"),
        "bbbb": (None, "merge"), "rrrr": (None, None),
    }


def test_an_idle_agent_comes_before_a_fix_up_round_in_the_one_value_of_alert(machine, capsys):
    write_run(machine, "alpha", "iiii", clock_time(13, 0),
              [working("1", clock_time(14, 0)), fixup_started("1", 2, clock_time(14, 5)), (clock_time(14, 10), "agent-start", {
                  "ticket": "1", "role": "fix-up", "round": 2, "event_stream": "/s/1.fixup.jsonl"})],
              adapter="claude-code", lock=True)

    _, out, _ = runs(capsys)

    assert cells_of(row_of(out, "iiii"))[-2:] == [IDLE_ONLY, "watch"]


def test_an_idle_agent_under_the_stall_limit_is_no_alert(machine, capsys):
    write_run(machine, "alpha", "iiii", clock_time(13, 0), [working("1", clock_time(14, 0)), agent_idle("1", clock_time(14, 16))],
              adapter="claude-code", lock=True)  # idle 4 minutes, and the limit is 5

    _, out, _ = runs(capsys, "--json")

    (run,) = json.loads(out)["runs"]
    assert (run["alert"], run["do"]) == (None, None)


def test_a_stopped_run_shows_resume_and_a_halted_run_shows_resume(machine, capsys):
    started = clock_time(13, 0)
    write_run(machine, "alpha", "ssss", started, built("1", started, clock_time(13, 30)))  # no process holds its lock: stopped
    write_run(machine, "beta", "hhhh", clock_time(13, 5), [(clock_time(13, 40), "halted", {"ticket": "1", "role": "specialist", "round": 0, "message": "no"})])

    _, out, _ = runs(capsys, "--json")

    cells = {run["run_id"][-4:]: (run["state"], run["do"]) for run in json.loads(out)["runs"]}
    assert cells == {"ssss": ("stopped", "resume"), "hhhh": ("halted", "resume")}


def legend(out: str) -> list[str]:
    """The lines after the last blank line of the table: the legend. The group `ended` is another group of the table, so the legend is the last group only when its lines hold a command."""
    lines = out.splitlines()
    last = lines[len(lines) - 1 - lines[::-1].index(""):][1:] if "" in lines[4:] else []
    return last if last and "  " in last[0] and last[0].split()[0] in ("watch", "resume", "retry", "merge") else []


def test_the_legend_has_one_line_for_each_word_in_the_table_and_the_command_of_the_word(machine, capsys):
    every_kind(machine)

    _, out, _ = runs(capsys)

    assert legend(out) == [
        "watch   delegate watch <run>",
        "resume  delegate run --resume <run>",
        "retry   delegate run --resume <run> --retry <ticket>",
        "merge   git merge --ff-only <run branch>",
    ]


def test_the_legend_has_no_line_for_a_word_that_is_not_in_the_table_and_names_a_word_once(machine, capsys):
    write_run(machine, "alpha", "pppp", clock_time(13, 0), [(clock_time(13, 40), "paused", {"ticket": "1", "before": "step"})])
    write_run(machine, "beta", "qqqq", clock_time(13, 5), [(clock_time(13, 45), "paused", {"ticket": "1", "before": "step"})])

    _, out, _ = runs(capsys)

    assert legend(out) == ["resume  delegate run --resume <run>"]


def test_no_legend_follows_a_table_with_no_command(machine, capsys):
    write_run(machine, "alpha", "rrrr", clock_time(13, 0), [working("1", clock_time(14, 18))], lock=True)

    _, out, _ = runs(capsys)

    assert cells_of(out.splitlines()[-1])[-1] == "-"
    assert "delegate watch" not in out


def test_the_legend_drops_under_100_columns(machine, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "99")
    every_kind(machine)

    _, out, _ = runs(capsys)

    assert "delegate watch <run>" not in out
    assert "git merge" not in out


def failed_start(root) -> None:
    """A run that failed at start: it has a log and no journal. The log last changed at 13:30."""
    log = root / "theta-0123456789ab" / "runs" / "20261114T133000Z-ssss" / "run.log"
    log.parent.mkdir(parents=True)
    log.write_text("delegate run: the workflow file is missing\n")
    stamp = clock_time(13, 30).timestamp()
    os.utime(log, (stamp, stamp))


def test_the_json_of_each_run_has_the_idle_seconds_the_fix_up_round_the_fix_up_limit_and_the_failed_start_as_values(machine, capsys):
    every_kind(machine)
    failed_start(machine)

    code, out, _ = runs(capsys, "--json")

    found = {run["run_id"][-4:]: run for run in json.loads(out)["runs"]}
    values = {short: (run["idle_seconds"], run["fixup_round"], run["fixup_limit"], run["failed_start"]) for short, run in found.items()}
    assert (code, json.loads(out)["format"]) == (0, 1)
    assert values == {
        "iiii": (600, None, None, False),  # idle since 14:10, now 14:20
        "ffff": (None, 1, 2, False),
        "pppp": (None, None, None, False), "llll": (None, None, None, False), "bbbb": (None, None, None, False), "rrrr": (None, None, None, False),
        "ssss": (None, None, None, True),
    }
    assert found["ssss"]["alert"] is None
