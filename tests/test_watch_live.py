"""`delegate watch` on a terminal: the live progress block of the snapshot at the top, and a readable feed of the events below.

A case writes the journal of a run, with the time of each event, in the state
directory of a real temporary git repository, and calls
`delegate.main(["watch", ...])`. The system boundaries are faked: the terminal
(a standard output whose `isatty` is true, and `COLUMNS` and `LINES`), the clock
(`time.time`), the time zone (`TZ`), and the wait between two polls
(`time.sleep`), which appends the next events of a live run.

Standard output that is not a terminal, and `--raw`, give the raw lines of the
earlier `watch`. The golden lines below are the output of `watch` before the
live view existed, so a change of the raw form fails a case.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from delegate.cli import delegate

from . import claude_fake
from .support import TITLE, Terminal, bare_repo, frames, journal_path, without_titles

START = datetime(2026, 10, 16, 3, 0, 0, tzinfo=timezone.utc)
PYTEST = "nix develop -c python3 -m pytest -q"
DOCS = 'uvx "zensical==0.0.2" build --strict'


def at(seconds: float) -> str:
    return (START + timedelta(seconds=seconds)).isoformat()


class Run:
    """The journal of one run, written line by line with the time of each event, and the event streams of its agents."""

    def __init__(self, repo: Path, *tickets: str, mode: str = "assure") -> None:
        self.path = journal_path(repo, "r1")
        self.path.parent.mkdir(parents=True)
        self.dir = self.path.parent
        self.seq = 0
        self.add(0, "run-start", run_id="r1", workflow="/w/workflow.toml", mode=mode, adapter="claude-code",
                 base_branch="main", run_branch="run/demo", tickets=list(tickets))

    def add(self, seconds: float, event: str, **fields: object) -> None:
        self.seq += 1
        with self.path.open("a") as handle:
            handle.write(json.dumps({"seq": self.seq, "time": at(seconds), "event": event, **fields}) + "\n")

    def stream(self, name: str, fixture: str = "finished.jsonl", cut: bool = False) -> str:
        """An event stream in the run directory. `cut` keeps the first seven lines and a part of the eighth: an agent that runs."""
        text = (claude_fake.FIXTURES / fixture).read_text()
        if cut:
            lines = text.splitlines(keepends=True)
            text = "".join(lines[:7]) + lines[7][:120]
        path = self.dir / "reports" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        return str(path)

    def step_start(self, seconds: float, ticket: str) -> None:
        self.add(seconds, "step-start", ticket=ticket, stack="python", branch=f"run/demo-{ticket}", worktree=f"/w/{ticket}",
                 base_commit="0" * 40, agent="python-specialist", tier="strong", model="m")

    def agent(
        self, start: float, end: float, ticket: str, role: str, stream: str, result: str = "adapter-result", end_state: str = "finished"
    ) -> None:
        self.add(start, "agent-start", ticket=ticket, role=role, round=0, event_stream=stream)
        self.add(end, result, ticket=ticket, **({"round": 0} if result == "verify-result" else {}), exit_status=0,
                 end_state=end_state, session_id="s", event_stream=stream)

    def report(self, seconds: float, ticket: str, valid: bool = True, reason: str | None = None) -> None:
        self.add(seconds, "report-validation", ticket=ticket, valid=valid, path=str(self.dir / "reports" / f"{ticket}.specialist.json"),
                 reason=reason, status="committed" if valid else None, blocked_reason=None)

    def gate(self, start: float, end: float, ticket: str, command: str, green: bool = True) -> None:
        log = self.dir / "gates" / ticket / "build-0.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("")
        self.add(start, "gate-start", ticket=ticket, round=0, phase="build", command=command, gate_log=str(log))
        self.add(end, "gate-result", ticket=ticket, round=0, phase="build", command=command, commit="1" * 40,
                 exit_status=0 if green else 1, green=green, output_tail="ok")

    def verify(self, start: float, end: float, ticket: str, verdict: str = "ACCEPT", chain: list[str] | None = None) -> None:
        """A verifier of one ticket, or with `chain` the verifier of the chain of the stack `python` in `economy` mode."""
        of_chain: dict[str, object] = {} if chain is None else {"stack": "python", "tickets": chain}
        self.add(start, "verify-start", ticket=ticket, round=0, agent="python-verifier", tier="verifier", model="m", commit="1" * 40,
                 copy=str(self.dir / "copy"), **of_chain)
        stream = self.stream(f"{ticket}.verifier.stream.jsonl")
        self.agent(start, end, ticket, "verifier", stream, result="verify-result")
        self.add(end, "verify-report", ticket=ticket, round=0, valid=True, path=str(self.dir / "verdict.json"), reason=None)
        if chain is not None:
            of_chain.update(mapped={}, unmapped=[])
        self.add(end, "verdict", ticket=ticket, round=0, mode="full", verdict=verdict, findings=[] if verdict == "ACCEPT" else ["a: x"],
                 unverified=[], report=str(self.dir / "verdict.json"), **of_chain)


def ticket_a_is_built(run: Run) -> None:
    """Ticket a: the specialist runs 36 minutes, the gates 16 minutes and 1 second, the verifier 7 minutes 59 seconds."""
    run.step_start(0, "a")
    run.agent(0, 2160, "a", "specialist", run.stream("a.specialist.stream.jsonl"))
    run.report(2160, "a")
    run.gate(2160, 3120, "a", PYTEST)
    run.gate(3120, 3121, "a", DOCS)
    run.verify(3121, 3600, "a")
    run.add(3600, "run-branch-advance", ticket="a", run_branch="run/demo", from_commit="0" * 40, to_commit="1" * 40)
    run.add(3600, "step-end", ticket="a", state="built", reason=None)


def a_live_run(repo: Path) -> Run:
    """Ticket a is built, and the specialist of ticket b runs since 3600 seconds."""
    run = Run(repo, "a", "b")
    ticket_a_is_built(run)
    run.step_start(3600, "b")
    run.add(3600, "agent-start", ticket="b", role="specialist", round=0, event_stream=run.stream("b.specialist.stream.jsonl", "capped.jsonl", cut=True))
    return run


def ticket_b_ends(run: Run) -> None:
    stream = str(run.dir / "reports" / "b.specialist.stream.jsonl")
    run.add(4000, "adapter-result", ticket="b", exit_status=0, end_state="finished", session_id="s", event_stream=stream)
    run.report(4000, "b")
    run.add(4000, "step-end", ticket="b", state="built", reason=None)
    run.add(4000, "run-end", result="built", built=["a", "b"], failed=[], skipped=[])


@pytest.fixture
def local_zone(monkeypatch):
    """Set the local time zone of the process to a POSIX zone, and set it back after the case."""

    def set_zone(zone: str) -> None:
        monkeypatch.setenv("TZ", zone)
        time.tzset()

    yield set_zone
    monkeypatch.undo()
    time.tzset()


def watch(
    repo: Path, monkeypatch, *extra: str, terminal: bool = True, columns: int = 200, rows: int = 50, then=None, sleep=None
) -> tuple[int, str]:
    """Run `delegate watch` with standard output to a terminal or to a pipe.

    The first wait calls `then`, and a later wait fails the case. A case that waits more than once gives its own `sleep`.
    """
    out = Terminal() if terminal else io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))
    waits: list[float] = []

    def wait_once(seconds: float) -> None:
        assert then is not None and not waits, "watch waited, but the run has no more events"
        waits.append(seconds)
        then()

    monkeypatch.setattr(time, "sleep", sleep or wait_once)
    code = delegate.main(["watch", "--repo", str(repo), *extra])
    return code, out.getvalue()


FEED = [
    "16:00  run r1 started · assure · 2 tickets",
    "16:00  #a started · python-specialist",
    "16:36  #a specialist finished · 36m · tokens 19k new, 0 cache · report ok",
    "16:52  #a gates green (pytest 16m, zensical 1s)",
    "16:52  #a verifier started",
    "17:00  #a verifier finished · 7m · tokens 19k new, 0 cache · report ok",
    "17:00  #a ✓ ACCEPT · built",
    "17:00  #b started · python-specialist",
]

FEED_AFTER_THE_END = [
    "17:06  #b specialist finished · 6m · tokens 12k new, 14k cache · report ok",
    "17:06  #b built",
    "17:06  run built · 1h06m · tokens 50k new, 14k cache",
]


def test_watch_on_a_terminal_draws_the_live_block_at_the_top_and_the_feed_below_and_draws_them_again_at_each_poll(
    tmp_path, monkeypatch, local_zone
):
    local_zone("NZT-13")  # 13 hours east of UTC: the run starts at 03:00 UTC, 16:00 local time
    repo = bare_repo(tmp_path)
    run = a_live_run(repo)
    monkeypatch.setattr(time, "time", lambda: (START + timedelta(seconds=3650)).timestamp())

    code, out = watch(repo, monkeypatch, then=lambda: ticket_b_ends(run))

    assert code == 0
    first, last = frames(out)
    assert first == [
        "run r1 running 50%, 1 of 2 ended, 1h00m used, ETA 59m10s, tokens 50k new, 14k cache",
        "  a built, 1h00m used, tokens 39k new, 0 cache",
        "  b running specialist, 50s used, tokens 12k new, 14k cache, last activity 7d08h ago",
        "",
        *FEED,
    ]
    assert last == [
        "run r1 built 100%, 2 of 2 ended, 1h06m used, tokens 50k new, 14k cache",
        "  a built, 1h00m used, tokens 39k new, 0 cache",
        "  b built, 6m40s used, tokens 12k new, 14k cache",
        "",
        *FEED,
        *FEED_AFTER_THE_END,
    ]
    assert out.splitlines()[-1] == f"position {run.seq}"


def test_the_feed_gives_the_local_time_of_each_event(tmp_path, monkeypatch, local_zone):
    local_zone("EST+5")  # 5 hours west of UTC: 03:00 UTC is 22:00 of the day before
    repo = bare_repo(tmp_path)
    run = a_live_run(repo)
    ticket_b_ends(run)

    _, out = watch(repo, monkeypatch)

    (last,) = frames(out)
    assert [line[:5] for line in last if re.match(r"\d\d:\d\d  ", line)] == (
        ["22:00", "22:00", "22:36", "22:52", "22:52", "23:00", "23:00", "23:00", "23:06", "23:06", "23:06"]
    )


@pytest.mark.parametrize("columns", [30, 60, 200])
def test_the_live_view_has_no_line_wider_than_the_terminal_no_absolute_path_and_no_none(tmp_path, monkeypatch, columns, local_zone):
    local_zone("NZT-13")
    repo = bare_repo(tmp_path)
    run = a_live_run(repo)
    monkeypatch.setattr(time, "time", lambda: (START + timedelta(seconds=3650)).timestamp())

    code, out = watch(repo, monkeypatch, columns=columns, then=lambda: ticket_b_ends(run))

    assert code == 0
    drawn = [line for frame in frames(out) for line in frame]
    assert len(drawn) > 20
    for line in drawn:
        assert len(line) <= columns, line
        assert not re.search(r"(^|\s)/", line), line
        assert "None" not in line, line
    assert str(tmp_path) not in out


def test_a_frame_of_a_run_that_goes_on_fits_the_height_of_the_terminal_and_keeps_the_newest_events(tmp_path, monkeypatch, local_zone):
    local_zone("NZT-13")
    repo = bare_repo(tmp_path)
    run = a_live_run(repo)
    monkeypatch.setattr(time, "time", lambda: (START + timedelta(seconds=3650)).timestamp())

    _, out = watch(repo, monkeypatch, rows=8, then=lambda: ticket_b_ends(run))

    first, last = frames(out)
    # The block has 3 lines and a blank line under it; the last line of the terminal stays free for the cursor.
    assert first == first[:4] + FEED[-3:]
    assert last[4:] == FEED + FEED_AFTER_THE_END  # the last frame keeps the whole feed, so the terminal scrolls it


def test_a_problem_is_a_line_that_starts_with_a_cross_and_names_a_file_with_no_path(tmp_path, monkeypatch, local_zone):
    local_zone("NZT-13")
    repo = bare_repo(tmp_path)
    run = Run(repo, "a")
    run.step_start(0, "a")
    run.agent(0, 2160, "a", "specialist", run.stream("a.specialist.stream.jsonl"))
    run.report(2160, "a", valid=False, reason=f"report missing: {run.dir}/reports/a.specialist.json")

    code, out = watch(repo, monkeypatch)

    assert code == 3
    (last,) = frames(out)
    assert last[-2:] == [
        "16:36  #a specialist finished · 36m · tokens 19k new, 0 cache",
        "16:36  ✗ #a the specialist returned no valid result: report missing: a.specialist.json",
    ]
    assert out.splitlines()[-1] == "position 5"


def test_the_verdict_of_an_economy_chain_names_its_stack_and_joins_the_end_of_the_run(tmp_path, monkeypatch, local_zone):
    local_zone("NZT-13")
    repo = bare_repo(tmp_path)
    run = Run(repo, "a", mode="economy")
    run.step_start(0, "a")
    run.agent(0, 600, "a", "specialist", run.stream("a.specialist.stream.jsonl"))
    run.report(600, "a")
    run.gate(600, 660, "a", PYTEST)
    run.add(660, "step-end", ticket="a", state="built", reason=None)
    run.verify(700, 1000, "a", chain=["a"])
    run.add(1000, "run-branch-advance", ticket="a", run_branch="run/demo", from_commit="0" * 40, to_commit="1" * 40)
    run.add(1000, "run-end", result="built", built=["a"], failed=[], skipped=[])

    code, out = watch(repo, monkeypatch)

    assert code == 0
    (last,) = frames(out)
    assert last[last.index("") + 1:] == [
        "16:00  run r1 started · economy · 1 ticket",
        "16:00  #a started · python-specialist",
        "16:10  #a specialist finished · 10m · tokens 19k new, 0 cache · report ok",
        "16:11  #a gates green (pytest 1m) · built",
        "16:11  stack python verifier started (#a)",
        "16:16  stack python verifier finished · 5m · tokens 19k new, 0 cache · report ok",
        "16:16  stack python ✓ ACCEPT · run built · 16m · tokens 39k new, 0 cache",
    ]


def test_a_capped_specialist_a_continuation_and_red_gates_each_have_a_line_and_the_second_continuation_is_a_problem(
    tmp_path, monkeypatch, local_zone
):
    local_zone("NZT-13")
    repo = bare_repo(tmp_path)
    run = Run(repo, "a")
    run.step_start(0, "a")
    stream = run.stream("a.specialist.stream.jsonl")
    run.agent(0, 2160, "a", "specialist", stream, end_state="capped")
    run.add(2160, "continuation", ticket="a", count=1, limit=2, trigger="capped", mode="resume", resume_session="s", commits=[], reason="capped")
    run.agent(2160, 2400, "a", "specialist", stream)
    run.report(2400, "a")
    run.gate(2400, 2460, "a", PYTEST, green=False)
    run.add(2460, "continuation", ticket="a", count=2, limit=2, trigger="gates-red", mode="resume", resume_session="s", commits=[], reason="red")

    code, out = watch(repo, monkeypatch)

    assert code == 3
    (last,) = frames(out)
    assert last[last.index("") + 1:] == [
        "16:00  run r1 started · assure · 1 ticket",
        "16:00  #a started · python-specialist",
        "16:36  #a specialist capped · 36m · tokens 19k new, 0 cache",
        "16:36  #a continuation 1 of 2 · capped",
        "16:40  #a specialist finished · 4m · tokens 19k new, 0 cache · report ok",
        "16:41  #a gates red (pytest 1m red)",
        "16:41  ✗ #a the step continued 2 times (limit 2, trigger gates-red)",
    ]


# The raw lines of each case below are the output of `delegate watch` before the live view existed (commit 42cf836).
# `{dir}` is the run directory, which holds the streams, the gate logs and the reports.
RAW_OF_THE_WHOLE_RUN = """\
1 2026-10-16T03:00:00+00:00 run-start run_id=r1 workflow=/w/workflow.toml mode=assure adapter=claude-code base_branch=main run_branch=run/demo
2 2026-10-16T03:00:00+00:00 step-start ticket=a stack=python branch=run/demo-a worktree=/w/a base_commit=0000000000000000000000000000000000000000 agent=python-specialist tier=strong model=m
3 2026-10-16T03:00:00+00:00 agent-start ticket=a role=specialist round=0 event_stream={dir}/reports/a.specialist.stream.jsonl
4 2026-10-16T03:36:00+00:00 adapter-result ticket=a exit_status=0 end_state=finished session_id=s event_stream={dir}/reports/a.specialist.stream.jsonl
5 2026-10-16T03:36:00+00:00 report-validation ticket=a valid=True path={dir}/reports/a.specialist.json reason=None status=committed blocked_reason=None
6 2026-10-16T03:36:00+00:00 gate-start ticket=a round=0 phase=build command=nix develop -c python3 -m pytest -q gate_log={dir}/gates/a/build-0.log
7 2026-10-16T03:52:00+00:00 gate-result ticket=a round=0 phase=build command=nix develop -c python3 -m pytest -q commit=1111111111111111111111111111111111111111 exit_status=0 green=True
8 2026-10-16T03:52:00+00:00 gate-start ticket=a round=0 phase=build command=uvx "zensical==0.0.2" build --strict gate_log={dir}/gates/a/build-0.log
9 2026-10-16T03:52:01+00:00 gate-result ticket=a round=0 phase=build command=uvx "zensical==0.0.2" build --strict commit=1111111111111111111111111111111111111111 exit_status=0 green=True
10 2026-10-16T03:52:01+00:00 verify-start ticket=a round=0 agent=python-verifier tier=verifier model=m commit=1111111111111111111111111111111111111111 copy={dir}/copy
11 2026-10-16T03:52:01+00:00 agent-start ticket=a role=verifier round=0 event_stream={dir}/reports/a.verifier.stream.jsonl
12 2026-10-16T04:00:00+00:00 verify-result ticket=a round=0 exit_status=0 end_state=finished session_id=s event_stream={dir}/reports/a.verifier.stream.jsonl
13 2026-10-16T04:00:00+00:00 verify-report ticket=a round=0 valid=True path={dir}/verdict.json reason=None
14 2026-10-16T04:00:00+00:00 verdict ticket=a round=0 mode=full verdict=ACCEPT report={dir}/verdict.json
15 2026-10-16T04:00:00+00:00 run-branch-advance ticket=a run_branch=run/demo from_commit=0000000000000000000000000000000000000000 to_commit=1111111111111111111111111111111111111111
16 2026-10-16T04:00:00+00:00 step-end ticket=a state=built reason=None
17 2026-10-16T04:00:00+00:00 step-start ticket=b stack=python branch=run/demo-b worktree=/w/b base_commit=0000000000000000000000000000000000000000 agent=python-specialist tier=strong model=m
18 2026-10-16T04:00:00+00:00 agent-start ticket=b role=specialist round=0 event_stream={dir}/reports/b.specialist.stream.jsonl
19 2026-10-16T04:06:40+00:00 adapter-result ticket=b exit_status=0 end_state=finished session_id=s event_stream={dir}/reports/b.specialist.stream.jsonl
20 2026-10-16T04:06:40+00:00 report-validation ticket=b valid=True path={dir}/reports/b.specialist.json reason=None status=committed blocked_reason=None
21 2026-10-16T04:06:40+00:00 step-end ticket=b state=built reason=None
22 2026-10-16T04:06:40+00:00 run-end result=built
position 22
"""


@pytest.mark.parametrize(
    ("terminal", "extra"),
    [pytest.param(False, (), id="pipe"), pytest.param(True, ("--raw",), id="raw-on-a-terminal"), pytest.param(False, ("--raw",), id="raw-to-a-pipe")],
)
def test_watch_to_a_pipe_or_with_raw_prints_the_raw_lines_of_before(tmp_path, monkeypatch, terminal, extra):
    repo = bare_repo(tmp_path)
    run = a_live_run(repo)
    ticket_b_ends(run)

    code, out = watch(repo, monkeypatch, *extra, terminal=terminal)

    assert code == 0
    # On a terminal the output also sets the title of the terminal, which the terminal does not show as text.
    assert without_titles(out) == RAW_OF_THE_WHOLE_RUN.format(dir=run.dir)
    assert (TITLE.search(out) is not None) == terminal


RAW_OF_A_PROBLEM = """\
1 2026-10-16T03:00:00+00:00 run-start run_id=r1 workflow=/w/workflow.toml mode=assure adapter=claude-code base_branch=main run_branch=run/demo
2 2026-10-16T03:00:00+00:00 step-start ticket=a stack=python branch=run/demo-a worktree=/w/a base_commit=0000000000000000000000000000000000000000 agent=python-specialist tier=strong model=m
3 2026-10-16T03:00:00+00:00 step-end ticket=a state=failed reason=crashed: OSError: disk full
watch: problem: ticket a: the step failed: crashed: OSError: disk full
position 3
"""

RAW_OF_A_VERDICT = """\
1 2026-10-16T03:00:00+00:00 run-start run_id=r1 workflow=/w/workflow.toml mode=assure adapter=claude-code base_branch=main run_branch=run/demo
2 2026-10-16T03:00:00+00:00 step-start ticket=a stack=python branch=run/demo-a worktree=/w/a base_commit=0000000000000000000000000000000000000000 agent=python-specialist tier=strong model=m
3 2026-10-16T03:52:01+00:00 verify-start ticket=a round=0 agent=python-verifier tier=verifier model=m commit=1111111111111111111111111111111111111111 copy={dir}/copy
4 2026-10-16T03:52:01+00:00 agent-start ticket=a role=verifier round=0 event_stream={dir}/reports/a.verifier.stream.jsonl
5 2026-10-16T04:00:00+00:00 verify-result ticket=a round=0 exit_status=0 end_state=finished session_id=s event_stream={dir}/reports/a.verifier.stream.jsonl
6 2026-10-16T04:00:00+00:00 verify-report ticket=a round=0 valid=True path={dir}/verdict.json reason=None
7 2026-10-16T04:00:00+00:00 verdict ticket=a round=0 mode=full verdict=REJECT report={dir}/verdict.json
watch: verdict: ticket a round 0: REJECT: a: x
position 7
"""


def ended_built(run: Run) -> None:
    run.add(1, "run-end", result="built", built=[], failed=[], skipped=[])


def ended_failed(run: Run) -> None:
    run.add(1, "run-end", result="failed", built=[], failed=["a"], skipped=[])


def a_problem(run: Run) -> None:
    run.step_start(0, "a")
    run.add(0, "step-end", ticket="a", state="failed", reason="crashed: OSError: disk full")


def a_problem_with_no_reason(run: Run) -> None:
    """A failed step with a null reason. A review found that the feed then showed "None"."""
    run.step_start(0, "a")
    run.add(0, "step-end", ticket="a", state="failed", reason=None)


def a_verdict(run: Run) -> None:
    run.step_start(0, "a")
    run.verify(3121, 3600, "a", "REJECT")


def a_stall(run: Run) -> None:
    run.step_start(0, "a")
    then = time.time() - 600
    os.utime(run.path, (then, then))


def a_run_that_goes_on(run: Run) -> None:
    run.step_start(0, "a")


# Each reason to end, the journal that gives it, the options, the exit code of `watch` before the live view, the raw
# lines of before (None when the case does not fix them), and the line that the feed of the live view ends with.
END_REASONS = [
    pytest.param(ended_built, (), 0, None, r"16:00  run built · 1s · tokens 0 new, 0 cache", id="run-succeeded"),
    pytest.param(ended_failed, (), 1, None, r"16:00  run failed · 1s · tokens 0 new, 0 cache", id="run-failed"),
    pytest.param(a_problem, (), 3, RAW_OF_A_PROBLEM, r"16:00  ✗ #a the step failed: crashed: OSError: disk full", id="problem"),
    pytest.param(a_problem_with_no_reason, (), 3, None, r"16:00  ✗ #a the step failed", id="problem-with-no-reason"),
    pytest.param(a_verdict, ("--until", "verdict"), 4, RAW_OF_A_VERDICT, r"17:00  #a REJECT · 1 finding", id="verdict"),
    pytest.param(a_stall, ("--stall-minutes", "5"), 5, None, r"\d\d:\d\d  ✗ stall · no change for 5m", id="stall"),
    pytest.param(
        a_run_that_goes_on, ("--max-minutes", "0.0001", "--poll-seconds", "0.01"), 6, None,
        r"\d\d:\d\d  time limit · 0\.0001m used · the run goes on", id="time-limit",
    ),
]


@pytest.mark.parametrize(("journal", "extra", "code", "raw", "feed_end"), END_REASONS)
def test_each_end_reason_has_the_exit_code_and_the_position_of_before_on_a_terminal_and_to_a_pipe(
    tmp_path, monkeypatch, local_zone, journal, extra, code, raw, feed_end
):
    local_zone("NZT-13")
    repo = bare_repo(tmp_path)
    run = Run(repo, "a")
    journal(run)

    def poll(seconds: float) -> None:
        """The time-limit case polls until its limit, so a wait does nothing."""

    piped, raw_out = watch(repo, monkeypatch, *extra, terminal=False, sleep=poll)
    live, live_out = watch(repo, monkeypatch, *extra, terminal=True, sleep=poll)

    assert piped == live == code
    assert raw_out.splitlines()[-1] == live_out.splitlines()[-1] == f"position {run.seq}"
    if raw is not None:
        assert raw_out == raw.format(dir=run.dir)
    assert re.fullmatch(feed_end, frames(live_out)[-1][-1]), frames(live_out)[-1]


def test_watch_of_an_unknown_run_on_a_terminal_exits_2_as_before(tmp_path, monkeypatch, capsys):
    repo = bare_repo(tmp_path)
    monkeypatch.setattr(sys, "stdout", Terminal())

    assert delegate.main(["watch", "no-such-run", "--repo", str(repo)]) == 2
    assert "no-such-run" in capsys.readouterr().err
    assert sys.stdout.getvalue() == ""
