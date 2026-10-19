"""The frame of the live view of `delegate watch`: the run line, the ticket table, the divider and the feed (#116, #143).

The spec is `design/design_handoff_delegate_terminal_output/`: the frames 3a and 1d to 1h of its README (design turn
3), and `docs/components.md`, `docs/tokens.md` and `docs/copy-deck.md`. A case writes the journal of a run in the state
directory of a real temporary git repository and calls `delegate.main(["watch", ...])`. The system boundaries are
faked as in `test_watch_live`: the terminal (a standard output whose `isatty` is true, `COLUMNS` and `LINES`), the
clock (`time.time`), the time zone (`TZ`), the locale (`LC_ALL`), and the wait between two polls (`time.sleep`).

The expected lines are the cells of components.md: the columns are 2 cells apart, each column is as wide as its design
track or as its widest value when that is wider, and the numbers align right. The rows are flat and in plan order: a
rail joins a row to the row above when that row is its blocker, and the phases of the running ticket stack in sub-rows.
When a line is wider than the terminal, the columns drop (#143). A line of the feed puts the subject on a track of its
own, 2 cells before the text.
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from delegate.cli import delegate

from .support import TITLE, Terminal, bare_repo, frames
from .test_watch_live import PYTEST, START, Run

# The ticket ids and the blockers of the frames of the design (screen-map.md): 72 after 66, 67 after 72, 70 after 69,
# and 71 after 70.
BLOCKED_BY: dict[str, tuple[str, ...]] = {"72": ("66",), "67": ("72",), "70": ("69",), "71": ("70",)}
EARLY = ["59", "60", "61", "62", "64", "65"]
SEVEN = ["66", "72", "67", "68", "69", "70", "71"]

# An SGR sequence: the paint of a role.
SGR = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def terminal_of_a_person(monkeypatch):
    """A UTF-8 locale, colour on, and the local time zone 13 hours east of UTC: the run starts at 16:00 local time."""
    monkeypatch.setenv("LC_ALL", "en_NZ.UTF-8")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TZ", "NZT-13")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class Clock:
    """The clock of the case, in seconds after the start of the run. A case moves it."""

    def __init__(self, monkeypatch, seconds: float) -> None:
        self.seconds = seconds
        monkeypatch.setattr(time, "time", lambda: (START + timedelta(seconds=self.seconds)).timestamp())


def built(run: Run, ticket: str, start: float) -> None:
    """A ticket that is built in 600 seconds: the specialist, the gate pytest, and a verifier that accepts."""
    run.step_start(start, ticket)
    run.agent(start, start + 300, ticket, "specialist", run.stream(f"{ticket}.specialist.stream.jsonl"))
    run.report(start + 300, ticket)
    run.gate(start + 300, start + 360, ticket, PYTEST)
    run.verify(start + 360, start + 600, ticket)
    run.add(start + 600, "step-end", ticket=ticket, state="built", reason=None)


def verifier_starts(run: Run, start: float, ticket: str, round_number: int = 0) -> None:
    """The verifier of a ticket starts. Its event stream is not there yet: the harness has not opened it."""
    run.add(start, "verify-start", ticket=ticket, round=round_number, agent="python-verifier", tier="verifier", model="m",
            commit="1" * 40, copy=str(run.dir / "copy"))
    run.add(start, "agent-start", ticket=ticket, role="verifier", round=round_number,
            event_stream=str(run.dir / "reports" / f"{ticket}.verifier-{round_number}.stream.jsonl"))


def specialist_and_gates(run: Run, ticket: str, start: float) -> None:
    run.step_start(start, ticket)
    run.agent(start, start + 300, ticket, "specialist", run.stream(f"{ticket}.specialist.stream.jsonl"))
    run.report(start + 300, ticket)
    run.gate(start + 300, start + 360, ticket, PYTEST)


def in_the_verifier(repo: Path, built_first: list[str]) -> Run:
    """The tickets of `built_first` are built, 600 seconds each, and ticket 70 is in the verifier since 360 seconds."""
    tickets = [*built_first, "70", "71"]
    run = Run(repo, *tickets, run_id="20261016T030000Z-abcd", blocked_by=BLOCKED_BY)
    for index, ticket in enumerate(built_first):
        built(run, ticket, index * 600)
    start = len(built_first) * 600
    specialist_and_gates(run, "70", start)
    verifier_starts(run, start + 360, "70")
    return run


def in_a_fixup(repo: Path) -> Run:
    """Frame 1h: 11 tickets are built, and the verifier of ticket 70 rejected; its fix-up runs since 480 seconds."""
    run = Run(repo, *EARLY, *SEVEN, run_id="20261016T030000Z-abcd", blocked_by=BLOCKED_BY)
    for index, ticket in enumerate([*EARLY, *SEVEN[:5]]):
        built(run, ticket, index * 600)
    start = 11 * 600
    specialist_and_gates(run, "70", start)
    run.verify(start + 360, start + 480, "70", "REJECT")
    fixup_starts(run, "70", start + 480)
    return run


def fixup_starts(run: Run, ticket: str, start: float) -> None:
    run.add(start, "fixup-start", ticket=ticket, round=1, rejected_commit="1" * 40, findings_file=str(run.dir / "findings" / "f.md"),
            agent="python-specialist", tier="strong", model="m")
    run.add(start, "agent-start", ticket=ticket, role="fix-up", round=1, event_stream=str(run.dir / "reports" / f"{ticket}.fixup.stream.jsonl"))


def watch(repo: Path, monkeypatch, run_id: str, *extra: str, out: io.StringIO | None = None, columns: int = 120, rows: int = 24,
          sleep=None) -> tuple[int, str]:
    """Run `delegate watch <run_id>` on a terminal of `columns` and `rows`. A wait that `sleep` does not handle fails the case."""
    out = Terminal() if out is None else out
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    def no_wait(seconds: float) -> None:
        raise AssertionError("watch waited, but the run has no more events")

    monkeypatch.setattr(time, "sleep", sleep or no_wait)
    code = delegate.main(["watch", run_id, "--repo", str(repo), *extra])
    return code, out.getvalue()


def ends_at_first_poll(run: Run):
    """A wait that ends the run, so the watch draws the frame of the poll, then the last frame."""

    def sleep(seconds: float) -> None:
        run.add(9000, "run-end", result="failed", built=[], failed=[], skipped=[])

    return sleep


def first_frame(repo: Path, monkeypatch, run: Run, columns: int, rows: int = 24) -> list[str]:
    code, out = watch(repo, monkeypatch, run.dir.name, columns=columns, rows=rows, sleep=ends_at_first_poll(run))
    assert code == 1
    return frames(out)[0]


# The harness has not opened the stream of the verifier yet, so its waiting line has 0 tokens.
FEED_OF_70 = [
    "16:50  #70  started · python-specialist",
    "16:55  #70  specialist finished · 5m · tokens 19k new, 0 cache · report ok",
    "16:56  #70  gates green (pytest 1m)",
    "16:56  #70  verifier started",
    "16:58  #70  verifier running · 2m · 0 new · 0 cache · quiet 2m20s",
]


def test_at_120_columns_the_frame_has_the_run_line_the_flat_table_in_plan_order_the_divider_and_the_feed_of_the_running_ticket(
    tmp_path, monkeypatch
):
    repo = bare_repo(tmp_path)
    run = in_the_verifier(repo, SEVEN[:5])
    Clock(monkeypatch, 3500)

    frame = first_frame(repo, monkeypatch, run, columns=120)

    assert frame == [
        "run abcd  running  █████████████░░░ 82%  5/7 ended  58m20s used  ETA 11m40s  213k new · 0 cache",
        "",
        "  #      plan    state       phase          after        used    new   cache",
        "  1      ● 66    ✓ built                               10m00s    39k       0",
        "  2  │   ● 72    ✓ built                               10m00s    39k       0",
        "  3  │   ● 67    ✓ built                               10m00s    39k       0",
        "  4      ● 68    ✓ built                               10m00s    39k       0",
        "  5      ● 69    ✓ built                               10m00s    39k       0",
        "  6  │   ▶ 70    running     specialist ✓               8m20s    19k       0  quiet 2m20s",
        "     │                       gates ✓",
        "     │                       verifier ●",
        "     │                       fix-up ○",
        "  7  │   · 71    pending",
        "─" * 120,
        *FEED_OF_70,
    ]


def test_at_80_columns_the_frame_drops_the_bar_then_new_and_cache_and_keeps_the_tracks_the_phase_labels_and_the_used_time(
    tmp_path, monkeypatch
):
    repo = bare_repo(tmp_path)
    run = in_the_verifier(repo, SEVEN[:5])
    Clock(monkeypatch, 3500)

    frame = first_frame(repo, monkeypatch, run, columns=80)

    assert frame == [
        "run abcd  running  82%  5/7 ended  58m20s used  ETA 11m40s  213k new · 0 cache",
        "",
        "  #      plan    state       phase          after        used",
        "  1      ● 66    ✓ built                               10m00s",
        "  2  │   ● 72    ✓ built                               10m00s",
        "  3  │   ● 67    ✓ built                               10m00s",
        "  4      ● 68    ✓ built                               10m00s",
        "  5      ● 69    ✓ built                               10m00s",
        "  6  │   ▶ 70    running     specialist ✓               8m20s  quiet 2m20s",
        "     │                       gates ✓",
        "     │                       verifier ●",
        "     │                       fix-up ○",
        "  7  │   · 71    pending",
        "─" * 80,
        *FEED_OF_70,
    ]


FEED_OF_A_FIXUP = [
    "17:50  #70  started · python-specialist",
    "17:55  #70  specialist finished · 5m · tokens 19k new, 0 cache · report ok",
    "17:56  #70  gates green (pytest 1m)",
    "17:56  #70  verifier started",
    "17:58  #70  verifier finished · 2m · tokens 19k new, 0 cache · report ok",
    "17:58  #70  REJECT · 1 finding",
    "17:58  #70  fix-up round 1 started",
    "17:59  #70  fix-up running · 1m · 0 new · 0 cache · quiet 1m20s",
]


def test_frame_1h_at_80_columns_and_24_rows_folds_the_built_rows_in_the_middle_and_the_rail_runs_through_the_fold(
    tmp_path, monkeypatch
):
    repo = bare_repo(tmp_path)
    run = in_a_fixup(repo)
    Clock(monkeypatch, 6600 + 560)

    frame = first_frame(repo, monkeypatch, run, columns=80)

    # The table gets 15 rows (#144): 23 rows of the terminal, less the run line, the blank line, the header, the divider
    # and 4 lines of the feed. The plan has 16. A fold of the tail (71) saves no row, so the built rows in the middle
    # fold: the first three rows and the three rows before 70 stay. The row under the fold is after the row above, so
    # the rail runs through the fold.
    assert frame == [
        "run abcd  running  90%  11/13 ended  1h59m used  ETA 10m40s  464k new · 0 cache",
        "",
        "  #      plan    state       phase          after        used",
        "  1      ● 59    ✓ built                               10m00s",
        "  2      ● 60    ✓ built                               10m00s",
        "  3      ● 61    ✓ built                               10m00s",
        "     │   ✓ 5 built                          62–72      50m00s  rows 4–8 folded",
        "  9  │   ● 67    ✓ built                               10m00s",
        " 10      ● 68    ✓ built                               10m00s",
        " 11      ● 69    ✓ built                               10m00s",
        " 12  │   ▶ 70    running     specialist ✓               9m20s  quiet 1m20s",
        "     │                       gates ✓",
        "     │                       verifier ✓",
        "     │                       fix-up ● 1/2",
        " 13  │   · 71    pending",
        "─" * 80,
        *FEED_OF_A_FIXUP[1:],  # the feed gets the 7 rows that are left, and keeps its newest lines
    ]
    assert len(frame) < 24  # the last line of the terminal stays free for the cursor


def test_at_120_columns_and_24_rows_the_built_rows_fold_and_every_column_stays(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = in_a_fixup(repo)
    Clock(monkeypatch, 6600 + 560)

    frame = first_frame(repo, monkeypatch, run, columns=120)

    assert frame[:10] == [
        "run abcd  running  ██████████████░░ 90%  11/13 ended  1h59m used  ETA 10m40s  464k new · 0 cache",
        "",
        "  #      plan    state       phase          after        used    new   cache",
        "  1      ● 59    ✓ built                               10m00s    39k       0",
        "  2      ● 60    ✓ built                               10m00s    39k       0",
        "  3      ● 61    ✓ built                               10m00s    39k       0",
        "     │   ✓ 5 built                          62–72      50m00s   193k       0  rows 4–8 folded",
        "  9  │   ● 67    ✓ built                               10m00s    39k       0",
        " 10      ● 68    ✓ built                               10m00s    39k       0",
        " 11      ● 69    ✓ built                               10m00s    39k       0",
    ]
    assert frame[10] == " 12  │   ▶ 70    running     specialist ✓               9m20s    39k       0  quiet 1m20s"
    assert frame[14:16] == [" 13  │   · 71    pending", "─" * 120]
    assert frame[16:] == FEED_OF_A_FIXUP[1:]


def test_a_frame_with_room_for_every_row_does_not_fold(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = in_a_fixup(repo)
    Clock(monkeypatch, 6600 + 560)

    frame = first_frame(repo, monkeypatch, run, columns=120, rows=40)

    rows = [line for line in frame[3:19] if line[:3].strip()]
    assert [(line[5], line[9]) for line in rows] == [(" ", "●")] * 7 + [("│", "●")] * 2 + [(" ", "●")] * 2 + [("│", "▶"), ("│", "·")]
    assert not any("11 built" in line for line in frame)


@pytest.mark.parametrize(
    ("extra", "tail", "waiting"),
    [
        pytest.param((), "⚠ idle 6m40s", "17:02  #70  verifier running · 6m · 0 new · 0 cache · ⚠ idle 6m40s", id="past-the-default-of-5-minutes"),
        pytest.param(("--stall-minutes", "10"), "quiet 6m40s", "17:02  #70  verifier running · 6m · 0 new · 0 cache · quiet 6m40s", id="within-the-stall-minutes"),
    ],
)
def test_an_agent_idle_past_the_stall_time_shows_a_warning_in_the_live_tail_and_in_the_waiting_line(tmp_path, monkeypatch, extra, tail, waiting):
    repo = bare_repo(tmp_path)
    run = in_the_verifier(repo, SEVEN[:5])
    Clock(monkeypatch, 3360 + 400)

    code, out = watch(repo, monkeypatch, run.dir.name, *extra, sleep=ends_at_first_poll(run))

    assert code == 1
    frame = frames(out)[0]
    assert frame[8].endswith(f"  {tail}"), frame[8]  # the row of 70, above the sub-rows of its phases
    assert frame[-1] == waiting


def test_a_running_gate_shows_its_short_name_and_the_age_of_its_log_in_the_live_tail(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "a", run_id="r1")
    run.step_start(0, "a")
    run.agent(0, 300, "a", "specialist", run.stream("a.specialist.stream.jsonl"))
    run.report(300, "a")
    log = run.dir / "gates" / "a" / "build-0.log"
    log.parent.mkdir(parents=True)
    log.write_text("")
    changed = (START + timedelta(seconds=330)).timestamp()
    os.utime(log, (changed, changed))
    run.add(310, "gate-start", ticket="a", round=0, phase="build", command=PYTEST, gate_log=str(log))
    Clock(monkeypatch, 360)

    frame = first_frame(repo, monkeypatch, run, columns=120)

    # The stack has no run of its gates that ended yet, so the phase `gates` has no gate count.
    assert frame[3:7] == [
        "  1      ▶ a     running     specialist ✓               6m00s    19k       0",
        " " * 29 + "gates ●".ljust(49) + "gate pytest · log 30s ago",  # the phase starts at cell 29, and the live tail at 78
        "                             verifier ○",
        "                             fix-up ○",
    ]


def test_in_economy_mode_the_feed_of_the_chain_verification_is_one_group_named_by_its_stack(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "a", "b", mode="economy")
    for ticket, start in (("a", 0), ("b", 600)):
        specialist_and_gates(run, ticket, start)
        run.add(start + 360, "step-end", ticket=ticket, state="built", reason=None)
    run.add(1000, "verify-start", ticket="b", round=0, agent="python-verifier", tier="verifier", model="m", commit="1" * 40,
            copy=str(run.dir / "copy"), stack="python", tickets=["a", "b"])
    run.add(1000, "agent-start", ticket="b", role="verifier", round=0, event_stream=str(run.dir / "reports" / "chain.stream.jsonl"))
    Clock(monkeypatch, 1100)

    frame = first_frame(repo, monkeypatch, run, columns=120)

    assert frame == [
        "run r1  running · verifier  ██████████░░░░░░ 66%  2/2 ended  18m20s used  ETA 4m20s  39k new · 0 cache",
        "",
        "  #      plan    state       phase          after        used    new   cache",
        "  1      ● a     ✓ built                                6m00s    19k       0",
        "  2      ● b     ✓ built                                6m00s    19k       0",
        "─" * 120,
        "16:16  stack python  verifier started (#a, #b)",
        "16:18  stack python  verifier running · 1m · 0 new · 0 cache · quiet 1m40s",
    ]


# The journal of frames 1d to 1g: a REJECT and its fix-up, a continuation, a step that fails, a skip, a pause, a resume,
# and the run-end. Ticket b is after a, c is after b, and d has no blocker.
SEQUENCE: dict[str, tuple[str, ...]] = {"b": ("a",), "c": ("b",)}


def rejected_and_in_a_fixup(run: Run) -> None:
    specialist_and_gates(run, "a", 0)
    run.verify(360, 480, "a", "REJECT")
    fixup_starts(run, "a", 480)


def fixup_accepted_and_b_continues_twice(run: Run) -> None:
    stream = run.stream("a.fixup.stream.jsonl")
    run.add(600, "fixup-result", ticket="a", round=1, exit_status=0, end_state="finished", session_id="s", event_stream=stream)
    run.add(600, "fixup-report", ticket="a", round=1, valid=True, path=str(run.dir / "reports" / "a.fixup.json"), reason=None,
            status="committed", blocked_reason=None)
    verifier_starts(run, 620, "a", round_number=1)
    stream = run.stream("a.verifier-1.stream.jsonl")
    run.add(700, "verify-result", ticket="a", round=1, exit_status=0, end_state="finished", session_id="s", event_stream=stream)
    run.add(700, "verify-report", ticket="a", round=1, valid=True, path=str(run.dir / "verdict.json"), reason=None)
    run.add(700, "verdict", ticket="a", round=1, mode="scoped", verdict="ACCEPT", findings=[], unverified=[], report=str(run.dir / "verdict.json"))
    run.add(700, "step-end", ticket="a", state="built", reason=None)
    run.step_start(700, "b")
    stream = run.stream("b.specialist.stream.jsonl")
    run.agent(700, 1000, "b", "specialist", stream, end_state="capped")
    run.add(1000, "continuation", ticket="b", count=1, limit=2, trigger="capped", mode="resume", resume_session="s", commits=[], reason="capped")
    run.agent(1000, 1200, "b", "specialist", stream)
    run.report(1200, "b")
    run.gate(1200, 1260, "b", PYTEST, green=False)
    run.add(1260, "continuation", ticket="b", count=2, limit=2, trigger="gates-red", mode="resume", resume_session="s", commits=[], reason="red")


def b_fails(run: Run) -> None:
    run.agent(1260, 1500, "b", "specialist", str(run.dir / "reports" / "b.specialist.stream.jsonl"))
    run.report(1500, "b")
    run.gate(1500, 1560, "b", PYTEST, green=False)
    run.add(1560, "step-end", ticket="b", state="failed", reason=f"gates red: {PYTEST}")


def c_is_skipped_and_the_run_pauses_before_d(run: Run) -> None:
    run.add(1560, "skip", ticket="c", blockers=["b"], reason="blocked by b, which failed")
    run.add(1560, "pause-requested", ticket="d", requested_at=None)
    run.add(1560, "paused", ticket="d", before="step")


def resumed_and_d_is_built(run: Run) -> None:
    run.add(2000, "resume", run_id="r1", built=["a"], failed=["b"], skipped=["c"])
    built(run, "d", 2000)
    run.add(2600, "run-end", result="failed", built=["a", "d"], failed=["b"], skipped=["c"])


def test_a_journal_with_a_reject_a_continuation_a_skip_a_pause_and_a_run_end_gives_the_frames_1d_1e_1f_and_1g_in_order(
    tmp_path, monkeypatch
):
    repo = bare_repo(tmp_path)
    run = Run(repo, "a", "b", "c", "d", blocked_by=SEQUENCE)
    rejected_and_in_a_fixup(run)
    clock = Clock(monkeypatch, 500)

    def next_events(seconds: float) -> None:
        if clock.seconds != 1270:  # the fix-up is active, so the wait has steps that draw its spinner (#147)
            fixup_accepted_and_b_continues_twice(run)
            clock.seconds = 1270

    # 1d: the REJECT and the fix-up. Then 1e: the second continuation is a problem, and the watch exits.
    code, out = watch(repo, monkeypatch, "r1", sleep=next_events)
    assert code == 3
    drawn = frames(out)
    fixup, problem = drawn[0], drawn[-1]
    assert fixup == [
        "run r1  running  ███░░░░░░░░░░░░░ 20%  0/4 ended  8m20s used  ETA -  39k new · 0 cache",
        "",
        "  #      plan    state       phase          after        used    new   cache",
        "  1      ▶ a     running     specialist ✓               8m20s    39k       0  ⠋",
        "     │                       gates ✓",
        "     │                       verifier ✓",
        "     │                       fix-up ● 1/2",
        "  2  │   · b     pending",
        "  3  │   · c     pending",
        "  4      · d     pending",
        "─" * 120,
        "16:00  #a   started · python-specialist",
        "16:05  #a   specialist finished · 5m · tokens 19k new, 0 cache · report ok",
        "16:06  #a   gates green (pytest 1m)",
        "16:06  #a   verifier started",
        "16:08  #a   verifier finished · 2m · tokens 19k new, 0 cache · report ok",
        "16:08  #a   REJECT · 1 finding",
        "16:08  #a   fix-up round 1 started",
        "16:08  #a   fix-up running · 20s · 0 new · 0 cache · ⠋",
    ]
    assert problem == [
        "run r1  running  ████░░░░░░░░░░░░ 25%  1/4 ended  21m10s used  ETA 25m30s  97k new · 0 cache",
        "",
        "  #      plan    state       phase          after        used    new   cache",
        "  1      ● a     ✓ built                               11m40s    77k       0",
        "  2  │   ▶ b     running     specialist ●               9m30s    19k       0  cont 2/2",
        "     │                       gates ○",
        "     │                       verifier ○",
        "     │                       fix-up ○",
        "  3  │   · c     pending",
        "  4      · d     pending",
        "─" * 120,
        "16:11  #b   started · python-specialist",
        "16:16  #b   specialist capped · 5m · tokens 19k new, 0 cache",
        "16:16  #b   continuation 1 of 2 · capped",
        "16:20  #b   specialist finished · 3m · tokens 19k new, 0 cache · report ok",
        "16:21  #b   gates red (pytest 1m red)",
        "16:21  ✗ #b the step continued 2 times (limit 2, trigger gates-red)",
    ]
    position = int(out.splitlines()[-1].split()[1])

    # 1e: the step fails on red gates, which is a problem. The live tail of the row of b gives the reason, cut at the
    # width of the terminal, and the ticket that runs next has no line yet, so the lines of b stay.
    b_fails(run)
    clock.seconds = 1560
    code, out = watch(repo, monkeypatch, "r1", "--from", str(position))
    assert code == 3
    assert frames(out) == [[
        "run r1  running  ████████░░░░░░░░ 50%  2/4 ended  26m00s used  ETA 26m00s  97k new · 0 cache",
        "",
        "  #      plan    state       phase          after        used    new   cache",
        "  1      ● a     ✓ built                               11m40s    77k       0",
        ("  2  │   ✗ b     failed                                14m20s    19k       0  " + f"gates red: {PYTEST}")[:120],
        "  3  │   · c     pending",
        "  4      · d     pending",
        "─" * 120,
        "16:25  #b   specialist finished · 4m · tokens 19k new, 0 cache · report ok",
        "16:26  #b   gates red (pytest 1m red)",
        "16:26  ✗ #b the gates are not green: nix develop -c python3 -m pytest -q",
    ]]
    position = int(out.splitlines()[-1].split()[1])

    # 1f: c is skipped, and the run pauses before the step of d. A paused run has no ETA.
    c_is_skipped_and_the_run_pauses_before_d(run)
    code, out = watch(repo, monkeypatch, "r1", "--from", str(position))
    assert code == 7
    assert frames(out) == [[
        "run r1  paused  ████████████░░░░ 75%  3/4 ended  26m00s used  97k new · 0 cache",
        "",
        "  #      plan    state       phase          after        used    new   cache",
        "  1      ● a     ✓ built                               11m40s    77k       0",
        ("  2  │   ✗ b     failed                                14m20s    19k       0  " + f"gates red: {PYTEST}")[:120],
        "  3  │   – c     skipped                                                      blocked by b, which failed",
        "  4      · d     pending                                                      paused before this step",
        "─" * 120,
        "16:26  #c   skipped · blocked by b, which failed",
        "16:26  paused · before ticket d · go on with delegate run --resume r1",
    ]]
    position = int(out.splitlines()[-1].split()[1])

    # 1g: the run resumes, d is built, and the run ends. The feed starts again with the line `started` of d.
    resumed_and_d_is_built(run)
    code, out = watch(repo, monkeypatch, "r1", "--from", str(position))
    assert code == 1
    assert frames(out) == [[
        "run r1  failed  ████████████████ 100%  4/4 ended  43m20s used  135k new · 0 cache",
        "",
        "  #      plan    state       phase          after        used    new   cache",
        "  1      ● a     ✓ built                               11m40s    77k       0",
        ("  2  │   ✗ b     failed                                14m20s    19k       0  " + f"gates red: {PYTEST}")[:120],
        "  3  │   – c     skipped                                                      blocked by b, which failed",
        "  4      ● d     ✓ built                               10m00s    39k       0",
        "─" * 120,
        "16:33  #d   started · python-specialist",
        "16:38  #d   specialist finished · 5m · tokens 19k new, 0 cache · report ok",
        "16:39  #d   gates green (pytest 1m)",
        "16:39  #d   verifier started",
        "16:43  #d   verifier finished · 4m · tokens 19k new, 0 cache · report ok",
        "16:43  #d   ✓ ACCEPT · built",
        "16:43  run failed · 43m · tokens 135k new, 0 cache",
    ]]


def painted_frames(out: str) -> list[list[str]]:
    """The frames as the terminal gets them, with the paint of each role, and with no other control sequence."""
    chunks = TITLE.sub("", out).split("\x1b[H")[1:]
    return [chunk.split("\x1b[J")[0].replace("\x1b[K", "").replace("\x1b[2J", "").splitlines() for chunk in chunks]


def test_each_part_of_the_frame_has_the_paint_of_its_role(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = in_a_fixup(repo)
    Clock(monkeypatch, 6600 + 560)

    _, out = watch(repo, monkeypatch, run.dir.name, rows=60, sleep=ends_at_first_poll(run))

    frame = painted_frames(out)[0]
    run_line, header, divider = frame[0], frame[2], next(line for line in frame if "─" * 50 in line)
    assert run_line.startswith("run \x1b[1mabcd\x1b[0m  running  \x1b[32m" + "█" * 14 + "\x1b[0m\x1b[2m░░\x1b[0m 90%")
    assert "\x1b[2m1h59m used\x1b[0m" in run_line and "\x1b[2m464k new · 0 cache\x1b[0m" in run_line
    assert header.startswith("  \x1b[2m#\x1b[0m" + " " * 6 + "\x1b[2mplan\x1b[0m  ") and header.endswith("\x1b[2mcache\x1b[0m")
    assert divider == "\x1b[2m" + "─" * 120 + "\x1b[0m"
    built_row = next(line for line in frame if " 66" in SGR.sub("", line))
    assert "\x1b[32m●\x1b[0m" in built_row and "\x1b[32m✓\x1b[0m\x1b[2m built\x1b[0m" in built_row
    assert "\x1b[34m│\x1b[0m" in next(line for line in frame if " 72" in SGR.sub("", line))  # the rail is `plan`
    index = next(index for index, line in enumerate(frame) if " 70" in SGR.sub("", line))
    running, fixup = frame[index], frame[index + 3]
    assert "\x1b[36m▶\x1b[0m" in running and "\x1b[36m\x1b[1m70\x1b[0m\x1b[0m" in running
    assert "\x1b[36m\x1b[1mrunning\x1b[0m\x1b[0m" in running and "\x1b[2mspecialist \x1b[0m\x1b[32m✓\x1b[0m" in running
    assert "\x1b[2mquiet 1m20s\x1b[0m" in running  # quiet for 1m20s: `dim`, with no spinner (#147)
    assert fixup == "     \x1b[34m│\x1b[0m" + " " * 23 + "\x1b[33mfix-up ● 1/2\x1b[0m"  # the sub-row of the fix-up is `warn`
    feed = frame[frame.index(divider) + 1:]
    assert feed[0] == "\x1b[2m17:50\x1b[0m  \x1b[36m\x1b[1m#70\x1b[0m\x1b[0m  started · python-specialist"
    assert "\x1b[32mgates green\x1b[0m\x1b[2m (pytest 1m)\x1b[0m" in feed[2]
    assert "\x1b[2mtokens 19k new, 0 cache\x1b[0m" in feed[1]
    assert "\x1b[33m\x1b[1mREJECT · 1 finding\x1b[0m\x1b[0m" in feed[5]
    assert "\x1b[33mfix-up round 1 started\x1b[0m" in feed[6]
    # The waiting line is `now`, and its tokens are `dim`.
    assert feed[7] == "\x1b[36m17:59  #70  fix-up running · 1m · \x1b[0m\x1b[2m0 new · 0 cache\x1b[0m\x1b[36m · \x1b[0m\x1b[2mquiet 1m20s\x1b[0m"


def test_a_problem_line_is_bad_and_an_accept_has_a_check_in_ok_and_a_bold_word(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "a")
    built(run, "a", 0)
    run.step_start(600, "b")
    run.add(600, "step-end", ticket="b", state="failed", reason="crashed: OSError: disk full")
    Clock(monkeypatch, 700)

    code, out = watch(repo, monkeypatch, "r1")

    assert code == 3
    (frame,) = painted_frames(out)
    assert frame[-1] == "\x1b[31m16:10  ✗ #b the step failed: crashed: OSError: disk full\x1b[0m"


def test_with_no_color_set_the_live_view_writes_no_sgr_code(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = in_a_fixup(repo)
    Clock(monkeypatch, 6600 + 560)
    monkeypatch.setenv("NO_COLOR", "1")

    _, out = watch(repo, monkeypatch, run.dir.name, sleep=ends_at_first_poll(run))

    assert len(frames(out)) == 2
    assert SGR.search(out) is None


def test_to_a_standard_output_that_is_not_a_terminal_watch_writes_no_escape_code(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = in_a_fixup(repo)
    run.add(9000, "run-end", result="failed", built=[], failed=[], skipped=[])

    code, out = watch(repo, monkeypatch, run.dir.name, out=io.StringIO())

    assert code == 1
    assert "\x1b" not in out
    assert out.splitlines()[0].startswith("1 2026-10-16T03:00:00+00:00 run-start run_id=20261016T030000Z-abcd ")


def test_with_a_locale_that_is_not_utf8_the_frame_has_the_ascii_glyphs_in_the_same_columns(tmp_path, monkeypatch):
    unicode_repo, ascii_repo = (bare_repo(tmp_path / name) for name in ("unicode", "ascii"))
    unicode_run, ascii_run = in_a_fixup(unicode_repo), in_a_fixup(ascii_repo)
    Clock(monkeypatch, 6600 + 560)
    unicode = first_frame(unicode_repo, monkeypatch, unicode_run, columns=120)
    monkeypatch.setenv("LC_ALL", "C")

    ascii_frame = first_frame(ascii_repo, monkeypatch, ascii_run, columns=120)

    assert [len(line) for line in ascii_frame] == [len(line) for line in unicode]
    assert ascii_frame[5:15] == [
        "  3      * 61    + built                               10m00s    39k       0",
        "     |   + 5 built                          62-72      50m00s   193k       0  rows 4-8 folded",
        "  9  |   * 67    + built                               10m00s    39k       0",
        " 10      * 68    + built                               10m00s    39k       0",
        " 11      * 69    + built                               10m00s    39k       0",
        " 12  |   > 70    running     specialist +               9m20s    39k       0  quiet 1m20s",
        "     |                       gates +",
        "     |                       verifier +",
        "     |                       fix-up * 1/2",
        " 13  |   . 71    pending",
    ]
    assert ascii_frame[0] == "run abcd  running  ##############.. 90%  11/13 ended  1h59m used  ETA 10m40s  464k new . 0 cache"
    assert ascii_frame[15] == "-" * 120
    # The run line, the table and the divider are ASCII. The feed keeps its joins (` · `).
    assert all(line.isascii() for line in ascii_frame[:16])
    assert ascii_frame[20:22] == ["17:58  #70  REJECT · 1 finding", "17:58  #70  fix-up round 1 started"]


def test_the_feed_puts_the_id_on_its_own_track_and_the_waiting_line_gives_the_tokens_of_its_agent_in_dim(tmp_path, monkeypatch):
    # The local time zone is 4 hours 22 minutes east of UTC: the run starts at 07:22 local time.
    monkeypatch.setenv("TZ", "XXX-4:22")
    time.tzset()
    repo = bare_repo(tmp_path)
    run = Run(repo, "127", run_id="20261016T030000Z-abcd")
    run.step_start(0, "127")
    run.add(0, "agent-start", ticket="127", role="specialist", round=0,
            event_stream=run.stream("127.specialist.stream.jsonl", "capped.jsonl", cut=True))
    Clock(monkeypatch, 130)

    code, out = watch(repo, monkeypatch, run.dir.name, "--stall-minutes", "1000000", sleep=ends_at_first_poll(run))

    assert code == 1
    feed = frames(out)[0][-2:]
    # The cut stream of the specialist has 12k new tokens and 14k cache reads (as in `test_watch_live`).
    assert feed[0] == "07:22  #127  started · python-specialist"
    assert re.fullmatch(r"07:24  #127  specialist running · 2m · 12k new · 14k cache · (?:quiet \S+|[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏])", feed[1]), feed[1]
    assert "\x1b[2m12k new · 14k cache\x1b[0m" in out  # the token part is `dim`


def test_the_id_track_of_the_feed_is_at_least_3_cells_wide(tmp_path, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "a", run_id="r1")
    run.step_start(0, "a")
    run.add(0, "agent-start", ticket="a", role="specialist", round=0, event_stream=str(run.dir / "reports" / "a.stream.jsonl"))
    Clock(monkeypatch, 20)

    frame = first_frame(repo, monkeypatch, run, columns=120)

    # The harness has not opened the stream of the specialist yet, so it has 0 tokens.
    assert frame[-2:] == ["16:00  #a   started · python-specialist", "16:00  #a   specialist running · 20s · 0 new · 0 cache · ⠋"]
