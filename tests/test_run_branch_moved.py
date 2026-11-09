"""The run branch tip must be where the journal says before each rebase, fast-forward, chain verification and resume (#186).

The run branch moves only when the engine advances it after an ACCEPT. A commit that moves the run branch from
outside the engine (a specialist with a shell, a person, another tool) would land with no verification. Each case
moves the run branch with `git update-ref` while the engine works, in a real temporary git repository, and calls
`delegate.main`. The engine must write `run-branch-moved` with the expected tip and the tip that it found, and halt
the run as it halts for an invariant violation.
"""

import pytest

from .support import without_take_over
from .support import WORKFLOW, event_names, git, kill_a_run, make_repo, read_journal, read_journal_file, resume_main, run_main, tree
from .test_run_multi import HEAD, ticket

ECONOMY = WORKFLOW.replace('mode = "assure"', 'mode = "economy"')
TWO = HEAD + ticket("a") + ticket("b", ("a",))
ECONOMY_TWO = ECONOMY.replace(
    'gates = ["test -f feature.txt"]', 'gates = ["ls *.txt"]'
) + ticket("b", ("a",))


def evil_commit(repo, tmp_path) -> str:
    """A commit on top of `main` that adds evil.txt. It is on no branch. A side worktree makes it and goes away."""
    side = tmp_path / "side"
    git(repo, "worktree", "add", "--detach", str(side), "main")
    (side / "evil.txt").write_text("evil\n")
    git(side, "add", "evil.txt")
    git(side, "commit", "-q", "-m", "evil")
    sha = git(side, "rev-parse", "HEAD").strip()
    git(repo, "worktree", "remove", "--force", str(side))
    return sha


def move_run_branch(repo, commit):
    git(repo, "update-ref", "refs/heads/run/demo", commit)


def moved_event(events):
    (moved,) = [e for e in events if e["event"] == "run-branch-moved"]
    return moved


def test_a_specialist_that_moves_the_run_branch_halts_the_run_and_the_ticket_does_not_land(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    evil = evil_commit(repo, tmp_path)
    base = git(repo, "rev-parse", "main").strip()
    scripted.on_specialist = lambda request: move_run_branch(repo, evil)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    events = read_journal(out)
    assert code == 1
    assert "Traceback" not in err
    moved = moved_event(events)
    assert (moved["ticket"], moved["expected"], moved["found"]) == ("a", base, evil)
    assert "run-branch-advance" not in event_names(events)
    assert events[-1]["event"] == "run-end" and events[-1]["result"] == "failed"
    assert next(e for e in events if e["event"] == "step-end")["state"] == "failed"
    assert {e["ticket"] for e in events if e["event"] == "skip"} == {"b"}
    assert git(repo, "rev-parse", "run/demo").strip() == evil
    assert "a.txt" not in tree(repo, "run/demo")
    assert len(scripted.verifier_calls) == 0  # the verifier never sees a branch that rests on an unverified tip


def test_a_verifier_that_moves_the_run_branch_halts_the_run_before_the_fast_forward(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    evil = evil_commit(repo, tmp_path)
    base = git(repo, "rev-parse", "main").strip()
    scripted.during_verifier = lambda request: move_run_branch(repo, evil)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    events = read_journal(out)
    assert code == 1
    moved = moved_event(events)
    assert (moved["ticket"], moved["expected"], moved["found"]) == ("a", base, evil)
    assert "run-branch-advance" not in event_names(events)
    assert git(repo, "rev-parse", "run/demo").strip() == evil
    assert "a.txt" not in tree(repo, "run/demo")


def test_a_ticket_that_moves_the_run_branch_in_economy_mode_halts_the_run_before_the_chain_verification(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, ECONOMY_TWO)
    evil = evil_commit(repo, tmp_path)
    scripted.files_by_ticket = {"a": {"feature.txt": "a\n"}, "b": {"b.txt": "b\n"}}
    scripted.on_specialist = lambda request: move_run_branch(repo, evil)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    events = read_journal(out)
    assert code == 1
    moved = moved_event(events)
    assert moved["found"] == evil
    assert scripted.verifier_calls == []
    assert "run-branch-advance" not in event_names(events)
    assert git(repo, "rev-parse", "run/demo").strip() == evil


def test_a_resume_halts_when_the_run_branch_moved_between_the_runs_and_a_second_resume_is_refused(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    evil = evil_commit(repo, tmp_path)
    run_id, _ = kill_a_run(repo, 3)  # killed as the specialist of ticket b starts, after ticket a landed
    landed = git(repo, "rev-parse", "run/demo").strip()
    assert "a.txt" in tree(repo, "run/demo")
    move_run_branch(repo, evil)

    code, out, err = resume_main(repo, capsys, run_id)

    events = read_journal_file(repo, run_id)
    assert code == 1
    assert "Traceback" not in err
    moved = moved_event(events)
    assert (moved["expected"], moved["found"]) == (landed, evil)
    assert scripted.calls == []  # no specialist ran
    assert "b.txt" not in tree(repo, "run/demo")
    assert git(repo, "rev-parse", "run/demo").strip() == evil

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1
    assert "start a new run" in err
    assert scripted.calls == []
    assert len([e for e in read_journal_file(repo, run_id) if e["event"] == "run-branch-moved"]) == 1


def test_a_run_in_which_nothing_else_moves_the_run_branch_ends_built_with_each_advance_from_the_last(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    base = git(repo, "rev-parse", "main").strip()

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    events = read_journal(out)
    assert (code, err) == (0, "")
    assert "run-branch-moved" not in event_names(events)
    assert events[-1]["event"] == "run-end" and events[-1]["result"] == "built"
    advances = [e for e in events if e["event"] == "run-branch-advance"]
    assert [e["ticket"] for e in advances] == ["a", "b"]
    assert advances[0]["from_commit"] == base and advances[1]["from_commit"] == advances[0]["to_commit"]
    assert {"a.txt", "b.txt"} <= set(tree(repo, "run/demo"))


def test_a_resume_of_a_run_that_nothing_else_moved_goes_on(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO)
    run_id, _ = kill_a_run(repo, 3)

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, without_take_over(err)) == (0, "")
    assert "run-branch-moved" not in event_names(read_journal_file(repo, run_id))
    assert {"a.txt", "b.txt"} <= set(tree(repo, "run/demo"))


@pytest.mark.parametrize("workflow", [TWO, ECONOMY_TWO], ids=["assure", "economy"])
def test_a_run_whose_tip_is_the_base_commit_and_that_nothing_moves_builds_in_both_modes(tmp_path, capsys, scripted, workflow):
    repo = make_repo(tmp_path, workflow)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")
    assert "run-branch-moved" not in event_names(read_journal(out))
