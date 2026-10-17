"""`delegate run` in `assure` mode with the workflow field `ticket-start`.

With `ticket-start = "run-branch"`, each ticket branch starts from the tip of
the run branch at the start of its step, so a ticket sees the accepted work of
the tickets before it. Two tickets that change the same file then do not
conflict at the rebase onto the run branch. Without the field, a ticket branch
starts from the base of the run: the commit of the base branch at `run-start`.
A ticket with `blocked-by` starts from the run branch with every value of the
field, so it holds the accepted commits of its blockers (issue #91).

Each case writes a workflow into a real temporary git repository, registers a
scripted adapter by name, and calls `delegate.main`.
"""

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, WORKFLOW, git, make_repo, read_journal, tree

HEAD = WORKFLOW.split("[[tickets]]")[0].replace('gates = ["test -f feature.txt"]', 'gates = ["test -f shared.txt"]')

# Ticket b changes the file that ticket a adds: it appends one line to it.
SAME_FILE = {"a": {"shared.txt": "from a\n"}, "b": {"shared.txt": "from a\nfrom b\n"}}


def ticket(ticket_id: str, blocked_by: tuple[str, ...] = ()) -> str:
    blockers = ", ".join(f'"{b}"' for b in blocked_by)
    return f'\n[[tickets]]\nid = "{ticket_id}"\ntext = "Ticket {ticket_id} changes the shared file."\nstack = "python"\nblocked-by = [{blockers}]\n'


def workflow(ticket_start: str | None = None, mode: str = "assure", blocked: bool = True) -> str:
    """Two tickets that change one file. Ticket b waits on ticket a when `blocked` is set."""
    head = HEAD.replace('mode = "assure"', f'mode = "{mode}"')
    if ticket_start is not None:
        head = head.replace('adapter = "scripted"\n', f'adapter = "scripted"\nticket-start = "{ticket_start}"\n')
    return head + ticket("a") + ticket("b", ("a",) if blocked else ())


@pytest.fixture(autouse=True)
def committer_identity(monkeypatch):
    """The rebase writes commits, so git needs a committer. This sets the environment of the process, which is the real boundary."""
    for key, value in (("NAME", "Scratch"), ("EMAIL", "scratch@example.invalid")):
        monkeypatch.setenv(f"GIT_COMMITTER_{key}", value)
        monkeypatch.setenv(f"GIT_AUTHOR_{key}", value)


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter(files_by_ticket=dict(SAME_FILE))
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys, *extra):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_with_ticket_start_run_branch_two_tickets_that_change_one_file_both_build_with_no_rebase_conflict(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, workflow("run-branch"))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    rebases = {e["ticket"]: e["result"] for e in events if e["event"] == "rebase"}
    assert rebases == {"a": "rebased", "b": "rebased"}
    run_end = events[-1]
    assert (run_end["event"], run_end["built"], run_end["failed"]) == ("run-end", ["a", "b"], [])
    assert git(repo, "show", "run/demo:shared.txt") == "from a\nfrom b\n"
    # Ticket b started from the run branch after ticket a landed on it, and both verifiers ran at once, one for each ticket.
    advance_a = next(e for e in events if e["event"] == "run-branch-advance" and e["ticket"] == "a")
    start_b = next(e for e in events if e["event"] == "step-start" and e["ticket"] == "b")
    assert start_b["base_commit"] == advance_a["to_commit"]
    assert [e["ticket"] for e in events if e["event"] == "verify-start"] == ["a", "b"]


@pytest.mark.parametrize("ticket_start", [None, "base-branch"], ids=["no-ticket-start", "ticket-start-base-branch"])
def test_a_ticket_with_blocked_by_starts_from_the_run_branch_after_its_blockers_were_accepted(tmp_path, capsys, scripted, ticket_start):
    repo = make_repo(tmp_path, workflow(ticket_start))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    advance_a = next(e for e in events if e["event"] == "run-branch-advance" and e["ticket"] == "a")
    start_b = next(e for e in events if e["event"] == "step-start" and e["ticket"] == "b")
    assert start_b["base_commit"] == advance_a["to_commit"]
    # The branch of ticket b holds the accepted commit of ticket a, so the same file does not conflict at the rebase.
    git(repo, "merge-base", "--is-ancestor", advance_a["to_commit"], "run/demo-b")
    assert next(e for e in events if e["event"] == "rebase" and e["ticket"] == "b")["result"] == "rebased"
    assert (events[-1]["built"], events[-1]["failed"]) == (["a", "b"], [])
    assert git(repo, "show", "run/demo:shared.txt") == "from a\nfrom b\n"


@pytest.mark.parametrize("ticket_start", [None, "base-branch"], ids=["no-ticket-start", "ticket-start-base-branch"])
def test_a_ticket_with_no_blocked_by_starts_from_the_base_of_the_run_and_the_same_file_conflicts_at_the_rebase(
    tmp_path, capsys, scripted, ticket_start
):
    repo = make_repo(tmp_path, workflow(ticket_start, blocked=False))
    base_tip = git(repo, "rev-parse", "main").strip()

    code, out, err = run(repo, capsys)

    assert code == 1
    events = read_journal(out)
    start_b = next(e for e in events if e["event"] == "step-start" and e["ticket"] == "b")
    assert start_b["base_commit"] == base_tip
    conflict = next(e for e in events if e["event"] == "rebase" and e["ticket"] == "b")
    assert (conflict["result"], conflict["files"]) == ("conflict", ["shared.txt"])
    assert "ticket b:" in err and "shared.txt" in err
    assert git(repo, "show", "run/demo:shared.txt") == "from a\n"


def test_with_ticket_start_run_branch_a_ticket_with_no_blocked_by_starts_from_the_run_branch(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, workflow("run-branch", blocked=False))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    advance_a = next(e for e in events if e["event"] == "run-branch-advance" and e["ticket"] == "a")
    assert next(e for e in events if e["event"] == "step-start" and e["ticket"] == "b")["base_commit"] == advance_a["to_commit"]
    assert git(repo, "show", "run/demo:shared.txt") == "from a\nfrom b\n"


def test_with_ticket_start_run_branch_the_work_of_a_ticket_counts_from_the_run_branch_tip_and_not_from_the_base_branch(
    tmp_path, capsys, scripted
):
    # The specialist of ticket b commits nothing. The commit of ticket a is on its branch, but it is not the work of ticket b.
    scripted.files_by_ticket = {"a": SAME_FILE["a"], "b": {}}
    repo = make_repo(tmp_path, workflow("run-branch"))

    code, out, err = run(repo, capsys)

    assert code == 1
    events = read_journal(out)
    start_b = next(e for e in events if e["event"] == "step-start" and e["ticket"] == "b")
    end_b = next(e for e in events if e["event"] == "step-end" and e["ticket"] == "b")
    assert end_b["state"] == "failed"
    assert end_b["reason"] == f"branch run/demo-b holds no commit beyond {start_b['base_commit']}, though the report says committed"
    assert [e["ticket"] for e in events if e["event"] == "verify-start"] == ["a"]
    assert tree(repo, "run/demo") == ["seed.txt", "shared.txt", "workflow.toml"]


def test_the_dry_run_names_the_run_branch_as_the_start_of_each_ticket(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, workflow("run-branch"))

    code = delegate.main(["run", str(repo / "workflow.toml"), "--dry-run"])

    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    assert "each ticket starts from the run branch run/demo" in out.splitlines()


def test_the_dry_run_of_a_workflow_without_ticket_start_does_not_name_a_ticket_start(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, workflow())

    code = delegate.main(["run", str(repo / "workflow.toml"), "--dry-run"])

    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    assert "starts from" not in out


def test_a_ticket_start_that_is_not_known_is_an_error_that_names_the_field_and_the_values(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, workflow("previous-ticket"))

    code = delegate.main(["run", str(repo / "workflow.toml"), "--dry-run"])

    out, err = capsys.readouterr()
    assert (code, out) == (1, "")
    assert "ticket-start: 'previous-ticket' is not a ticket start; known values: base-branch, run-branch" in err


def test_ticket_start_run_branch_in_economy_mode_is_an_error_because_the_chain_sets_the_start(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, workflow("run-branch", mode="economy"))

    code = delegate.main(["run", str(repo / "workflow.toml"), "--dry-run"])

    out, err = capsys.readouterr()
    assert (code, out) == (1, "")
    assert "ticket-start: 'run-branch' works only in assure mode; in economy mode each ticket starts from the previous ticket" in err
