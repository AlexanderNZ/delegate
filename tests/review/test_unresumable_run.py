"""A run that cannot be resumed is a fact of the run state, and `halted` means a stop that a resume goes on with (#358).

An invariant violation and a moved run branch end the run for good. The run state gives that fact with its event, the
engine refuses the resume from the fact with a message that does not use the word halted, and `status` shows the fact
in the note of the run. Each engine case calls `delegate.main` against a real temporary git repository with the
scripted adapter.
"""

from __future__ import annotations

import json
import re

from delegate.cli import delegate
from delegate.run import run_state

from ..run_state.journals import built, ev, journal, started
from ..support import git, kill_a_run, make_repo, read_journal_file, resume_main, run_main, state_dir
from ..test_run_multi import HEAD, ticket

TWO = HEAD + ticket("a") + ticket("b", ("a",))

VIOLATION = ev("invariant-violation", ticket="a", round=0, worktree="/w/a", changes=["added oops.txt"])
JOURNAL_CHANGED = ev("invariant-violation", kind="journal-changed", journal="/j/journal.jsonl", offset=4096, size_written=4096)
MOVED = ev("run-branch-moved", ticket=None, run_branch="run/demo", expected="a" * 40, found="b" * 40)
HALTED = ev("halted", ticket="a", role="verifier", message="the harness failed")


def moved_run(repo, capsys) -> str:
    """A run whose run branch moved between two runs: the first resume writes `run-branch-moved`. Return the run id."""
    run_id, _ = kill_a_run(repo, 3)  # killed as the specialist of ticket b starts, after ticket a landed
    git(repo, "update-ref", "refs/heads/run/demo", git(repo, "rev-parse", "main").strip())
    assert resume_main(repo, capsys, run_id)[0] == 1
    assert any(e["event"] == "run-branch-moved" for e in read_journal_file(repo, run_id))
    return run_id


def violated_run(repo, capsys, scripted) -> str:
    """A run that the verifier of ticket a ended by a write into the real worktree. Return the run id."""

    def write_in_the_real_worktree(request):
        (run_dir,) = (state_dir(repo) / "worktrees").iterdir()
        (run_dir / "a" / "oops.txt").write_text("by the verifier\n")

    scripted.during_verifier = write_in_the_real_worktree
    code, _, _ = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 1
    scripted.during_verifier = None
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    return run_dir.name


def test_the_run_state_of_a_journal_with_an_invariant_violation_says_that_the_run_cannot_be_resumed_and_names_the_event():
    state = run_state.fold(journal(started("a"), VIOLATION))

    assert state.unresumable is not None
    assert state.unresumable.event["event"] == "invariant-violation"
    assert state.unresumable.event["ticket"] == "a"


def test_the_run_state_of_a_journal_with_a_moved_run_branch_says_that_the_run_cannot_be_resumed_and_names_the_event():
    state = run_state.fold(journal(built("a"), MOVED))

    assert state.unresumable is not None
    assert state.unresumable.event["event"] == "run-branch-moved"
    assert state.unresumable.event["found"] == "b" * 40


def test_the_run_state_of_a_journal_with_a_failure_of_the_harness_or_no_stop_can_be_resumed():
    assert run_state.fold(journal(started("a"), HALTED)).unresumable is None
    assert run_state.fold(journal(started("a"))).unresumable is None


def test_the_resume_of_a_run_that_an_invariant_violation_ended_is_refused_without_the_word_halted(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    run_id = violated_run(repo, capsys, scripted)

    for extra in ((), ("--retry", "a")):
        code, _, err = resume_main(repo, capsys, run_id, *extra)

        assert code == 1
        assert f"run {run_id!r} cannot be resumed: an invariant violation ended the run at ticket 'a'" in err
        assert "oops.txt" in err and err.rstrip().endswith("start a new run")
        assert "halted" not in err


def test_the_resume_of_a_run_that_a_moved_run_branch_ended_is_refused_without_the_word_halted(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    run_id = moved_run(repo, capsys)
    calls = list(scripted.calls)

    code, _, err = resume_main(repo, capsys, run_id)

    assert code == 1
    assert f"run {run_id!r} cannot be resumed: the run branch run/demo is at" in err
    assert err.rstrip().endswith("start a new run")
    assert "halted" not in err
    assert scripted.calls == calls


def test_the_first_resume_that_finds_a_moved_run_branch_is_refused_without_the_word_halted(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    run_id, _ = kill_a_run(repo, 3)
    git(repo, "update-ref", "refs/heads/run/demo", git(repo, "rev-parse", "main").strip())

    code, _, err = resume_main(repo, capsys, run_id)

    assert code == 1
    assert "run cannot be resumed: the run branch run/demo is at" in err and "start a new run" in err
    assert "halted" not in err


def test_status_of_a_run_that_an_invariant_violation_ended_shows_that_it_cannot_be_resumed(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    run_id = violated_run(repo, capsys, scripted)
    capsys.readouterr()

    assert delegate.main(["status", run_id, "--repo", str(repo), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["state"] == "failed"
    assert re.fullmatch(r"cannot be resumed: an invariant violation ended the run at ticket 'a'.*; start a new run", document["note"])

    assert delegate.main(["status", run_id, "--repo", str(repo)]) == 0
    assert "cannot be resumed" in capsys.readouterr().out


def test_status_of_a_run_that_a_moved_run_branch_ended_shows_that_it_cannot_be_resumed(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    run_id = moved_run(repo, capsys)
    capsys.readouterr()

    assert delegate.main(["status", run_id, "--repo", str(repo), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["state"] == "failed"
    assert document["note"].startswith("cannot be resumed: the run branch run/demo is at")
    assert document["note"].endswith("start a new run")
