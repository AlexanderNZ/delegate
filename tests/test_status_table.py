"""The text of `delegate status` is the ticket table of frame 1i of the design (#117).

The spec is `design/design_handoff_delegate_terminal_output/docs/components.md` (Ticket table (status)) and
`docs/copy-deck.md`. Under the header lines (run, progress, journal), `status` prints the ticket table of the live view
of `watch`, with three more columns: `verdict`, `branch` and `last event`. The progress line of each ticket folds into
its row. `status --json` does not change.

A case writes the journal of a run in the state directory of a real temporary git repository and calls
`delegate.main(["status", ...])`. The system boundaries are faked: the clock (`time.time`), the locale (`LC_ALL`), the
terminal (a standard output whose `isatty` is true), and `NO_COLOR`. The event streams are the recorded fixtures of
the `claude-code` adapter.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from delegate.cli import delegate

from .pages import outside_the_package, repository_root
from .support import Terminal, bare_repo, without_titles
from .test_watch_frame import built, specialist_and_gates, verifier_starts
from .test_watch_live import PYTEST, START, Run, at

ROOT = repository_root(Path(__file__))

# Ticket b waits on a, and c waits on b. Ticket d has no blocker.
BLOCKED_BY: dict[str, tuple[str, ...]] = {"b": ("a",), "c": ("b",)}

# An SGR sequence: the paint of a role.
SGR = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def utf8_locale(monkeypatch):
    """A UTF-8 locale, so the table has the Unicode glyph set, and no `NO_COLOR` in the environment."""
    monkeypatch.setenv("LC_ALL", "en_NZ.UTF-8")
    monkeypatch.delenv("NO_COLOR", raising=False)


def frozen(monkeypatch, seconds: float) -> None:
    monkeypatch.setattr(time, "time", lambda: (START + timedelta(seconds=seconds)).timestamp())


def status(repo: Path, capsys, *extra: str) -> str:
    code = delegate.main(["status", "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    assert (code, captured.err) == (0, ""), captured.err
    return captured.out


def a_running_run(repo: Path, run_id: str = "r1") -> Run:
    """Ticket a is built in 600 seconds, with ACCEPT. The verifier of ticket b runs since 960 seconds. c and d wait."""
    run = Run(repo, "a", "b", "c", "d", blocked_by=BLOCKED_BY, run_id=run_id)
    built(run, "a", 0)
    specialist_and_gates(run, "b", 600)
    verifier_starts(run, 960, "b")
    return run


def an_ended_run(repo: Path) -> Run:
    """Ticket a is built. The gates of b are red, so b failed and c is skipped. Ticket d is built, and the run ended."""
    run = Run(repo, "a", "b", "c", "d", blocked_by=BLOCKED_BY)
    built(run, "a", 0)
    specialist_and_gates(run, "b", 600)
    run.add(960, "step-end", ticket="b", state="failed", reason=f"gates red: {PYTEST}")
    run.add(960, "skip", ticket="c", blockers=["b"], reason="blocked by b, which failed")
    built(run, "d", 960)
    run.add(1560, "run-end", result="failed", built=["a", "d"], failed=["b"], skipped=["c"])
    return run


def a_paused_run(repo: Path) -> Run:
    """Ticket a is built, and the run paused before the step of ticket b."""
    run = Run(repo, "a", "b", "c", "d", blocked_by=BLOCKED_BY)
    built(run, "a", 0)
    run.add(600, "pause-requested", ticket="b", requested_at=None)
    run.add(600, "paused", ticket="b", before="step")
    return run


def lines_of(repo: Path, capsys, *extra: str) -> list[str]:
    return status(repo, capsys, "r1", *extra).splitlines()


def test_the_status_of_a_running_run_is_the_header_lines_and_the_ticket_table_with_verdict_branch_and_last_event(
    tmp_path, capsys, monkeypatch
):
    repo = bare_repo(tmp_path)
    run = a_running_run(repo)
    frozen(monkeypatch, 1100)

    lines = lines_of(repo, capsys)

    # a used 600 seconds and has the tokens of its specialist and its verifier (finished.jsonl twice). b is in its
    # verifier since 960 seconds: the harness has not opened the stream yet, so its last activity is the agent-start.
    # The progress sub-line of each ticket folds into its row, and a pending ticket has empty cells.
    assert lines == [
        "run r1: running (mode assure, adapter claude-code)",
        "progress: 45%, 1 of 4 ended, 18m20s used, ETA 21m40s, tokens 58k new, 0 cache",
        f"journal {run.path}",
        "",
        "#  plan     state    phase" + " " * 38 + "  verdict  branch        used  new  cache  last event",
        "1  ● a      ✓ built  " + " " * 43 + f"  ACCEPT   run/demo-a  10m00s  39k      0  {at(600)}",
        "2  └─▶ b    running  specialist ✓  gates ✓  verifier ●  fix-up ○  " + " " * 7 + f"  run/demo-b   8m20s  19k      0  {at(960)}  active 2m20s",
        "3    └─· c  pending",
        "4  · d      pending",
    ]


def test_the_status_of_a_run_that_ended_gives_the_reason_of_a_failed_and_of_a_skipped_ticket_in_the_phase_cell(
    tmp_path, capsys, monkeypatch
):
    repo = bare_repo(tmp_path)
    run = an_ended_run(repo)
    frozen(monkeypatch, 9000)

    lines = lines_of(repo, capsys)

    # The skipped ticket c has no step: no branch, no time used, and no tokens. Its last event is its `skip`.
    assert lines == [
        "run r1: failed (mode assure, adapter claude-code)",
        "progress: 100%, 4 of 4 ended, 26m00s used, tokens 97k new, 0 cache",
        f"journal {run.path}",
        "",
        "#  plan     state    phase" + " " * 41 + "  verdict  branch        used  new  cache  last event",
        "1  ● a      ✓ built  " + " " * 46 + f"  ACCEPT   run/demo-a  10m00s  39k      0  {at(600)}",
        f"2  └─✗ b    failed   gates red: {PYTEST}  " + " " * 7 + f"  run/demo-b   6m00s  19k      0  {at(960)}",
        "3    └─– c  skipped  blocked by b, which failed" + " " * 20 + "  " + " " * 7 + "  " + " " * 10 + "  " + " " * 6
        + "  " + " " * 3 + "  " + " " * 5 + f"  {at(960)}",
        "4  ● d      ✓ built  " + " " * 46 + f"  ACCEPT   run/demo-d  10m00s  39k      0  {at(1560)}",
    ]


def test_the_status_of_a_paused_run_gives_paused_before_this_step_in_the_row_of_the_next_ticket(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = a_paused_run(repo)
    frozen(monkeypatch, 9000)

    lines = lines_of(repo, capsys)

    # The pause events name ticket b, so they are its last event. b has no step, so it has no branch.
    assert lines == [
        "run r1: paused (mode assure, adapter claude-code)",
        "progress: 25%, 1 of 4 ended, 10m00s used, tokens 39k new, 0 cache",
        f"journal {run.path}",
        "",
        "#  plan     state    phase" + " " * 18 + "  verdict  branch        used  new  cache  last event",
        "1  ● a      ✓ built  " + " " * 23 + f"  ACCEPT   run/demo-a  10m00s  39k      0  {at(600)}",
        "2  └─· b    pending  paused before this step  " + " " * 7 + "  " + " " * 10 + "  " + " " * 6 + "  " + " " * 3
        + "  " + " " * 5 + f"  {at(600)}",
        "3    └─· c  pending",
        "4  · d      pending",
    ]


@pytest.mark.parametrize(("state", "make", "clock"), [
    ("running", a_running_run, 1100), ("ended", an_ended_run, 9000), ("paused", a_paused_run, 9000),
])
def test_status_json_keeps_the_bytes_of_each_field_before_the_table_and_adds_the_plan_as_the_last_field(
    tmp_path, capsys, monkeypatch, state, make, clock
):
    """The golden files are the output of `status --json` of version 1.6.0 with #115 and #116, before #117.

    #120 adds the field `plan` after `tickets`. The text up to the end of `tickets` stays byte-identical.
    """
    repo = bare_repo(tmp_path)
    make(repo)
    frozen(monkeypatch, clock)

    out = status(repo, capsys, "r1", "--json")

    golden = (ROOT / "tests" / "fixtures" / "status-json" / f"{state}.json").read_text()
    assert golden.endswith("\n  ]\n}\n")
    assert out.startswith(golden.removesuffix("\n}\n") + ",\n  \"plan\": ")
    assert list(json.loads(out)) == [*json.loads(golden), "plan"]


def test_status_json_gives_the_plan_of_the_run_with_the_blockers_of_each_ticket_in_plan_order(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    a_running_run(repo)
    frozen(monkeypatch, 1100)

    snap = json.loads(status(repo, capsys, "r1", "--json"))

    assert snap["plan"] == [
        {"id": "a", "blocked_by": []},
        {"id": "b", "blocked_by": ["a"]},
        {"id": "c", "blocked_by": ["b"]},
        {"id": "d", "blocked_by": []},
    ]


def test_status_json_gives_the_plan_of_the_newest_plan_change(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "a", "b", blocked_by={"b": ("a",)})
    built(run, "a", 0)
    run.add(600, "plan-change", added=["c"], changed=["b"], fields=["blocked-by"], removed=[], order=["a", "c", "b"],
            reviewed=["b"], plan=[
                {"id": t, "stack": "python", "blocked_by": blockers, "text_sha256": "0" * 64}
                for t, blockers in (("a", []), ("c", ["a"]), ("b", ["c"]))
            ])
    frozen(monkeypatch, 700)

    snap = json.loads(status(repo, capsys, "r1", "--json"))

    assert [t["id"] for t in snap["tickets"]] == ["a", "c", "b"]
    assert snap["plan"] == [{"id": "a", "blocked_by": []}, {"id": "c", "blocked_by": ["a"]}, {"id": "b", "blocked_by": ["c"]}]


def test_status_json_gives_a_null_plan_for_the_journal_of_an_earlier_version_with_no_plan(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "a", "b")
    built(run, "a", 0)
    frozen(monkeypatch, 700)

    snap = json.loads(status(repo, capsys, "r1", "--json"))

    assert [t["id"] for t in snap["tickets"]] == ["a", "b"]
    assert snap["plan"] is None


def on_a_terminal(repo: Path, monkeypatch) -> str:
    """The output of `status` on a terminal, with no title sequence: on a terminal, `status` also sets the title."""
    out = Terminal()
    monkeypatch.setattr(sys, "stdout", out)
    code = delegate.main(["status", "--repo", str(repo), "r1"])
    assert code == 0
    return without_titles(out.getvalue())


def test_on_a_terminal_each_cell_of_the_table_has_the_colour_of_its_role_and_the_header_lines_stay_plain(
    tmp_path, capsys, monkeypatch
):
    repo = bare_repo(tmp_path)
    a_running_run(repo)
    frozen(monkeypatch, 1100)
    plain = lines_of(repo, capsys)

    painted = on_a_terminal(repo, monkeypatch).splitlines()

    assert [SGR.sub("", line) for line in painted] == plain
    assert [SGR.search(line) for line in painted[:4]] == [None] * 4
    header, a, b, c, _ = painted[4:]
    assert header.startswith("\x1b[2m#\x1b[0m  \x1b[2mplan\x1b[0m")  # the header is faint
    assert "\x1b[1mACCEPT\x1b[0m" in a  # the verdict word is bold
    assert f"\x1b[2m{at(600)}\x1b[0m" in a  # the time of the last event is faint
    assert "\x1b[2mrun/demo-a\x1b[0m" in a  # each cell of a row that ended is faint
    assert "\x1b[36m\x1b[1mrunning\x1b[0m\x1b[0m" in b  # the ticket that runs now is in `now`, bold
    assert "  run/demo-b  " in b  # and its branch has the colour of the theme
    assert "\x1b[36mactive 2m20s\x1b[0m" in b
    assert "\x1b[34m  └─\x1b[0m" in c  # the tree is in `plan`


def test_with_no_color_set_the_status_on_a_terminal_writes_no_sgr_code(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    a_running_run(repo)
    frozen(monkeypatch, 1100)
    plain = status(repo, capsys, "r1")
    monkeypatch.setenv("NO_COLOR", "1")

    out = on_a_terminal(repo, monkeypatch)

    assert out == plain
    assert "\x1b" not in out


def test_with_a_locale_that_is_not_utf8_the_table_has_the_ascii_glyphs_in_the_same_columns(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    a_running_run(repo)
    frozen(monkeypatch, 1100)
    unicode = lines_of(repo, capsys)
    monkeypatch.setenv("LC_ALL", "C")

    ascii_lines = lines_of(repo, capsys)

    assert [len(line) for line in ascii_lines] == [len(line) for line in unicode]
    assert ascii_lines[5:9] == [
        "1  * a      + built  " + " " * 43 + f"  ACCEPT   run/demo-a  10m00s  39k      0  {at(600)}",
        "2  \\-> b    running  specialist +  gates +  verifier *  fix-up o  " + " " * 7 + f"  run/demo-b   8m20s  19k      0  {at(960)}  active 2m20s",
        "3    \\-. c  pending",
        "4  . d      pending",
    ]


@outside_the_package
def test_the_example_of_the_reference_page_is_the_output_of_status_for_the_run_that_it_shows(tmp_path, capsys, monkeypatch):
    """The example is for a locale that is not UTF-8. Only the path of the journal differs: the page names no real path."""
    run_id = "20261016T030000Z-abcd"
    repo = bare_repo(tmp_path)
    a_running_run(repo, run_id)
    frozen(monkeypatch, 1100)
    monkeypatch.setenv("LC_ALL", "C")
    page = (ROOT / "docs" / "reference" / "status-and-watch.md").read_text()
    (example,) = re.findall(rf"```\n(run {run_id}: running .*?)```", page, flags=re.DOTALL)

    lines = status(repo, capsys, run_id).splitlines()

    journal = f"journal /path/to/repo/.git/delegate/runs/{run_id}/journal.jsonl"
    assert example.splitlines() == [journal if line.startswith("journal ") else line for line in lines]
