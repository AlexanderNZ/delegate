"""The engine restores the progress of a run from the run state (#320, ADR 0009).

A resume reads the tickets of the run from `run_state.fold`. So two rules of the run state now apply to a resume after a
stop that came between two events of a resume:

1. A `plan-change` takes a changed ticket out of the skipped tickets. A crash after the `plan-change`, before the next
   `step-start`, does not put the ticket back in the skipped tickets.
2. An `unskip` makes the ticket pending. A pause after the `unskip`, before the `step-start` of the ticket, does not put
   the ticket back in the skipped tickets.
3. A `run-end` ends the run when no `resume` comes after it. A retry of a run that ended, which then paused, goes on with a
   plain resume, as the note of the pause says. Before, the engine said that the run had ended.

The ticket of the first two cases is a ticket that the run did not reach after a crash. The skip rule did not skip it, so a resume
does not apply the skip rule to it again (#245): only the plan change makes it run. Each case calls `delegate.main`
against a real temporary git repository, with the scripted adapter in the place of the harness.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ..support import HEAD, make_repo, read_journal_file, resume_main, run_main, ticket, ticket_of
from ..test_run_pause import pause, run_id_of
from ..test_run_retry import as_an_earlier_version_ended_the_crash, end_with_no_verdict, first_run, healthy

pytestmark = pytest.mark.usefixtures("home")

CHANGED_B = ticket("b").replace("Ticket b adds its file.", "Ticket b adds its file, with a new text.")


def write_agents(repo: Path) -> None:
    """Agent files for the stack, so the run seals its verifier and the setup check of the adapter runs at each resume."""
    directory = repo / ".claude" / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("python-specialist", "python-verifier"):
        (directory / f"{name}.md").write_text(f"---\nname: {name}\n---\nThe rules of {name}.\n")
    (repo / ".git" / "info" / "exclude").write_text(".claude/\n")


def a_run_that_did_not_reach_b(tmp_path, capsys, scripted) -> tuple[Path, str]:
    """A run whose step of a crashed, in the journal of an earlier version: a failed, b skipped with no blocker, and a `run-end`."""

    def crash(request):
        raise RuntimeError("the harness broke")

    scripted.during_verifier = crash
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))
    write_agents(repo)
    run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = run_id_of(repo)
    as_an_earlier_version_ended_the_crash(repo, run_id, ["b"])
    healthy(scripted, "ACCEPT")
    scripted.during_verifier = None
    return repo, run_id


def test_a_resume_after_a_crash_that_came_after_a_plan_change_of_a_skipped_ticket_builds_that_ticket(tmp_path, capsys, scripted):
    repo, run_id = a_run_that_did_not_reach_b(tmp_path, capsys, scripted)
    (repo / "workflow.toml").write_text(HEAD + ticket("a") + CHANGED_B)

    def crash_at_the_setup_check(sealed, command):
        raise RuntimeError("the process crashed")

    # The setup check runs after the `plan-change` and before the first step of the resume.
    scripted.checks_setup, scripted.permits = True, crash_at_the_setup_check
    with pytest.raises(RuntimeError, match="the process crashed"):
        resume_main(repo, capsys, run_id)
    events = read_journal_file(repo, run_id)
    assert [e["event"] for e in events][-2:] == ["resume", "plan-change"]
    assert events[-1]["changed"] == ["b"]
    scripted.checks_setup = False

    code, out, err = resume_main(repo, capsys, run_id)

    assert [ticket_of(call) for call in scripted.calls] == ["b"]
    resume = [e for e in read_journal_file(repo, run_id) if e["event"] == "resume"][-1]
    assert (resume["failed"], resume["skipped"]) == (["a"], [])
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["built"], end["failed"], end["skipped"]) == ("run-end", ["b"], ["a"], [])


def test_a_resume_after_a_pause_that_came_after_the_unskip_of_a_changed_ticket_builds_that_ticket(tmp_path, capsys, scripted):
    repo, run_id = a_run_that_did_not_reach_b(tmp_path, capsys, scripted)
    (repo / "workflow.toml").write_text(HEAD + ticket("a") + CHANGED_B)

    def pause_at_the_setup_check(sealed, command):
        assert pause(repo, capsys, run_id)[0] == 0

    # The pause request comes before the first step of the resume, so the run pauses before the step of b, after its `unskip`.
    scripted.checks_setup, scripted.permits = True, pause_at_the_setup_check
    resume_main(repo, capsys, run_id)
    names = [e["event"] for e in read_journal_file(repo, run_id)]
    assert names[-5:] == ["resume", "plan-change", "unskip", "pause-requested", "paused"]
    scripted.checks_setup = False

    code, out, err = resume_main(repo, capsys, run_id)

    assert [ticket_of(call) for call in scripted.calls] == ["b"]
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["built"], end["failed"], end["skipped"]) == ("run-end", ["b"], ["a"], [])


def test_a_plain_resume_goes_on_with_a_retry_of_a_run_that_ended_after_the_retry_paused(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))
    end_with_no_verdict(scripted, scoped_only=False)
    code, run_id = first_run(repo, capsys)
    assert code == 1 and read_journal_file(repo, run_id)[-1]["event"] == "run-end"
    healthy(scripted, "ACCEPT")

    def pause_at_the_verifier_of_a(request):
        if ticket_of(request) == "a":
            assert pause(repo, capsys, run_id)[0] == 0

    scripted.during_verifier = pause_at_the_verifier_of_a
    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")
    assert f"go on with delegate run --resume {run_id}\n" in out
    scripted.during_verifier = None

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert [ticket_of(call) for call in scripted.calls] == ["b"]
    end = read_journal_file(repo, run_id)[-1]
    assert (end["event"], end["result"], end["built"], end["failed"]) == ("run-end", "built", ["a", "b"], [])
