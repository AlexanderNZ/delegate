"""`delegate status` decides the state of a run, of each ticket and of each chain from the run state (#321, ADR 0009).

A case writes the journal of a run in the state directory of a real temporary git repository, as the engine writes it, and
calls `delegate status --json`. The expected values are literals of the rules of ADR 0009.
"""

from __future__ import annotations

import pytest

from delegate.run import run_state

from ..support import bare_repo, frozen, read_journal_file
from ..test_status_snapshot import Run, snapshot, utf8_locale  # noqa: F401 - the fixture is autouse

pytestmark = pytest.mark.usefixtures("clock")


def states(snap: dict) -> dict[str, str]:
    return {t["id"]: t["state"] for t in snap["tickets"]}


def test_after_a_plan_change_that_changes_a_skipped_ticket_status_shows_the_ticket_as_pending(tmp_path, capsys, monkeypatch):
    # Rule 1 of ADR 0009. A crash after the `plan-change` leaves no `unskip` in the journal; the engine reads b as pending.
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", "b")
    run.step_start(0, "a")
    run.add(10, "step-end", ticket="a", state="failed", reason="the gates are red")
    run.add(11, "skip", ticket="b", reason="blocked by a, which failed", blockers=["a"])
    run.add(12, "run-end", result="failed", built=[], failed=["a"], skipped=["b"])
    run.add(20, "resume")
    run.add(21, "plan-change", added=[], changed=["b"], fields={}, removed=[], order=["a", "b"], reviewed=[], plan=[])
    frozen(monkeypatch, 30)

    snap = snapshot(repo, capsys)

    assert (snap["state"], states(snap)) == ("running", {"a": "failed", "b": "pending"})


def test_a_removed_and_a_changed_ticket_are_pending_and_an_unchanged_skipped_ticket_stays_skipped(tmp_path, capsys, monkeypatch):
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", "b", "c")
    run.step_start(0, "a")
    run.add(10, "step-end", ticket="a", state="failed", reason="the gates are red")
    run.add(11, "skip", ticket="b", reason="blocked by a, which failed", blockers=["a"])
    run.add(12, "skip", ticket="c", reason="blocked by a, which failed", blockers=["a"])
    run.add(13, "run-end", result="failed", built=[], failed=["a"], skipped=["b", "c"])
    run.add(20, "resume")
    run.add(21, "plan-change", added=[], changed=["b"], fields={}, removed=[], order=["a", "b", "c"], reviewed=[], plan=[])
    frozen(monkeypatch, 30)

    assert states(snapshot(repo, capsys)) == {"a": "failed", "b": "pending", "c": "skipped"}


def test_status_gives_the_pass_of_the_chain_that_the_run_state_gives_after_a_retry_of_a_ticket_that_is_not_the_tip(tmp_path, capsys, monkeypatch):
    # #304: the retry names `a`, a ticket of the first stack, and not the tip. The engine reads the pass after the retry.
    repo = bare_repo(tmp_path)
    run = Run(repo, "r1", "a", "b", mode="economy")
    for seconds, ticket in ((0, "a"), (100, "b")):
        run.step_start(seconds, ticket)
        run.add(seconds + 50, "step-end", ticket=ticket, state="built", reason=None)
    run.add(200, "gate-start", ticket="b", phase="chain", command="make test", gate_log="/l/1", name="tests")
    run.add(210, "gate-result", ticket="b", phase="chain", command="make test", exit_code=0, passed=True)
    run.add(220, "verify-start", ticket="b", stack="python", round=0)
    run.add(230, "verdict", ticket="b", stack="python", verdict="REJECT", tickets=["a", "b"])
    run.add(231, "fixup-start", ticket="b", stack="python", round=1, rejected_commit="r" * 40)
    run.add(240, "step-end", ticket="b", state="failed", reason="the verifier rejected the chain")
    run.add(250, "run-end", result="failed", built=["a"], failed=["b"], skipped=[])
    run.add(300, "resume")
    run.add(301, "retry", ticket="a", phase="specialist", round=0, reason="x", revived=["a", "b"])
    run.step_start(302, "a")
    run.add(310, "step-end", ticket="a", state="built", reason=None)
    run.add(320, "gate-start", ticket="b", phase="chain", command="make test", gate_log="/l/2", name="tests")
    frozen(monkeypatch, 340)
    fold = run_state.fold(read_journal_file(repo, "r1")).chain

    snap = snapshot(repo, capsys)

    assert [(s.stack, s.verdicts, len(s.fixup_starts)) for s in fold.stacks] == []  # the pass after the retry has no verdict and no round
    (chain,) = snap["chains"]
    assert (chain["state"], chain["phase"], chain["fixup_round"], chain["verdict"], chain["elapsed_seconds"]) == ("running", "gates", 0, None, 20)
