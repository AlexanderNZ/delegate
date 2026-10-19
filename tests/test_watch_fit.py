"""The live view of `delegate watch` fits the height and the width of the terminal (#144).

The spec is frame 3b of `design/design_handoff_delegate_terminal_output/` (design turn 3), its `docs/components.md`
§ Ticket table (watch) (Fit to height) and § 80 columns, and its `docs/copy-deck.md`. The table has the rows that the
terminal gives it: the terminal rows, less the line of the cursor, the run line, the blank line, the header, the divider
and 4 lines of the feed. When the plan has more rows, the tail folds into one row above the divider. When that is not
enough, the built rows in the middle fold too: the first three rows and the three rows before the running ticket stay.
The running ticket and the pending rows nearest to it stay.

A case writes the journal of a run in the state directory of a real temporary git repository and calls
`delegate.main(["watch", ...])`, with the fakes of `test_watch_frame`: the terminal, the clock, the time zone and the
locale. The ids and the blockers are the neutral ids of frame 3b (screen-map.md): 131 after 124, 126 after 125, 133
after 126 and 132, and each other ticket after the row above.
"""

from __future__ import annotations

import json
import os
import re
from datetime import timedelta

import pytest

from delegate.cli import delegate
from delegate.run.status import format_duration, format_tokens

from .support import bare_repo, frames, live_lock
from .test_watch_frame import Clock, ends_at_first_poll, painted_frames, terminal_of_a_person, watch  # noqa: F401
from .test_watch_live import PYTEST, START, Run
from .test_watch_plan_table import START_CELL, line_of

# The 40 tickets of frame 3b in plan order: 101 to 125, then the branch 131 and 132 after 124, then 126 after 125, the
# join 133 after 126 and 132, and 11 more tickets in a chain.
PLAN_3B: list[str] = [str(n) for n in range(101, 126)] + ["131", "132", "126", "133"] + [str(n) for n in range(134, 145)]
BLOCKED_BY_3B: dict[str, tuple[str, ...]] = {
    **{ticket: (above,) for above, ticket in zip(PLAN_3B, PLAN_3B[1:])},
    "131": ("124",), "126": ("125",), "133": ("126", "132"),
}

# The gates of the stack: each built ticket runs the three, so the stack has 3 gates.
GATES: tuple[str, ...] = ("lint", "types", PYTEST)

# The running ticket 124 is the 24th row. 23 tickets are built, 600 seconds each.
RUNNING = "124"
STEP = 23 * 600


def built_with_three_gates(run: Run, ticket: str, start: float) -> None:
    run.step_start(start, ticket)
    run.agent(start, start + 300, ticket, "specialist", run.stream(f"{ticket}.specialist.stream.jsonl"))
    run.report(start + 300, ticket)
    for index, command in enumerate(GATES):
        run.gate(start + 300 + index * 20, start + 320 + index * 20, ticket, command)
    run.verify(start + 360, start + 600, ticket)
    run.add(start + 600, "step-end", ticket=ticket, state="built", reason=None)


def frame_3b(repo) -> Run:
    """Frame 3b: 23 tickets are built, and ticket 124 runs its second gate of 3. Its first gate is green."""
    run = Run(repo, *PLAN_3B, run_id="20261019T030000Z-a41f", blocked_by=BLOCKED_BY_3B)
    for index, ticket in enumerate(PLAN_3B[:23]):
        built_with_three_gates(run, ticket, index * 600)
    run.step_start(STEP, RUNNING)
    run.agent(STEP, STEP + 300, RUNNING, "specialist", run.stream(f"{RUNNING}.specialist.stream.jsonl"))
    run.report(STEP + 300, RUNNING)
    run.gate(STEP + 300, STEP + 320, RUNNING, "lint")
    log = run.dir / "gates" / RUNNING / "build-1.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("")
    changed = (START + timedelta(seconds=STEP + 356)).timestamp()
    os.utime(log, (changed, changed))
    run.add(STEP + 320, "gate-start", ticket=RUNNING, round=0, phase="build", command="types", gate_log=str(log))
    return run


def in_a_continuation(repo) -> Run:
    """The plan of frame 3b, with ticket 124 in its specialist after its first continuation: its live tail is
    `cont 1/2 · active 40s`, 21 cells. A continuation at its limit is a problem, which ends the watch."""
    run = Run(repo, *PLAN_3B, run_id="20261019T030000Z-a41f", blocked_by=BLOCKED_BY_3B)
    for index, ticket in enumerate(PLAN_3B[:23]):
        built_with_three_gates(run, ticket, index * 600)
    run.step_start(STEP, RUNNING)
    run.agent(STEP, STEP + 200, RUNNING, "specialist", run.stream(f"{RUNNING}.specialist.stream.jsonl"), end_state="capped")
    run.add(STEP + 200, "continuation", ticket=RUNNING, count=1, limit=2, trigger="capped", mode="resume", resume_session="s",
            commits=[], reason="capped")
    # The harness has not opened the stream of the continuation yet: the activity counts from its `agent-start`.
    run.add(STEP + 320, "agent-start", ticket=RUNNING, role="specialist", round=0,
            event_stream=str(run.dir / "reports" / f"{RUNNING}.continuation.stream.jsonl"))
    return run


def snapshot_tickets(repo, run: Run, capsys) -> dict[str, dict[str, object]]:
    """The tickets of `delegate status --json`, by id."""
    capsys.readouterr()
    assert delegate.main(["status", run.dir.name, "--repo", str(repo), "--json"]) == 0
    return {ticket["id"]: ticket for ticket in json.loads(capsys.readouterr().out)["tickets"]}


def first_frame(repo, monkeypatch, run: Run, columns: int = 120, rows: int = 24) -> list[str]:
    code, out = watch(repo, monkeypatch, run.dir.name, columns=columns, rows=rows, sleep=ends_at_first_poll(run))
    assert code == 1
    return frames(out)[0]


def test_frame_3b_at_120_by_24_folds_the_pending_tail_and_the_built_rows_in_the_middle(tmp_path, monkeypatch, capsys):
    repo = bare_repo(tmp_path)
    run = frame_3b(repo)
    Clock(monkeypatch, STEP + 360)
    tickets = snapshot_tickets(repo, run, capsys)
    middle = PLAN_3B[3:20]  # rows 4 to 20: 104 to 120
    used = format_duration(sum(int(tickets[t]["elapsed_seconds"]) for t in middle))  # type: ignore[call-overload]
    new = format_tokens(sum(int(tickets[t]["tokens_new"]) for t in middle))  # type: ignore[call-overload]
    cache = format_tokens(sum(int(tickets[t]["tokens_cache"]) for t in middle))  # type: ignore[call-overload]
    row = {t: (format_duration(int(tickets[t]["elapsed_seconds"])), format_tokens(int(tickets[t]["tokens_new"])),  # type: ignore[call-overload]
               format_tokens(int(tickets[t]["tokens_cache"]))) for t in tickets if tickets[t]["state"] != "pending"}  # type: ignore[call-overload]

    frame = first_frame(repo, monkeypatch, run)

    def built_row(position: int, rail: str = "│") -> str:
        ticket = PLAN_3B[position - 1]
        used_cell, new_cell, cache_cell = row[ticket]
        return line_of(**{"#": str(position), "rail": rail, "plan": f"● {ticket}", "state": "✓ built",
                          "used": used_cell, "new": new_cell, "cache": cache_cell})

    used_124, new_124, cache_124 = row[RUNNING]
    # 23 rows of the terminal for the frame (the last line is for the cursor): the run line, the blank line, the header,
    # 15 table rows, the divider, and the feed.
    assert frame[2:18] == [
        "  #      plan    state       phase          after        used    new   cache",
        built_row(1, rail=""),
        built_row(2),
        built_row(3),
        line_of(rail="│", plan="✓ 17 built", after="104–120", used=used, new=new, cache=cache, tail="rows 4–20 folded"),
        built_row(21),
        built_row(22),
        built_row(23),
        line_of(**{"#": "24", "rail": "│", "plan": "▶ 124", "state": "running", "phase": "specialist ✓",
                   "used": used_124, "new": new_124, "cache": cache_124}),
        line_of(rail="│", phase="gates ● 2/3", tail="gate types · log 4s ago"),
        line_of(rail="│", phase="verifier ○"),
        line_of(rail="│", phase="fix-up ○"),
        line_of(**{"#": "25", "rail": "│", "plan": "· 125", "state": "pending"}),
        line_of(**{"#": "26", "plan": "· 131", "state": "pending", "after": "124"}),
        line_of(**{"#": "27", "rail": "│", "plan": "· 132", "state": "pending"}),
        line_of(plan="· 13 pending", after="126–144").ljust(START_CELL["used"]) + "rows 28–40 folded",
    ]
    assert frame[18] == "─" * 120  # the tail fold is directly above the divider
    assert len(frame) < 24


def test_the_fold_rows_are_faint_and_the_rail_runs_through_the_middle_fold(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = frame_3b(repo)
    Clock(monkeypatch, STEP + 360)

    _, out = watch(repo, monkeypatch, run.dir.name, sleep=ends_at_first_poll(run))

    frame = painted_frames(out)[0]
    middle, tail = frame[6], frame[17]
    assert middle.startswith(" " * START_CELL["rail"] + "\x1b[34m│\x1b[0m   \x1b[32m✓\x1b[0m\x1b[2m 17 built\x1b[0m"), middle
    assert "\x1b[2m104–120\x1b[0m" in middle and middle.endswith("\x1b[2mrows 4–20 folded\x1b[0m"), middle
    assert tail.startswith(" " * START_CELL["plan"] + "\x1b[2m· 13 pending\x1b[0m"), tail
    assert "\x1b[2m126–144\x1b[0m" in tail and tail.endswith("\x1b[2mrows 28–40 folded\x1b[0m"), tail


def test_with_a_c_locale_a_fold_row_has_the_ascii_glyphs_and_an_ascii_range(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = frame_3b(repo)
    Clock(monkeypatch, STEP + 360)
    monkeypatch.setenv("LC_ALL", "C")

    frame = first_frame(repo, monkeypatch, run)

    assert frame[6].startswith(line_of(rail="|", plan="+ 17 built", after="104-120")), frame[6]
    assert frame[6].endswith("  rows 4-20 folded"), frame[6]
    assert frame[17] == line_of(plan=". 13 pending", after="126-144").ljust(START_CELL["used"]) + "rows 28-40 folded"


def test_a_terminal_with_more_rows_folds_only_the_tail_and_keeps_every_row_from_the_top(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = frame_3b(repo)
    Clock(monkeypatch, STEP + 360)

    frame = first_frame(repo, monkeypatch, run, rows=41)

    # 41 rows: 32 table rows. The 24 rows to the running ticket, its 3 sub-rows, 4 pending rows, and the tail fold.
    table = frame[3:35]
    assert [line[:3].strip() for line in table[:24]] == [str(n) for n in range(1, 25)]
    assert table[27:31] == [
        line_of(**{"#": "25", "rail": "│", "plan": "· 125", "state": "pending"}),
        line_of(**{"#": "26", "plan": "· 131", "state": "pending", "after": "124"}),
        line_of(**{"#": "27", "rail": "│", "plan": "· 132", "state": "pending"}),
        line_of(**{"#": "28", "plan": "· 126", "state": "pending", "after": "125"}),
    ]
    assert table[31] == line_of(plan="· 12 pending", after="133–144").ljust(START_CELL["used"]) + "rows 29–40 folded"
    assert frame[35] == "─" * 120
    assert not any("built" in line and "folded" in line for line in frame)


def test_a_terminal_with_room_for_every_row_does_not_fold(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = frame_3b(repo)
    Clock(monkeypatch, STEP + 360)

    frame = first_frame(repo, monkeypatch, run, rows=60)

    assert not any("folded" in line for line in frame)
    assert [line[:3].strip() for line in frame[3:46] if line[:3].strip()] == [str(n) for n in range(1, 41)]


@pytest.mark.parametrize(
    ("columns", "bar", "dropped"),
    [
        pytest.param(100, True, set(), id="100-everything"),
        pytest.param(99, False, set(), id="99-the-bar-drops-first"),
        pytest.param(84, False, {"new", "cache"}, id="84-then-new-and-cache"),
        pytest.param(80, False, {"new", "cache", "used"}, id="80-then-used"),
    ],
)
def test_at_80_by_24_the_bar_drops_first_then_new_and_cache_then_used_and_the_phase_labels_and_the_folds_stay(
    tmp_path, monkeypatch, columns, bar, dropped
):
    repo = bare_repo(tmp_path)
    run = in_a_continuation(repo)
    Clock(monkeypatch, STEP + 360)

    frame = first_frame(repo, monkeypatch, run, columns=columns)

    assert ("█" in frame[0]) is bar, frame[0]
    assert set(frame[2].split()) == {"#", "plan", "state", "phase", "after", "used", "new", "cache"} - dropped, frame[2]
    running = next(line for line in frame if "▶ 124" in line)
    assert re.split(r"\s{2,}", running.strip()) == [
        "24", "│", "▶ 124", "running", "specialist ●", *([] if "used" in dropped else ["6m00s"]),
        *([] if "new" in dropped else ["19k", "0"]), "cont 1/2 · active 40s",
    ], running
    assert [line.strip() for line in frame if line.strip().split(" ")[-1] in ("○", "●") and "▶" not in line] == [
        "│                       gates ○", "│                       verifier ○", "│                       fix-up ○",
    ]
    assert any(line.lstrip().startswith("│   ✓ 17 built") and line.endswith("rows 4–20 folded") for line in frame)
    assert any(line.lstrip().startswith("· 13 pending") and line.endswith("rows 28–40 folded") for line in frame)
    assert all(len(line) <= columns for line in frame)


def a_chain_where_b_runs(repo) -> Run:
    run = Run(repo, "a", "b", "c", "d", blocked_by={"b": ("a",), "c": ("b",), "d": ("c",)})
    built_with_three_gates(run, "a", 0)
    run.step_start(600, "b")
    run.add(600, "agent-start", ticket="b", role="specialist", round=0, event_stream=str(run.dir / "reports" / "b.specialist.stream.jsonl"))
    return run


def test_while_the_run_is_pausing_the_next_row_gives_pauses_before_this_step_and_once_paused_paused_before_this_step(
    tmp_path, monkeypatch
):
    repo = bare_repo(tmp_path)
    run = a_chain_where_b_runs(repo)
    request = run.dir / "pause-request"
    request.write_text(json.dumps({"time": "2026-10-16T03:10:00+00:00", "pid": os.getpid()}) + "\n")
    Clock(monkeypatch, 700)

    with live_lock(run.path):
        code, out = watch(repo, monkeypatch, run.dir.name, sleep=ends_at_first_poll(run))

    assert code == 1
    pausing = frames(out)[0]
    assert pausing[0].startswith("run r1  pausing  "), pausing[0]
    assert pausing[8] == line_of(**{"#": "3", "rail": "│", "plan": "· c", "state": "pending", "tail": "pauses before this step"})
    assert pausing[9] == line_of(**{"#": "4", "rail": "│", "plan": "· d", "state": "pending"})
    assert painted_frames(out)[0][8].endswith("\x1b[33mpauses before this step\x1b[0m")

    # The engine reads the request after the step of b, and the run pauses before the step of c.
    paused_repo = bare_repo(tmp_path / "paused")
    run = a_chain_where_b_runs(paused_repo)
    run.add(1200, "step-end", ticket="b", state="built", reason=None)
    run.add(1200, "pause-requested", ticket="c", requested_at=None)
    run.add(1200, "paused", ticket="c", before="step")

    code, out = watch(paused_repo, monkeypatch, run.dir.name)

    assert code == 7
    paused = frames(out)[0]
    assert paused[0].startswith("run r1  paused  "), paused[0]
    assert paused[5] == line_of(**{"#": "3", "rail": "│", "plan": "· c", "state": "pending", "tail": "paused before this step"})
    assert painted_frames(out)[0][5].endswith("\x1b[33mpaused before this step\x1b[0m")
    assert not any("pauses before" in line for line in paused)


def test_when_the_terminal_gives_no_size_no_row_folds(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = frame_3b(repo)
    Clock(monkeypatch, STEP + 360)

    def no_size(*args: object) -> os.terminal_size:
        raise OSError("not a terminal")

    # `LINES` 0 is no value, so the size comes from the terminal, which does not give one.
    monkeypatch.setattr(os, "get_terminal_size", no_size)
    frame = first_frame(repo, monkeypatch, run, rows=0)

    assert not any("folded" in line for line in frame)
    assert [line[:3].strip() for line in frame if line[:3].strip().isdigit()] == [str(n) for n in range(1, 41)]
    assert frame[frame.index("─" * 120) + 1].endswith("started · python-specialist")  # and the feed is not cut
