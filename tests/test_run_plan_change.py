"""`delegate run --resume` with a changed workflow: the plan change of a run (#107).

The built part of a run is fixed. A new ticket goes after it, at the end or
before a ticket that has not started. A ticket that has not started can change
or go. The coordinator reviews each later ticket that has not started
(`--reviewed`), and the briefs of those tickets get a section "Plan change".
The journal records each change as `plan-change`.

Each case runs `delegate.main` against a real temporary git repository with the
scripted adapter. A run stops when the scripted verifier of a ticket raises the
interrupt of Ctrl-C, which leaves what a stop leaves: a journal with no
`run-end`, and a step that is open.
"""

import json

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, event_names, git, journal_path, make_repo, read_journal_file, resume_main, run_main, ticket_of, tree
from .test_run_economy import ECONOMY
from .test_run_multi import HEAD, ticket


def text_of(ticket_id: str) -> str:
    return f"Ticket {ticket_id} adds its file."


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abcdxy"})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def write_workflow(repo, text: str) -> None:
    (repo / "workflow.toml").write_text(text)


def start(repo, capsys) -> tuple[int, str, str]:
    return run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))


def run_id_of(repo) -> str:
    (run_dir,) = (repo / ".git" / "delegate" / "runs").iterdir()
    return run_dir.name


def stopped_at_the_verifier_of(repo, capsys, scripted, ticket_id: str) -> str:
    """Start the run of `workflow.toml`, stop it with Ctrl-C as the verifier of `ticket_id` starts, and return the run id."""

    def interrupt(request):
        if ticket_of(request) == ticket_id:
            raise KeyboardInterrupt

    scripted.during_verifier = interrupt
    with pytest.raises(KeyboardInterrupt):
        start(repo, capsys)
    capsys.readouterr()
    scripted.during_verifier = None
    clear_calls(scripted)
    return run_id_of(repo)


def finished(repo, capsys, scripted) -> str:
    code, out, err = start(repo, capsys)
    assert (code, err) == (0, ""), err
    clear_calls(scripted)
    return run_id_of(repo)


def clear_calls(scripted) -> None:
    for calls in (scripted.calls, scripted.verifier_calls, scripted.fixup_calls, scripted.continuation_calls):
        calls.clear()


def plan_changes(repo, run_id):
    return [e for e in read_journal_file(repo, run_id) if e["event"] == "plan-change"]


def test_the_dry_run_of_a_resume_prints_the_orders_the_verdict_of_each_rule_and_each_later_ticket_with_the_added_text(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",)))
    run_id = stopped_at_the_verifier_of(repo, capsys, scripted, "a")
    write_workflow(repo, HEAD + ticket("a") + ticket("x", ("a",)) + ticket("b", ("x",)) + ticket("c", ("b",)))
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--resume", run_id, "--repo", str(repo), "--dry-run")

    lines = out.splitlines()
    assert "old order: a, b, c" in lines and "new order: a, x, b, c" in lines
    for rule in range(1, 10):
        assert any(line.startswith(f"rule {rule}: ") for line in lines), (rule, out)
    (rule_8,) = [line for line in lines if line.startswith("rule 8: ")]
    assert "refused" in rule_8 and "b, c" in rule_8
    assert "later tickets to review, in plan order: b, c" in lines
    assert "ticket x (added):" in lines and f"  {text_of('x')}" in lines
    assert code == 1 and "--reviewed" in err
    # A dry run changes nothing: no journal line, no adapter call, no branch.
    assert journal_path(repo, run_id).read_bytes() == before
    assert scripted.calls == [] and scripted.verifier_calls == []
    assert "run/demo-x" not in git(repo, "branch", "--list", "run/demo-x")

    code, out, err = run_main(
        capsys, str(repo / "workflow.toml"), "--resume", run_id, "--repo", str(repo), "--dry-run", "--reviewed", "b,c"
    )

    assert (code, err) == (0, "")
    (rule_8,) = [line for line in out.splitlines() if line.startswith("rule 8: ")]
    assert rule_8.startswith("rule 8: ok")
    assert journal_path(repo, run_id).read_bytes() == before


def test_a_resume_with_a_plan_change_and_no_review_of_the_later_tickets_is_refused_and_names_them(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",)))
    run_id = stopped_at_the_verifier_of(repo, capsys, scripted, "a")
    write_workflow(repo, HEAD + ticket("a") + ticket("x", ("a",)) + ticket("b", ("x",)) + ticket("c", ("b",)))
    before = journal_path(repo, run_id).read_bytes()

    for reviewed in ((), ("--reviewed", "b")):
        code, out, err = resume_main(repo, capsys, run_id, *reviewed)

        assert code == 1 and "--reviewed" in err
        assert "'c'" in err and ("'b'" in err) == (reviewed == ())
        assert journal_path(repo, run_id).read_bytes() == before
        assert scripted.calls == [] and scripted.verifier_calls == []

    code, out, err = resume_main(repo, capsys, run_id, "--reviewed", "all")

    assert (code, err) == (0, "")
    (change,) = plan_changes(repo, run_id)
    assert change["reviewed"] == ["b", "c"]
    assert (change["added"], change["changed"], change["removed"]) == (["x"], ["b"], [])
    assert change["order"] == ["a", "x", "b", "c"]


def test_the_briefs_of_a_later_ticket_hold_the_plan_change_section_and_a_ticket_before_the_insertion_point_has_none(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b") + ticket("c"))
    run_id = stopped_at_the_verifier_of(repo, capsys, scripted, "a")
    write_workflow(repo, HEAD + ticket("a") + ticket("b") + ticket("x") + ticket("c"))

    code, out, err = resume_main(repo, capsys, run_id, "--reviewed", "c")

    assert (code, err) == (0, "")
    specialist = {ticket_of(call): call.prompt for call in scripted.calls}
    verifier = {ticket_of(call): call.prompt for call in scripted.verifier_calls}
    assert list(specialist) == ["b", "x", "c"]
    for prompt in (specialist["c"], verifier["c"]):
        section = prompt.split("## Plan change", 1)[1].split("\n## ", 1)[0]
        assert "Ticket x (added)" in section and text_of("x") in section and "`run/demo`" in section
    assert "report the status `blocked`" in specialist["c"].split("## Plan change", 1)[1].split("\n## ", 1)[0]
    assert "Check the diff against the changed plan" in verifier["c"].split("## Plan change", 1)[1].split("\n## ", 1)[0]
    for ticket_id in ("b", "x"):
        assert "## Plan change" not in specialist[ticket_id] and "## Plan change" not in verifier[ticket_id]
    assert "## Plan change" not in verifier["a"]


def test_a_ticket_added_at_the_end_of_a_run_that_ended_is_built_after_the_others(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))
    run_id = finished(repo, capsys, scripted)
    built_tip = git(repo, "rev-parse", "run/demo").strip()
    write_workflow(repo, HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",)))

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert [ticket_of(c) for c in scripted.calls] == ["c"] and [ticket_of(c) for c in scripted.verifier_calls] == ["c"]
    assert git(repo, "merge-base", "--is-ancestor", built_tip, "run/demo") == ""
    assert {"a.txt", "b.txt", "c.txt"} <= set(tree(repo, "run/demo"))
    events = read_journal_file(repo, run_id)
    assert [e["ticket"] for e in events if e["event"] == "step-start"] == ["a", "b", "c"]
    run_end = [e for e in events if e["event"] == "run-end"][-1]
    assert (run_end["result"], run_end["built"]) == ("built", ["a", "b", "c"])


def test_a_ticket_inserted_before_a_ticket_that_has_not_started_is_built_first_and_its_blocker_is_respected(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))
    run_id = stopped_at_the_verifier_of(repo, capsys, scripted, "a")
    write_workflow(repo, HEAD + ticket("a") + ticket("b", ("a", "x")) + ticket("x", ("a",)))
    seen: dict[str, list[str]] = {}
    scripted.on_specialist = lambda request: seen.setdefault(ticket_of(request), sorted(p.name for p in request.cwd.glob("*.txt")))

    code, out, err = resume_main(repo, capsys, run_id, "--reviewed", "b")

    assert (code, err) == (0, "")
    assert [ticket_of(c) for c in scripted.calls] == ["x", "b"]
    assert [ticket_of(c) for c in scripted.verifier_calls] == ["a", "x", "b"]
    # Ticket x waits on a: its branch starts from the run branch, which holds the accepted work of a.
    assert seen["x"] == ["a.txt", "seed.txt"]
    assert seen["b"] == ["a.txt", "seed.txt", "x.txt"]
    assert {"a.txt", "b.txt", "x.txt"} <= set(tree(repo, "run/demo"))


@pytest.mark.parametrize(
    "changed, field",
    [
        pytest.param(ticket("a").replace(text_of("a"), "Ticket a adds another file.") + ticket("b", ("a",)), "text", id="text"),
        pytest.param(ticket("a") + ticket("b"), "blocked-by", id="blocked-by"),
    ],
)
def test_a_change_to_a_built_ticket_is_refused_and_the_message_names_the_ticket_and_the_field(tmp_path, capsys, scripted, changed, field):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))
    run_id = finished(repo, capsys, scripted)
    write_workflow(repo, HEAD + changed + ticket("c"))
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = resume_main(repo, capsys, run_id)

    target = "'a'" if field == "text" else "'b'"
    assert code == 1 and target in err and field in err and "has started" in err
    assert journal_path(repo, run_id).read_bytes() == before
    assert scripted.calls == []


def test_an_insertion_before_a_built_ticket_is_refused(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))
    run_id = finished(repo, capsys, scripted)
    write_workflow(repo, HEAD + ticket("a") + ticket("x") + ticket("b"))
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = resume_main(repo, capsys, run_id, "--reviewed", "all")

    assert code == 1 and "'x'" in err and "'b'" in err and "new run" in err
    assert journal_path(repo, run_id).read_bytes() == before
    assert scripted.calls == []
    assert "run/demo-x" not in git(repo, "branch", "--list", "run/demo-x")


def test_a_ticket_that_has_not_started_can_be_removed_and_a_ticket_that_has_started_cannot(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b") + ticket("c"))
    run_id = stopped_at_the_verifier_of(repo, capsys, scripted, "a")
    write_workflow(repo, HEAD + ticket("b") + ticket("c"))

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1 and "'a'" in err and "removed" in err

    write_workflow(repo, HEAD + ticket("a") + ticket("b"))

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert [ticket_of(c) for c in scripted.calls] == ["b"]
    events = read_journal_file(repo, run_id)
    (change,) = plan_changes(repo, run_id)
    assert (change["removed"], change["order"], change["reviewed"]) == (["c"], ["a", "b"], [])
    run_end = [e for e in events if e["event"] == "run-end"][-1]
    assert (run_end["built"], run_end["skipped"]) == (["a", "b"], [])
    assert "c.txt" not in tree(repo, "run/demo")


def test_in_an_economy_chain_a_ticket_added_after_the_chain_verification_gets_its_own_gates_and_verifier(tmp_path, capsys, scripted):
    head = ECONOMY.split("[[tickets]]")[0].replace('gates = ["test -f feature.txt"]', 'gates = ["ls *.txt"]')
    repo = make_repo(tmp_path, head + ticket("a") + ticket("b", ("a",)))
    run_id = finished(repo, capsys, scripted)
    verified = [e for e in read_journal_file(repo, run_id) if e["event"] == "verify-start"]
    assert [e["tickets"] for e in verified] == [["a", "b"]]
    chain_tip = git(repo, "rev-parse", "run/demo").strip()
    write_workflow(repo, head + ticket("a") + ticket("b", ("a",)) + ticket("c"))

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert [ticket_of(c) for c in scripted.calls] == ["c"]
    events = read_journal_file(repo, run_id)
    after = events[event_names(events).index("plan-change") :]
    assert [e["base_commit"] for e in after if e["event"] == "step-start"] == [chain_tip]
    gates = [e for e in after if e["event"] in ("gate-result", "gate-skip") and e["phase"] == "chain"]
    assert [(e["event"], e["ticket"], e["commit"]) for e in gates] == [("gate-result", "c", git(repo, "rev-parse", "run/demo-c").strip())]
    assert [e["tickets"] for e in after if e["event"] == "verify-start"] == [["c"]]
    assert len(scripted.verifier_calls) == 1
    assert git(repo, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "run/demo-c").strip()
    run_end = [e for e in events if e["event"] == "run-end"][-1]
    assert (run_end["result"], run_end["built"]) == ("built", ["a", "b", "c"])


def test_plan_change_is_in_the_journal_a_second_resume_compares_with_the_newest_plan_and_status_counts_the_new_ticket(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))
    run_id = stopped_at_the_verifier_of(repo, capsys, scripted, "a")
    write_workflow(repo, HEAD + ticket("a") + ticket("x", ("a",)) + ticket("b", ("a",)))

    def interrupt(request):
        if ticket_of(request) == "x":
            raise KeyboardInterrupt

    scripted.during_verifier = interrupt
    with pytest.raises(KeyboardInterrupt):
        resume_main(repo, capsys, run_id, "--reviewed", "b")
    capsys.readouterr()
    scripted.during_verifier = None
    (change,) = plan_changes(repo, run_id)
    assert (change["added"], change["order"], change["reviewed"]) == (["x"], ["a", "x", "b"], ["b"])
    assert [entry["id"] for entry in change["plan"]] == ["a", "x", "b"]
    clear_calls(scripted)

    # The second resume compares the workflow with the newest plan: no change, so no --reviewed and no second plan-change.
    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert len(plan_changes(repo, run_id)) == 1
    assert [ticket_of(c) for c in scripted.calls] == ["b"]
    assert "Ticket x (added)" in scripted.calls[0].prompt.split("## Plan change", 1)[1]

    code = delegate.main(["status", "--repo", str(repo), "--json", run_id])
    snapshot = json.loads(capsys.readouterr().out)

    assert code == 0
    assert (snapshot["tickets_total"], snapshot["tickets_ended"]) == (3, 3)
    assert [t["id"] for t in snapshot["tickets"]] == ["a", "x", "b"]


def test_a_workflow_with_another_mode_than_the_run_is_refused_and_the_message_names_the_field(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))
    run_id = stopped_at_the_verifier_of(repo, capsys, scripted, "a")
    write_workflow(repo, (HEAD + ticket("a") + ticket("b")).replace('mode = "assure"', 'mode = "economy"'))
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1 and "mode" in err and run_id in err
    assert journal_path(repo, run_id).read_bytes() == before


def test_the_dry_run_of_a_resume_with_no_plan_change_says_so_and_changes_nothing(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))
    run_id = stopped_at_the_verifier_of(repo, capsys, scripted, "a")
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = run_main(capsys, "--resume", run_id, "--repo", str(repo), "--dry-run")

    assert (code, err) == (0, "")
    assert "no plan change" in out and "order: a, b" in out
    assert journal_path(repo, run_id).read_bytes() == before


def test_reviewed_without_resume_is_a_usage_error(tmp_path, capsys):
    repo = make_repo(tmp_path, HEAD + ticket("a"))

    with pytest.raises(SystemExit) as stop:
        delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--reviewed", "a"])

    assert stop.value.code == 2 and "--reviewed" in capsys.readouterr().err


def test_a_plan_change_does_not_go_on_with_a_run_that_an_invariant_violation_halted(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))

    def write_in_the_real_worktree(request):
        (run_dir,) = (repo / ".git" / "delegate" / "worktrees").iterdir()
        (run_dir / "a" / "oops.txt").write_text("by the verifier\n")

    scripted.during_verifier = write_in_the_real_worktree
    code, out, err = start(repo, capsys)
    assert code == 1 and "invariant violation" in err
    scripted.during_verifier = None
    clear_calls(scripted)
    run_id = run_id_of(repo)
    write_workflow(repo, HEAD + ticket("a") + ticket("b"))
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1 and "invariant violation" in err and "new run" in err
    assert journal_path(repo, run_id).read_bytes() == before
    assert scripted.calls == []


def test_a_new_ticket_whose_branch_exists_already_is_refused_and_the_message_names_the_branch(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id = finished(repo, capsys, scripted)
    git(repo, "branch", "run/demo-b", "main")
    write_workflow(repo, HEAD + ticket("a") + ticket("b"))
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1 and "'run/demo-b'" in err and "exists already" in err
    assert journal_path(repo, run_id).read_bytes() == before
    assert scripted.calls == []
