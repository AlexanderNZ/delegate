"""The ticket table of `delegate watch` is flat, in plan order, with a rail and an `after` column (#143).

The spec is frame 3a of `design/design_handoff_delegate_terminal_output/` (design turn 3) and its `docs/components.md`
§ Ticket table (watch): the tracks are `#` 3 (right) · rail 2 · `plan` 6 · `state` 10 · `phase` 13 · `after` 8 ·
`used` 7 (right) · `new` 5 (right) · `cache` 6 (right) · the live tail, with 2 cells between two tracks. The width of
the table does not change with the depth of the plan. The rail `│` joins a row to the row directly above when that row
is its one blocker. Otherwise the `after` column names the blockers. The phases of the running ticket stack: the row
has the first phase, and each further phase is a sub-row with only the rail, the phase and a detail in the live tail.

A case writes the journal of a run in the state directory of a real temporary git repository and calls
`delegate.main(["watch", ...])`, with the fakes of `test_watch_frame`: the terminal, the clock, the time zone and the
locale. The ticket ids are the neutral ids of frame 3a.
"""

from __future__ import annotations

import os
import re
from datetime import timedelta

import pytest

from .support import bare_repo, frames
from .test_watch_frame import Clock, built, ends_at_first_poll, painted_frames, terminal_of_a_person, watch  # noqa: F401
from .test_watch_live import PYTEST, START, Run

# The cell where each track starts: `#` 3, rail 2, `plan` 6, `state` 10, `phase` 13, `after` 8, `used` 7, `new` 5,
# `cache` 6, and the live tail, with 2 cells between two tracks.
START_CELL: dict[str, int] = {
    "#": 0, "rail": 5, "plan": 9, "state": 17, "phase": 29, "after": 44, "used": 54, "new": 63, "cache": 70, "tail": 78,
}
WIDTH: dict[str, int] = {"#": 3, "used": 7, "new": 5, "cache": 6}

# Frame 3a: a chain of 11 tickets, each after the row above. 127 and 130 are built, and 128 is in its specialist.
CHAIN_3A: list[str] = ["127", "130", "128", "131", "132", "133", "134", "135", "136", "137", "129"]


def chain(tickets: list[str]) -> dict[str, tuple[str, ...]]:
    """Each ticket after the ticket before it in the list."""
    return {ticket: (above,) for above, ticket in zip(tickets, tickets[1:])}


def at_cell(line: str, cell: int, text: str) -> bool:
    """True when `text` starts at `cell` of the line, after a space or at the start of the line."""
    return line[cell:cell + len(text)] == text and (cell == 0 or line[cell - 1] == " ")


def line_of(**cells: str) -> str:
    """The line that has each text on its design track: a number ends at the last cell of its track, other text starts
    at the first cell. The keyword `rail` is the rail, and `tail` the live tail."""
    line = ""
    for name, text in sorted(cells.items(), key=lambda item: START_CELL[item[0]]):
        cell = START_CELL[name] + WIDTH[name] - len(text) if name in WIDTH else START_CELL[name]
        line = line.ljust(cell) + text
    return line


def right_in_track(line: str, track: str, text: str) -> bool:
    """True when `text` ends at the last cell of the track of a column of numbers."""
    end = START_CELL[track] + WIDTH[track]
    return at_cell(line, end - len(text), text) and line[end:end + 2].strip() == ""


def in_the_specialist(repo, tickets: list[str], built_first: int, blocked_by: dict[str, tuple[str, ...]]) -> Run:
    """The first `built_first` tickets are built, 600 seconds each, and the next ticket is in its specialist."""
    run = Run(repo, *tickets, run_id="20261016T030000Z-abcd", blocked_by=blocked_by)
    for index, ticket in enumerate(tickets[:built_first]):
        built(run, ticket, index * 600)
    start = built_first * 600
    run.step_start(start, tickets[built_first])
    run.add(start, "agent-start", ticket=tickets[built_first], role="specialist", round=0,
            event_stream=str(run.dir / "reports" / f"{tickets[built_first]}.specialist.stream.jsonl"))
    return run


def first_frame(repo, monkeypatch, run: Run, columns: int = 120, rows: int = 24) -> list[str]:
    code, out = watch(repo, monkeypatch, run.dir.name, columns=columns, rows=rows, sleep=ends_at_first_poll(run))
    assert code == 1
    return frames(out)[0]


def test_frame_3a_at_120_columns_puts_the_header_the_running_row_and_its_phase_sub_rows_on_the_tracks(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = in_the_specialist(repo, CHAIN_3A, 2, chain(CHAIN_3A))
    Clock(monkeypatch, 1200 + 240)

    frame = first_frame(repo, monkeypatch, run)

    header, running, gates, verifier, fixup, next_row = frame[2], frame[5], frame[6], frame[7], frame[8], frame[9]
    assert [at_cell(header, START_CELL[name], name) for name in ("plan", "state", "phase", "after")] == [True] * 4, header
    assert [right_in_track(header, name, name) for name in ("#", "used", "new", "cache")] == [True] * 4, header
    assert header[START_CELL["rail"]:START_CELL["plan"]].strip() == ""  # the rail has no header
    assert right_in_track(running, "#", "3"), running
    assert at_cell(running, START_CELL["rail"], "│"), running  # 128 is after 130, the row above
    assert at_cell(running, START_CELL["plan"], "▶ 128"), running
    assert at_cell(running, START_CELL["state"], "running"), running
    assert at_cell(running, START_CELL["phase"], "specialist ●"), running
    assert running[START_CELL["after"]:START_CELL["used"]].strip() == "", running
    assert [right_in_track(running, name, text) for name, text in (("used", "4m00s"), ("new", "0"), ("cache", "0"))] == [True] * 3
    assert at_cell(running, START_CELL["tail"], "quiet 4m00s"), running
    # Each further phase is a sub-row with the rail (the next row, 131, is after 128) and the phase only.
    assert gates == line_of(rail="│", phase="gates ○")
    assert verifier == line_of(rail="│", phase="verifier ○")
    assert fixup == line_of(rail="│", phase="fix-up ○")
    assert next_row.startswith("  4  │   · 131   pending"), next_row
    # The first row has no blocker, so it has no rail. The second row is after the first.
    assert frame[3].startswith("  1      ● 127   ✓ built"), frame[3]
    assert frame[4].startswith("  2  │   ● 130   ✓ built"), frame[4]
    assert all(len(line) <= 120 for line in frame)


def test_frame_3a_paints_the_rail_in_plan_and_the_active_phase_in_now(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = in_the_specialist(repo, CHAIN_3A, 2, chain(CHAIN_3A))
    Clock(monkeypatch, 1200 + 240)

    _, out = watch(repo, monkeypatch, run.dir.name, sleep=ends_at_first_poll(run))

    frame = painted_frames(out)[0]
    assert frame[5].startswith("  \x1b[2m3\x1b[0m  \x1b[34m│\x1b[0m   \x1b[36m▶\x1b[0m \x1b[36m\x1b[1m128\x1b[0m\x1b[0m"), frame[5]
    assert "\x1b[36mspecialist ●\x1b[0m" in frame[5]
    assert frame[6] == " " * START_CELL["rail"] + "\x1b[34m│\x1b[0m" + " " * 23 + "\x1b[2mgates ○\x1b[0m"


@pytest.mark.parametrize("columns", [120, 80])
def test_a_linear_chain_of_40_tickets_has_the_column_positions_of_a_chain_of_3(tmp_path, monkeypatch, columns):
    short, long = [f"t{n}" for n in range(1, 4)], [f"t{n}" for n in range(1, 41)]
    frames_of = []
    for name, tickets in (("short", short), ("long", long)):
        repo = bare_repo(tmp_path / name)
        run = in_the_specialist(repo, tickets, 1, chain(tickets))
        Clock(monkeypatch, 600 + 30)
        frames_of.append(first_frame(repo, monkeypatch, run, columns=columns, rows=100))
    three, forty = frames_of

    # The header, the rows of t1 and t2, and the sub-rows of t2 are the same lines: depth costs no width.
    assert forty[2:8] == three[2:8]
    assert at_cell(forty[2], START_CELL["after"], "after"), forty[2]
    assert at_cell(forty[4], START_CELL["plan"], "▶ t2"), forty[4]
    assert line_of(**{"#": "40", "rail": "│", "plan": "· t40", "state": "pending"}) in forty
    assert all(len(line) <= columns for line in forty)


# Ticket b is after a, the row above: a rail. c is after a, which is not the row above: no rail, and `after` names a.
# d is after b and c: a join, which names both. e is after d, the row above: a rail.
FORK_AND_JOIN: dict[str, tuple[str, ...]] = {"b": ("a",), "c": ("a",), "d": ("b", "c"), "e": ("d",)}


def test_a_ticket_after_a_row_that_is_not_the_row_above_has_no_rail_and_names_its_blocker_in_after_and_a_join_names_both(
    tmp_path, monkeypatch
):
    repo = bare_repo(tmp_path)
    run = Run(repo, "a", "b", "c", "d", "e", blocked_by=FORK_AND_JOIN)
    built(run, "a", 0)
    Clock(monkeypatch, 700)

    code, out = watch(repo, monkeypatch, run.dir.name, sleep=ends_at_first_poll(run))

    assert code == 1
    rows = dict(zip("abcde", frames(out)[0][3:8]))
    rail = {ticket: rows[ticket][START_CELL["rail"]] for ticket in "abcde"}
    after = {ticket: rows[ticket][START_CELL["after"]:START_CELL["used"]].strip() for ticket in "abcde"}
    assert rail == {"a": " ", "b": "│", "c": " ", "d": " ", "e": "│"}
    assert after == {"a": "", "b": "", "c": "a", "d": "b c", "e": ""}
    assert rows["d"] == line_of(**{"#": "4", "plan": "· d", "state": "pending", "after": "b c"})
    painted = dict(zip("abcde", painted_frames(out)[0][3:8]))
    assert painted["c"].endswith("\x1b[34ma\x1b[0m") and painted["d"].endswith("\x1b[34mb c\x1b[0m")  # the ids are `plan`
    assert "\x1b[34m│\x1b[0m" in painted["b"]


def test_the_reason_of_a_failed_and_of_a_skipped_row_is_in_the_live_tail_not_in_the_phase(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "a", "b", "c", blocked_by={"b": ("a",), "c": ("b",)})
    built(run, "a", 0)
    run.step_start(600, "b")
    run.add(700, "step-end", ticket="b", state="failed", reason=f"gates red: {PYTEST}")
    run.add(700, "skip", ticket="c", blockers=["b"], reason="blocked by b, which failed")
    run.add(700, "run-end", result="failed", built=["a"], failed=["b"], skipped=["c"])
    Clock(monkeypatch, 800)

    code, out = watch(repo, monkeypatch, run.dir.name)

    assert code == 3  # the failed step is a problem
    frame = frames(out)[0]
    failed = frame[4]
    # The reason does not make the columns drop: the cut at the width of the terminal ends it.
    assert failed == line_of(**{
        "#": "2", "rail": "│", "plan": "✗ b", "state": "failed", "used": "1m40s", "new": "0", "cache": "0", "tail": f"gates red: {PYTEST}",
    })[:120]
    position = int(out.splitlines()[-1].split()[1])
    _, out = watch(repo, monkeypatch, run.dir.name, "--from", str(position))
    skipped = frames(out)[0][5]
    assert skipped == line_of(**{"#": "3", "rail": "│", "plan": "– c", "state": "skipped", "tail": "blocked by b, which failed"})


def test_a_running_gate_gives_the_gate_count_and_its_log_in_the_sub_row_of_the_gates(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "a", "b", blocked_by={"b": ("a",)})
    # Ticket a ran 3 gates, so the stack has 3 gates. Ticket b ran its first gate and runs its second.
    run.step_start(0, "a")
    run.agent(0, 300, "a", "specialist", run.stream("a.specialist.stream.jsonl"))
    run.report(300, "a")
    for index, command in enumerate(("lint", "types", PYTEST)):
        run.gate(300 + index * 10, 310 + index * 10, "a", command)
    run.verify(330, 600, "a")
    run.add(600, "step-end", ticket="a", state="built", reason=None)
    run.step_start(600, "b")
    run.agent(600, 900, "b", "specialist", run.stream("b.specialist.stream.jsonl"))
    run.report(900, "b")
    run.gate(900, 910, "b", "lint")
    log = run.dir / "gates" / "b" / "build-1.log"
    log.write_text("")
    changed = (START + timedelta(seconds=930)).timestamp()
    os.utime(log, (changed, changed))
    run.add(910, "gate-start", ticket="b", round=0, phase="build", command="types", gate_log=str(log))
    Clock(monkeypatch, 960)

    frame = first_frame(repo, monkeypatch, run)

    running, gates = frame[4], frame[5]
    assert at_cell(running, START_CELL["phase"], "specialist ✓"), running
    assert gates == line_of(phase="gates ● 2/3", tail="gate types · log 30s ago")
    assert frame[6] == line_of(phase="verifier ○")


def test_a_fixup_round_is_a_sub_row_in_warn_with_its_round_count(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "a", run_id="r1")
    run.step_start(0, "a")
    run.agent(0, 300, "a", "specialist", run.stream("a.specialist.stream.jsonl"))
    run.report(300, "a")
    run.gate(300, 360, "a", PYTEST)
    run.verify(360, 480, "a", "REJECT")
    run.add(480, "fixup-start", ticket="a", round=1, rejected_commit="1" * 40, findings_file=str(run.dir / "f.md"),
            agent="python-specialist", tier="strong", model="m")
    run.add(480, "agent-start", ticket="a", role="fix-up", round=1, event_stream=str(run.dir / "reports" / "a.fixup.stream.jsonl"))
    Clock(monkeypatch, 500)

    _, out = watch(repo, monkeypatch, run.dir.name, sleep=ends_at_first_poll(run))

    frame = painted_frames(out)[0]
    plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in frame]
    assert plain[3].startswith("  1      ▶ a     running     specialist ✓"), plain[3]
    assert plain[4:7] == [line_of(phase="gates ✓"), line_of(phase="verifier ✓"), line_of(phase="fix-up ● 1/2")]
    assert frame[6] == " " * START_CELL["phase"] + "\x1b[33mfix-up ● 1/2\x1b[0m"


def test_with_a_c_locale_the_table_has_the_ascii_rail_and_the_same_column_positions(tmp_path, monkeypatch):
    unicode_repo, ascii_repo = (bare_repo(tmp_path / name) for name in ("unicode", "ascii"))
    unicode_run = in_the_specialist(unicode_repo, CHAIN_3A, 2, chain(CHAIN_3A))
    ascii_run = in_the_specialist(ascii_repo, CHAIN_3A, 2, chain(CHAIN_3A))
    Clock(monkeypatch, 1200 + 240)
    unicode = first_frame(unicode_repo, monkeypatch, unicode_run)
    monkeypatch.delenv("LC_ALL")
    monkeypatch.delenv("LC_CTYPE", raising=False)
    monkeypatch.setenv("LANG", "C")

    ascii_frame = first_frame(ascii_repo, monkeypatch, ascii_run)

    assert [len(line) for line in ascii_frame] == [len(line) for line in unicode]
    assert ascii_frame[3:10] == [
        line_of(**{"#": "1", "plan": "* 127", "state": "+ built", "used": "10m00s", "new": "39k", "cache": "0"}),
        line_of(**{"#": "2", "rail": "|", "plan": "* 130", "state": "+ built", "used": "10m00s", "new": "39k", "cache": "0"}),
        line_of(**{"#": "3", "rail": "|", "plan": "> 128", "state": "running", "phase": "specialist *", "used": "4m00s", "new": "0",
                   "cache": "0", "tail": "quiet 4m00s"}),
        line_of(rail="|", phase="gates o"),
        line_of(rail="|", phase="verifier o"),
        line_of(rail="|", phase="fix-up o"),
        line_of(**{"#": "4", "rail": "|", "plan": ". 131", "state": "pending"}),
    ]
    assert all(line.isascii() for line in ascii_frame[:15])


def test_the_gate_count_starts_again_with_each_run_of_the_gates(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "a", "b", run_id="r1")
    # Ticket a ran the 2 gates of the stack. The first run of the gates of b had a red gate, so its specialist
    # continued. Its new report starts a new run of the gates, and no gate of that run started yet.
    run.step_start(0, "a")
    run.agent(0, 300, "a", "specialist", run.stream("a.specialist.stream.jsonl"))
    run.report(300, "a")
    run.gate(300, 310, "a", "lint")
    run.gate(310, 320, "a", PYTEST)
    run.verify(320, 600, "a")
    run.add(600, "step-end", ticket="a", state="built", reason=None)
    run.step_start(600, "b")
    run.agent(600, 900, "b", "specialist", run.stream("b.specialist.stream.jsonl"))
    run.report(900, "b")
    run.gate(900, 910, "b", "lint")
    run.gate(910, 920, "b", PYTEST, green=False)
    run.add(920, "continuation", ticket="b", count=1, limit=2, trigger="gates-red", mode="resume", resume_session="s", commits=[], reason="red")
    run.agent(920, 1000, "b", "specialist", run.stream("b.specialist.stream.jsonl"))
    run.report(1000, "b")
    Clock(monkeypatch, 1005)

    frame = first_frame(repo, monkeypatch, run)

    assert frame[5] == line_of(phase="gates ●")  # not `gates ● 2/2`: the new run of the gates has no gate yet
