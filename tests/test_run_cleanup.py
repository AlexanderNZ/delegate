"""The clean-up at the end of a run that ends `built` (#167).

A run that ends `built` removes the worktree of each ticket and deletes each
ticket branch that the run branch holds. It keeps the run branch, the journal,
the reports and the gate logs, and it records one `cleanup` event before
`run-end`. A worktree with a change that is not committed stays, and so does
its branch. A run that ends `failed` keeps everything, and so does a workflow
with `keep-worktrees = true`.

Each case runs `delegate.main` against a real temporary git repository with
the scripted adapter, and reads the git state and the journal.
"""

from pathlib import Path

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import CLEANED_WORKFLOW, ScriptedAdapter, event_names, git, make_repo, read_journal

TWO_TICKETS = CLEANED_WORKFLOW + '\n[[tickets]]\nid = "b"\ntext = "The import command reads a CSV file."\nstack = "python"\nblocked-by = []\n'


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter(files_by_ticket={"b": {"feature.txt": "feature\n", "b.txt": "b\n"}})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo: Path, capsys) -> tuple[int, str, str]:
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def worktrees(repo: Path) -> list[Path]:
    """The path of each worktree that git has registered, the main checkout first."""
    lines = git(repo, "worktree", "list", "--porcelain").splitlines()
    return [Path(line.removeprefix("worktree ")).resolve() for line in lines if line.startswith("worktree ")]


def branches(repo: Path) -> set[str]:
    return set(git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").split())


def ticket_worktree(events: list[dict[str, object]], ticket: str) -> Path:
    return Path(str(next(e["worktree"] for e in events if e["event"] == "step-start" and e["ticket"] == ticket))).resolve()


def test_a_run_of_two_tickets_that_ends_built_removes_their_worktrees_and_branches_and_keeps_the_run_branch(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path, TWO_TICKETS)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    assert worktrees(repo) == [repo.resolve()]
    assert branches(repo) == {"main", "run/demo"}
    assert git(repo, "show", "run/demo:feature.txt") == "feature\n"
    assert not ticket_worktree(events, "a").exists() and not ticket_worktree(events, "b").exists()
    # The clean-up comes before `run-end`, so the newest event of a run that ended is still `run-end`.
    assert event_names(events)[-2:] == ["cleanup", "run-end"]
    cleanup = events[-2]
    assert sorted(Path(p).resolve() for p in cleanup["worktrees_removed"]) == sorted(
        [ticket_worktree(events, "a"), ticket_worktree(events, "b")]
    )
    assert sorted(cleanup["branches_deleted"]) == ["run/demo-a", "run/demo-b"]
    assert (cleanup["worktrees_kept"], cleanup["branches_kept"]) == ([], [])
    # The evidence of the run stays: the journal, the reports and the gate logs.
    run_dir = repo / ".git" / "delegate" / "runs" / str(events[0]["run_id"])
    assert (run_dir / "journal.jsonl").is_file()
    assert (run_dir / "reports" / "a.specialist.json").is_file()
    assert any(run_dir.rglob("*.log"))


def test_a_run_that_ends_failed_keeps_its_worktrees_and_branches_and_writes_no_cleanup(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO_TICKETS)
    scripted.failing_tickets = {"b"}

    code, out, err = run(repo, capsys)

    assert code == 1
    events = read_journal(out)
    assert events[-1]["event"] == "run-end" and events[-1]["result"] == "failed"
    assert "cleanup" not in event_names(events)
    assert set(worktrees(repo)) == {repo.resolve(), ticket_worktree(events, "a"), ticket_worktree(events, "b")}
    assert {"run/demo-a", "run/demo-b"} <= branches(repo)


def test_a_worktree_with_a_change_that_is_not_committed_is_kept_with_its_branch_and_the_event_names_both(
    tmp_path, capsys, scripted
):
    # The gate of the stack leaves a file that no commit holds in the worktree of ticket b only.
    gate = 'test -f feature.txt && { test ! -f b.marker || echo left > leftover.txt; }'
    repo = make_repo(tmp_path, TWO_TICKETS.replace('gates = ["test -f feature.txt"]', f"gates = ['{gate}']"))
    scripted.files_by_ticket = {"b": {"feature.txt": "feature\n", "b.marker": "b\n"}}

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    kept = ticket_worktree(events, "b")
    assert (kept / "leftover.txt").read_text() == "left\n"
    assert worktrees(repo) == [repo.resolve(), kept]
    assert branches(repo) == {"main", "run/demo", "run/demo-b"}
    cleanup = next(e for e in events if e["event"] == "cleanup")
    assert [Path(p).resolve() for p in cleanup["worktrees_removed"]] == [ticket_worktree(events, "a")]
    assert cleanup["branches_deleted"] == ["run/demo-a"]
    ((worktree_kept, worktree_reason),) = [(Path(k["worktree"]).resolve(), k["reason"]) for k in cleanup["worktrees_kept"]]
    assert worktree_kept == kept and "leftover.txt" in worktree_reason
    assert [(k["branch"], k["reason"]) for k in cleanup["branches_kept"]] == [("run/demo-b", "its worktree is kept")]


def test_keep_worktrees_true_keeps_every_worktree_and_branch_and_writes_no_cleanup(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO_TICKETS.replace('adapter = "scripted"\n', 'adapter = "scripted"\nkeep-worktrees = true\n'))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    assert events[-1]["result"] == "built"
    assert "cleanup" not in event_names(events)
    assert set(worktrees(repo)) == {repo.resolve(), ticket_worktree(events, "a"), ticket_worktree(events, "b")}
    assert branches(repo) == {"main", "run/demo", "run/demo-a", "run/demo-b"}


def test_keep_worktrees_that_is_not_a_boolean_is_an_error_that_names_the_field(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, CLEANED_WORKFLOW.replace('adapter = "scripted"\n', 'adapter = "scripted"\nkeep-worktrees = "yes"\n'))

    code = delegate.main(["run", str(repo / "workflow.toml"), "--dry-run"])

    out, err = capsys.readouterr()
    assert (code, out) == (1, "")
    assert "keep-worktrees: must be true or false, not 'yes'" in err


def test_an_economy_plan_change_after_the_cleanup_starts_the_new_ticket_from_the_run_branch_and_cleans_up_again(
    tmp_path, capsys, scripted
):
    head = CLEANED_WORKFLOW.split("[[tickets]]")[0].replace('mode = "assure"', 'mode = "economy"')
    head = head.replace('gates = ["test -f feature.txt"]', 'gates = ["ls *.txt"]')
    tickets = {t: f'\n[[tickets]]\nid = "{t}"\ntext = "Ticket {t} adds its file."\nstack = "python"\nblocked-by = []\n' for t in "abc"}
    scripted.files_by_ticket = {t: {f"{t}.txt": f"{t}\n"} for t in "abc"}
    repo = make_repo(tmp_path, head + tickets["a"] + tickets["b"])
    code, out, err = run(repo, capsys)
    assert (code, err) == (0, "")
    run_id = str(read_journal(out)[0]["run_id"])
    assert branches(repo) == {"main", "run/demo"}
    chain_tip = git(repo, "rev-parse", "run/demo").strip()
    (repo / "workflow.toml").write_text(head + tickets["a"] + tickets["b"] + tickets["c"])

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--resume", run_id])
    out, err = capsys.readouterr()

    assert (code, err) == (0, "")
    events = read_journal(out)
    start_c = next(e for e in events if e["event"] == "step-start" and e["ticket"] == "c")
    assert start_c["base_commit"] == chain_tip
    assert git(repo, "show", "run/demo:c.txt") == "c\n"
    assert git(repo, "rev-parse", "run/demo~1").strip() == chain_tip
    assert worktrees(repo) == [repo.resolve()]
    assert branches(repo) == {"main", "run/demo"}
    cleanups = [e for e in events if e["event"] == "cleanup"]
    assert [sorted(e["branches_deleted"]) for e in cleanups] == [["run/demo-a", "run/demo-b"], ["run/demo-c"]]
    assert events[-1]["event"] == "run-end" and (events[-1]["result"], events[-1]["built"]) == ("built", ["a", "b", "c"])
