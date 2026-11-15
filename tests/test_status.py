"""`delegate status`: the state of each ticket of a run, read from its journal only.

A case calls `delegate.main(["status", ...])` against a real temporary git
repository. A run that finished is a real one, built with a scripted adapter. A
case that needs a journal in a state the engine passes through quickly writes
that journal with the journal class of the engine. The scripted adapter is
removed before `status` runs, so each case also shows that `status` needs no
adapter.
"""

import json
import re

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import (
    ScriptedAdapter, bare_repo, committed_repo, make_repo, read_journal, run_end, run_start, status_row, step_end, step_start, write_journal,
    write_journal_in_state_root,
)
from .support import HEAD, ticket

# A time of the journal: the time of the newest event of a ticket, in the column `last event`.
TIME = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+\+00:00"


@pytest.fixture(autouse=True)
def utf8_locale(monkeypatch):
    """A UTF-8 locale, so the ticket table has the Unicode glyph set (`delegate.run.output.glyphs`)."""
    monkeypatch.setenv("LC_ALL", "en_NZ.UTF-8")


def status(repo, capsys, *extra):
    code = delegate.main(["status", "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_status_of_a_finished_run_prints_every_ticket_with_its_final_state_verdict_branch_and_last_event_time(tmp_path, capsys, git_identity):
    # Ticket a fails, b waits on a and is skipped, c is accepted.
    adapters.register("scripted", ScriptedAdapter(failing_tickets={"a"}, files_by_ticket={"c": {"c.txt": "c\n"}}))
    try:
        repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c"))
        delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
        events = read_journal(capsys.readouterr().out)
    finally:
        adapters.unregister("scripted")
    run_id = events[0]["run_id"]
    last_c = [e for e in events if e.get("ticket") == "c"][-1]["time"]

    code, out, err = status(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert out.splitlines()[0] == f"run {run_id}: failed (mode assure, adapter scripted)"
    # The cells of a row: the position, the plan, the state, the phase, the blockers that are not the row above, the
    # verdict, the branch, the time used, the last event, and the reason (#143). The scripted adapter has no stream
    # reader, so the table has no tokens.
    a, b, c = (status_row(out, t) for t in "abc")
    assert a[:4] == ["1", "✗ a", "failed", "run/demo-a"]  # a has no verdict
    assert re.fullmatch(r"\d+s", a[4]) and re.fullmatch(TIME, a[5]) and a[6].startswith("the specialist ended failed") and len(a) == 7
    assert b[:3] == ["2", "– b", "skipped"]  # b has no step: no branch, no time used
    assert re.fullmatch(TIME, b[3]) and b[4] == "blocked by a, which failed" and len(b) == 5
    assert c[:3] == ["3", "● c", "✓ built"] and c[3:5] == ["ACCEPT", "run/demo-c"] and c[6] == last_c and len(c) == 7


def test_status_without_a_run_id_selects_the_newest_run_of_the_repository(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "20260101T000000Z-zzzz", run_start("20260101T000000Z-zzzz", "a"), step_start("a"), step_end("a"), run_end("built", ["a"]))
    # The id of the newer run sorts before the older one, so only the start time can rank it.
    write_journal(repo, "20250101T000000Z-aaaa", run_start("20250101T000000Z-aaaa", "x", "y"), step_start("x"))

    code, out, err = status(repo, capsys)

    assert (code, err) == (0, "")
    # No run is active, so the newest run is the one that status shows. It has no run-end and no live process.
    assert out.splitlines()[0] == "run 20250101T000000Z-aaaa: stopped (mode assure, adapter scripted)"
    x = status_row(out, "x")
    assert x[:3] == ["1", "▶ x", "running"] and x[-3] == "run/demo-x" and re.fullmatch(TIME, x[-1])
    assert status_row(out, "y") == ["2", "· y", "pending"]


def test_status_shows_the_verdict_of_the_last_round(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    verdict = lambda round_number, value: ("verdict", {  # noqa: E731
        "ticket": "a", "round": round_number, "mode": "full", "verdict": value, "findings": [], "unverified": [], "report": "/r.json",
    })
    write_journal(
        repo, "r1", run_start("r1", "a"), step_start("a"), verdict(0, "REJECT"), verdict(1, "ACCEPT"), step_end("a"), run_end("built", ["a"])
    )

    code, out, err = status(repo, capsys, "r1")

    assert (code, err) == (0, "")
    assert status_row(out, "a")[2:5] == ["✓ built", "ACCEPT", "run/demo-a"]


def test_status_shows_the_verdict_of_a_chain_for_every_ticket_that_the_verdict_covers(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    chain_verdict = ("verdict", {
        "ticket": "c", "round": 0, "mode": "full", "verdict": "REJECT", "findings": ["[b] no header"], "unverified": [],
        "report": "/r.json", "stack": "python", "tickets": ["a", "b", "c"], "mapped": {"b": ["no header"]}, "unmapped": [],
    })
    write_journal(
        repo, "r1", run_start("r1", "a", "b", "c", "d", mode="economy"),
        *(event for ticket_id in "abc" for event in (step_start(ticket_id), step_end(ticket_id))),
        chain_verdict, step_end("b", "failed", "no header"), run_end("failed", ["a", "c"], ["b"]),
    )

    code, out, err = status(repo, capsys, "r1")

    assert (code, err) == (0, "")
    # b failed with the reason `no header`, at the end of its row. d has no verdict.
    assert [status_row(out, t)[2:5] for t in "abcd"] == [
        ["✓ built", "REJECT", "run/demo-a"], ["failed", "REJECT", "run/demo-b"], ["✓ built", "REJECT", "run/demo-c"], ["pending"],
    ]
    assert status_row(out, "b")[-1] == "no header"


def test_status_of_an_unknown_run_exits_2_and_names_the_run_id(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"))

    code, out, err = status(repo, capsys, "no-such-run")

    assert (code, out) == (2, "")
    assert "no-such-run" in err


def test_status_of_a_repository_with_no_run_exits_2_and_names_the_repository(tmp_path, capsys):
    repo = bare_repo(tmp_path)

    code, out, err = status(repo, capsys)

    assert (code, out) == (2, "")
    assert str(repo) in err and "no run" in err


@pytest.fixture
def outside(tmp_path, monkeypatch):
    """A current directory that is in no git repository: the person of #305 who is not in the directory of the run."""
    path = tmp_path / "outside"
    path.mkdir()
    monkeypatch.chdir(path)
    return path


def status_here(capsys, *args):
    """`delegate status` with no `--repo`: the repository, if any, is the current directory."""
    code = delegate.main(["status", *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_status_from_outside_each_repository_shows_the_run_of_a_repository_under_the_state_root_by_its_short_id(tmp_path, capsys, outside):
    repo = bare_repo(tmp_path)
    write_journal_in_state_root(repo, "20260101T000000Z-ab12", run_start("20260101T000000Z-ab12", "a"), step_start("a"))

    code, out, err = status_here(capsys, "ab12")

    assert (code, err) == (0, "")
    assert out.splitlines()[0] == "run 20260101T000000Z-ab12: stopped (mode assure, adapter scripted)"


def test_status_from_outside_each_repository_shows_the_run_of_a_repository_by_its_full_id_and_as_json(tmp_path, capsys, outside):
    repo = bare_repo(tmp_path)
    write_journal_in_state_root(repo, "20260101T000000Z-ab12", run_start("20260101T000000Z-ab12", "a"), step_start("a"))

    code, out, err = status_here(capsys, "20260101T000000Z-ab12", "--json")

    assert (code, err) == (0, "")
    assert json.loads(out)["run_id"] == "20260101T000000Z-ab12"


def test_status_from_another_repository_finds_the_run_of_the_first_repository_under_the_state_root(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    write_journal_in_state_root(repo, "20260101T000000Z-ab12", run_start("20260101T000000Z-ab12", "a"), step_start("a"))
    other = committed_repo(tmp_path / "other", {})
    monkeypatch.chdir(other)

    code, out, err = status_here(capsys, "ab12")

    assert (code, err) == (0, "")
    assert out.splitlines()[0] == "run 20260101T000000Z-ab12: stopped (mode assure, adapter scripted)"


def test_status_from_outside_each_repository_exits_2_and_names_each_full_id_when_two_repositories_hold_the_short_id(
    tmp_path, capsys, outside,
):
    first = bare_repo(tmp_path)
    second = committed_repo(tmp_path / "other", {})
    write_journal_in_state_root(first, "20260101T000000Z-ab12", run_start("20260101T000000Z-ab12", "a"))
    write_journal_in_state_root(second, "20260202T000000Z-ab12", run_start("20260202T000000Z-ab12", "a"))

    code, out, err = status_here(capsys, "ab12")

    assert (code, out) == (2, "")
    assert "20260101T000000Z-ab12" in err and "20260202T000000Z-ab12" in err


def test_status_with_repo_looks_only_in_that_repository(tmp_path, capsys, outside):
    first = bare_repo(tmp_path)
    second = committed_repo(tmp_path / "other", {})
    write_journal_in_state_root(first, "20260101T000000Z-ab12", run_start("20260101T000000Z-ab12", "a"))
    write_journal_in_state_root(second, "20260202T000000Z-cd34", run_start("20260202T000000Z-cd34", "a"))

    code, out, err = status(first, capsys, "cd34")

    assert (code, out) == (2, "")
    assert "cd34" in err and str(first) in err


def test_status_from_outside_each_repository_exits_2_and_names_the_state_root_when_no_run_matches(tmp_path, capsys, outside, isolated_state_home):
    repo = bare_repo(tmp_path)
    write_journal_in_state_root(repo, "20260101T000000Z-ab12", run_start("20260101T000000Z-ab12", "a"))

    code, out, err = status_here(capsys, "zz99")

    assert (code, out) == (2, "")
    assert "zz99" in err and str(isolated_state_home / "delegate") in err


def test_status_from_outside_each_repository_looks_under_delegate_state_root_when_the_variable_is_set(tmp_path, capsys, outside, monkeypatch):
    root = tmp_path / "custom-root"
    monkeypatch.setenv("DELEGATE_STATE_ROOT", str(root))
    repo = bare_repo(tmp_path)
    write_journal_in_state_root(repo, "20260101T000000Z-ab12", run_start("20260101T000000Z-ab12", "a"))

    code, out, err = status_here(capsys, "ab12")

    assert (code, err) == (0, "")
    assert out.splitlines()[0].startswith("run 20260101T000000Z-ab12:")
    assert any(root.glob("repo-*/runs/20260101T000000Z-ab12/journal.jsonl"))
