"""`delegate run`: one run has one base (issues #90 and #91).

The base of a run is the commit of the base branch at `run-start`. The journal
records it, and a resume uses it. A coordinator can land a commit on the base
branch while a run goes on, but no ticket branch of the run starts from the
moving base branch: a ticket starts from the base of the run, from the run
branch, or from the previous ticket of a chain. A ticket with `blocked-by`
starts from the run branch in `assure` mode, so it holds the accepted work of
its blockers.

Each case writes a workflow into a real temporary git repository, registers a
scripted adapter by name, and calls `delegate.main`. A case that needs a run
that stopped kills a real child process, or cuts a finished journal.
"""

import json

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import (
    ScriptedAdapter, git, journal_path, kill_a_run, make_repo, read_journal, read_journal_file, resume_main, ticket_of, tree,
)
from .test_run_multi import HEAD, ticket

# The subject of the commits that the scripted specialist makes.
SPECIALIST_WORK = "scripted work (python-specialist)"

# The subject of the commit that lands on the base branch while the run goes on.
LANDED = "landed on main during the run"


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abcd"})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys, *extra):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def rev(repo, ref):
    return git(repo, "rev-parse", ref).strip()


def land_on_base(repo, name):
    """Commit the file `name` on the base branch, as a coordinator who lands other work during the run does. Return the new tip."""
    (repo / name).write_text("landed\n")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", LANDED)
    return rev(repo, "main")


def land_during(ticket_id, repo, name):
    """A hook of the scripted adapter: land a commit on the base branch when the specialist of `ticket_id` first starts."""

    def hook(request):
        if ticket_of(request) == ticket_id and not (repo / name).exists():
            land_on_base(repo, name)

    return hook


def subjects(repo, since, until):
    return git(repo, "log", "--format=%s", f"{since}..{until}").splitlines()


def files_changed(repo, since, until):
    return sorted(set(git(repo, "log", "--format=", "--name-only", f"{since}..{until}").split()))


def step_start(events, ticket_id):
    return next(e for e in events if e["event"] == "step-start" and e["ticket"] == ticket_id)


def test_the_journal_records_the_base_of_the_run_at_run_start(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_base = rev(repo, "main")

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    run_start = read_journal(out)[0]
    assert (run_start["event"], run_start["base_branch"], run_start["base_commit"]) == ("run-start", "main", run_base)


def test_a_ticket_cut_after_the_base_branch_moved_starts_from_the_base_of_the_run_and_its_verifier_diff_holds_only_its_commits(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))
    run_base = rev(repo, "main")
    scripted.on_specialist = land_during("a", repo, "landed.txt")

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert rev(repo, "main") != run_base  # the base branch moved while the run went on
    events = read_journal(out)
    assert step_start(events, "b")["base_commit"] == run_base
    # The verifier of ticket b sees the diff of ticket b only: no file of the commit that landed on the base branch.
    verifier_b = scripted.verifier_calls[1]
    assert "b.txt" in verifier_b.prompt
    assert "landed.txt" not in verifier_b.prompt
    advance_a = next(e for e in events if e["event"] == "run-branch-advance" and e["ticket"] == "a")
    assert subjects(repo, advance_a["to_commit"], "run/demo-b") == [SPECIALIST_WORK]
    assert "landed.txt" not in tree(repo, "run/demo")


def test_the_rebase_onto_the_run_branch_copies_no_commit_of_the_base_branch_onto_the_ticket_branch(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))
    run_base = rev(repo, "main")
    # LICENSE is a hotspot of the stack. A copy of the commit that lands it would be a hotspot finding of ticket b.
    scripted.on_specialist = land_during("a", repo, "LICENSE")

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    rebase_b = next(e for e in events if e["event"] == "rebase" and e["ticket"] == "b")
    assert rebase_b["result"] == "rebased"
    assert subjects(repo, rebase_b["onto_commit"], rebase_b["to_commit"]) == [SPECIALIST_WORK]
    assert files_changed(repo, rebase_b["onto_commit"], rebase_b["to_commit"]) == ["b.txt"]
    assert LANDED not in subjects(repo, run_base, "run/demo-b")
    assert not [e for e in events if e["event"] == "hotspot-finding"]
    assert "LICENSE" not in tree(repo, "run/demo")


# Two stacks: the file of ticket a is a hotspot of the stack of ticket b only.
TWO_STACKS = HEAD.replace('hotspots = ["LICENSE", ".github/workflows/*"]', 'hotspots = ["LICENSE"]') + """
[stacks.docs]
specialist = "docs-specialist"
verifier = "docs-verifier"
gates = ["ls *.txt"]
hotspots = ["LICENSE", "a.txt"]
""" + ticket("a") + ticket("b").replace('stack = "python"', 'stack = "docs"')


def cut_journal_after(repo, run_id, event, ticket_id):
    """Keep the journal up to the first matching event. This is the file that a kill right after the event leaves."""
    path = journal_path(repo, run_id)
    kept = []
    for line in path.read_text().splitlines(keepends=True):
        kept.append(line)
        parsed = json.loads(line)
        if parsed["event"] == event and parsed.get("ticket") == ticket_id:
            break
    path.write_text("".join(kept))


def test_a_resume_after_the_rebase_checks_the_hotspots_on_the_files_of_the_ticket_only(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO_STACKS)
    scripted.on_specialist = land_during("a", repo, "LICENSE")
    code, out, err = run(repo, capsys)
    assert (code, err) == (0, "")
    events = read_journal(out)
    run_id = events[0]["run_id"]
    advance_a = next(e for e in events if e["event"] == "run-branch-advance" and e["ticket"] == "a")
    # The state that a kill right after the rebase of ticket b leaves: ticket b is rebased, and the run branch holds ticket a.
    cut_journal_after(repo, run_id, "rebase", "b")
    git(repo, "branch", "-f", "run/demo", advance_a["to_commit"])

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    resumed = read_journal_file(repo, run_id)
    assert not [e for e in resumed if e["event"] == "hotspot-finding"]
    run_end = resumed[-1]
    assert (run_end["event"], run_end["built"], run_end["failed"]) == ("run-end", ["a", "b"], [])
    assert "LICENSE" not in tree(repo, "run/demo")


THREE = HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c")


def test_a_resume_starts_a_ticket_from_the_base_of_the_run_and_not_from_the_tip_of_the_base_branch(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_base = rev(repo, "main")
    run_id, _ = kill_a_run(repo, 3)  # killed as the specialist of ticket b starts
    land_on_base(repo, "landed.txt")

    code, out, err = resume_main(repo, capsys, run_id, "--break-lock")

    assert (code, err) == (0, "")
    events = read_journal_file(repo, run_id)
    assert events[0]["base_commit"] == run_base
    assert step_start(events, "c")["base_commit"] == run_base
    assert "landed.txt" not in scripted.verifier_calls[-1].prompt
    assert LANDED not in subjects(repo, run_base, "run/demo")


def test_a_resume_of_a_journal_with_no_base_commit_takes_the_base_of_the_run_from_the_first_run_branch_advance(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_base = rev(repo, "main")
    run_id, _ = kill_a_run(repo, 3)
    # An engine before this change wrote no `base_commit` in `run-start`.
    path = journal_path(repo, run_id)
    lines = path.read_text().splitlines(keepends=True)
    first = json.loads(lines[0])
    del first["base_commit"]
    path.write_text(json.dumps(first) + "\n" + "".join(lines[1:]))
    land_on_base(repo, "landed.txt")

    code, out, err = resume_main(repo, capsys, run_id, "--break-lock")

    assert (code, err) == (0, "")
    events = read_journal_file(repo, run_id)
    assert step_start(events, "c")["base_commit"] == run_base
    assert LANDED not in subjects(repo, run_base, "run/demo")


def test_in_economy_mode_a_ticket_with_no_built_predecessor_starts_from_the_base_of_the_run(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, (HEAD + ticket("a") + ticket("b")).replace('mode = "assure"', 'mode = "economy"'))
    run_base = rev(repo, "main")
    scripted.failing_tickets = {"a"}
    scripted.on_specialist = land_during("a", repo, "landed.txt")

    code, out, err = run(repo, capsys)

    assert code == 1  # ticket a failed
    events = read_journal(out)
    assert step_start(events, "b")["base_commit"] == run_base
    assert subjects(repo, run_base, "run/demo-b") == [SPECIALIST_WORK]
