"""`delegate status`: the state of each ticket of a run, read from its journal only.

A case calls `delegate.main(["status", ...])` against a real temporary git
repository. A run that finished is a real one, built with a scripted adapter. A
case that needs a journal in a state the engine passes through quickly writes
that journal with the journal class of the engine. The scripted adapter is
removed before `status` runs, so each case also shows that `status` needs no
adapter.
"""

import re

from agent_definitions import adapters, delegate

from .support import (
    ScriptedAdapter, bare_repo, make_repo, read_journal, run_end, run_start, step_end, step_start, write_journal,
)
from .test_run_multi import HEAD, ticket


def status(repo, capsys, *extra):
    code = delegate.main(["status", "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def ticket_line(out: str, ticket_id: str) -> str:
    (line,) = [line for line in out.splitlines() if line.startswith(f"ticket {ticket_id}:")]
    return line


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
    assert re.fullmatch(r"ticket a: state failed, verdict -, branch run/demo-a, last event \S+", ticket_line(out, "a"))
    assert ticket_line(out, "b").startswith("ticket b: state skipped, verdict -, branch -, last event ")
    assert ticket_line(out, "c") == f"ticket c: state built, verdict ACCEPT, branch run/demo-c, last event {last_c}"
    assert "  reason: blocked by a, which failed" in out.splitlines()


def test_status_without_a_run_id_selects_the_newest_run_of_the_repository(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "20260101T000000Z-zzzz", run_start("20260101T000000Z-zzzz", "a"), step_start("a"), step_end("a"), run_end("built", ["a"]))
    # The id of the newer run sorts before the older one, so only the start time can rank it.
    write_journal(repo, "20250101T000000Z-aaaa", run_start("20250101T000000Z-aaaa", "x", "y"), step_start("x"))

    code, out, err = status(repo, capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[0] == "run 20250101T000000Z-aaaa: running (mode assure, adapter scripted)"
    assert ticket_line(out, "x").startswith("ticket x: state running, verdict -, branch run/demo-x, last event ")
    assert ticket_line(out, "y") == "ticket y: state pending, verdict -, branch -, last event -"


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
    assert ticket_line(out, "a").startswith("ticket a: state built, verdict ACCEPT, ")


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
    assert [ticket_line(out, t).split(", branch")[0] for t in "abcd"] == [
        "ticket a: state built, verdict REJECT", "ticket b: state failed, verdict REJECT",
        "ticket c: state built, verdict REJECT", "ticket d: state pending, verdict -",
    ]


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
