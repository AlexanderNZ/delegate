"""`delegate run --mode economy` builds a chain and verifies once for each stack at the end of it.

Each case writes a workflow into a real temporary git repository, registers a
scripted adapter by name, and calls `delegate.main`. The scripted adapter plays
the specialists and the verifiers, so the engine runs end to end with no model.
The expected values come from the ticket's acceptance criteria.
"""

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, WORKFLOW, event_names, git, make_repo, read_journal

ECONOMY = WORKFLOW.replace('mode = "assure"', 'mode = "economy"')

THREE_TICKETS = ECONOMY + '''
[[tickets]]
id = "b"
text = "The export command writes a header row."
stack = "python"
blocked-by = ["a"]

[[tickets]]
id = "c"
text = "The export command quotes a comma."
stack = "python"
blocked-by = []
'''


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter(files_by_ticket={"a": {"feature.txt": "a\n"}, "b": {"b.txt": "b\n"}, "c": {"c.txt": "c\n"}})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys, *extra):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def tip(repo, ref):
    return git(repo, "rev-parse", ref).strip()


def test_each_branch_starts_from_the_tip_of_the_previous_ticket(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE_TICKETS)
    main_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert tip(repo, "run/demo-a~1") == main_tip
    assert tip(repo, "run/demo-b~1") == tip(repo, "run/demo-a")
    assert tip(repo, "run/demo-c~1") == tip(repo, "run/demo-b")
    # The tickets b and c write no feature.txt. Their gate passes only because the branch holds the work of ticket a.
    assert git(repo, "ls-tree", "-r", "--name-only", "run/demo-c").split() == ["b.txt", "c.txt", "feature.txt", "seed.txt", "workflow.toml"]
    bases = {e["ticket"]: e["base_commit"] for e in read_journal(out) if e["event"] == "step-start"}
    assert bases == {"a": main_tip, "b": tip(repo, "run/demo-a"), "c": tip(repo, "run/demo-b")}


def test_a_ticket_that_failed_is_not_part_of_the_chain(tmp_path, capsys, scripted):
    scripted.failing_tickets = {"a"}
    scripted.files_by_ticket["b"] = {"feature.txt": "b\n"}  # the gate needs feature.txt, and ticket a does not make it
    scripted.files_by_ticket["c"] = {"c.txt": "c\n"}
    repo = make_repo(tmp_path, THREE_TICKETS.replace('blocked-by = ["a"]', 'blocked-by = []'))
    main_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert code == 1
    bases = {e["ticket"]: e["base_commit"] for e in read_journal(out) if e["event"] == "step-start"}
    assert bases["a"] == main_tip
    assert bases["b"] == main_tip  # b follows the failed ticket a in the plan, and starts from the base
    assert bases["c"] == tip(repo, "run/demo-b")


def tier_file(tmp_path):
    """A tier table whose model names tell the tier apart."""
    lines = ['[effort]\nlevels = ["low"]\n\n[effort.opencode]\nvariants = ["high"]\n']
    for tier in ("strong", "standard", "cheap", "verifier"):
        lines.append(f'[tiers.{tier}]\nclaude-code = "{tier}-model"\n')
    lines.append('[allowed-models]\nclaude-code = []\n\n[budget]\nclaude-code-description-chars = 1\n')
    path = tmp_path / "tiers.toml"
    path.write_text("\n".join(lines))
    return path


def test_a_clean_run_makes_one_verifier_invocation_for_the_stack_and_fast_forwards_the_run_branch(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE_TICKETS)
    main_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert len(scripted.calls) == 3
    (call,) = scripted.verifier_calls
    assert call.agent == "python-verifier"
    assert tip(repo, "run/demo") == tip(repo, "run/demo-c") != main_tip
    assert tip(repo, "main") == main_tip
    events = read_journal(out)
    assert [e["ticket"] for e in events if e["event"] == "step-end"] == ["a", "b", "c"]
    names = event_names(events)
    assert names.count("verify-start") == 1 and names.count("verdict") == 1
    assert names.index("verdict") < names.index("run-branch-advance") < names.index("run-end")
    (advance,) = [e for e in events if e["event"] == "run-branch-advance"]
    assert (advance["from_commit"], advance["to_commit"]) == (main_tip, tip(repo, "run/demo-c"))
    run_end = next(e for e in events if e["event"] == "run-end")
    assert (run_end["result"], run_end["built"], run_end["failed"]) == ("built", ["a", "b", "c"], [])


def test_the_verifier_brief_holds_every_ticket_of_the_chain_with_its_own_diff_and_no_line_of_a_specialist_report(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE_TICKETS)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (call,) = scripted.verifier_calls
    for ticket, text in (("a", "writes a CSV file"), ("b", "writes a header row"), ("c", "quotes a comma")):
        assert f"Ticket {ticket}" in call.prompt and text in call.prompt
    for path in ("feature.txt", "b.txt", "c.txt"):
        assert f"+++ b/{path}" in call.prompt
    assert str(scripted.calls[0].report_path) not in call.prompt
    assert "Done." not in call.prompt  # the summary field of the specialist report
    assert "square brackets" in call.prompt  # the brief tells the verifier how to label a finding with a ticket id


@pytest.mark.parametrize("mode", ["assure", "economy"])
@pytest.mark.parametrize("override", [None, "cheap"])
def test_specialists_run_on_standard_in_economy_and_the_verifier_tier_is_the_same_in_every_mode(tmp_path, capsys, scripted, mode, override):
    # In assure mode every ticket starts from the base branch, so one ticket suffices.
    workflow = WORKFLOW if mode == "assure" else THREE_TICKETS
    if override:
        workflow = workflow.replace("[stacks.python]", f'[tier-overrides]\nspecialist = "{override}"\n\n[stacks.python]')
    repo = make_repo(tmp_path, workflow)

    code, out, err = run(repo, capsys, "--tiers", str(tier_file(tmp_path)))

    assert (code, err) == (0, "")
    expected = override or ("strong" if mode == "assure" else "standard")
    assert {call.model for call in scripted.calls} == {f"{expected}-model"}
    assert {call.model for call in scripted.verifier_calls} == {"verifier-model"}


TWO_STACKS = ECONOMY.replace(
    "[[tickets]]",
    '[stacks.docs]\nspecialist = "docs-specialist"\nverifier = "docs-verifier"\ngates = ["test -f feature.txt"]\nhotspots = []\n\n[[tickets]]',
    1,
) + '''
[[tickets]]
id = "b"
text = "The guide describes the export command."
stack = "docs"
blocked-by = []

[[tickets]]
id = "c"
text = "The export command quotes a comma."
stack = "python"
blocked-by = []
'''


def test_a_chain_of_two_stacks_gets_one_verifier_for_each_stack_over_the_commits_of_that_stack_only(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO_STACKS)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert [call.agent for call in scripted.verifier_calls] == ["python-verifier", "docs-verifier"]
    python_prompt, docs_prompt = (call.prompt for call in scripted.verifier_calls)
    assert "Ticket a" in python_prompt and "Ticket c" in python_prompt and "Ticket b" not in python_prompt
    assert "+++ b/feature.txt" in python_prompt and "+++ b/c.txt" in python_prompt and "+++ b/b.txt" not in python_prompt
    assert "Ticket b" in docs_prompt and "Ticket a" not in docs_prompt and "Ticket c" not in docs_prompt
    assert "+++ b/b.txt" in docs_prompt and "+++ b/c.txt" not in docs_prompt
    assert tip(repo, "run/demo") == tip(repo, "run/demo-c")
    verdicts = [(e["stack"], e["tickets"], e["verdict"]) for e in read_journal(out) if e["event"] == "verdict"]
    assert verdicts == [("python", ["a", "c"], "ACCEPT"), ("docs", ["b"], "ACCEPT")]


FINDING_B = "[b] The header row is missing."


def reject_once(scripted, *findings):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = list(findings) or [FINDING_B]


def test_a_reject_gets_one_fixup_round_as_a_new_commit_on_the_chain_tip_and_a_fresh_scoped_verifier(tmp_path, capsys, scripted):
    reject_once(scripted)
    repo = make_repo(tmp_path, THREE_TICKETS)
    rejected = None

    def note_tip(request):
        nonlocal rejected
        rejected = rejected or tip(repo, "run/demo-c")

    scripted.during_verifier = note_tip

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (fixup,) = scripted.fixup_calls
    assert fixup.agent == "python-specialist"
    assert fixup.cwd.name == "c" and "## Findings to fix" in fixup.prompt and FINDING_B in fixup.prompt
    assert tip(repo, "run/demo-c~1") == rejected  # the fix-up is one new commit on top of the rejected tip
    first, second = scripted.verifier_calls
    assert first.cwd != second.cwd and "## Findings under verification" not in first.prompt
    assert second.prompt.startswith("## Findings under verification") and FINDING_B in second.prompt
    delta = second.prompt.split("## Delta")[1].split("## Gates")[0]
    assert "+++ b/fixup-1.txt" in delta and "+++ b/b.txt" not in delta  # the delta holds the fix-up only
    assert tip(repo, "run/demo") == tip(repo, "run/demo-c")
    events = read_journal(out)
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["REJECT", "ACCEPT"]
    assert [e["event"] for e in events if e["event"] in ("fixup-start", "fixup-report")] == ["fixup-start", "fixup-report"]


def test_a_second_reject_fails_the_run_after_exactly_one_fixup_round_and_the_run_branch_does_not_move(tmp_path, capsys, scripted):
    scripted.verdict = "REJECT"
    scripted.verdict_findings = [FINDING_B]
    repo = make_repo(tmp_path, THREE_TICKETS)
    main_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err and "after 1 fix-up round:" in err
    assert len(scripted.fixup_calls) == 1 and len(scripted.verifier_calls) == 2
    assert tip(repo, "run/demo") == main_tip
    events = read_journal(out)
    assert "run-branch-advance" not in event_names(events)
    run_end = next(e for e in events if e["event"] == "run-end")
    assert run_end["result"] == "failed"


def test_each_finding_maps_to_the_ticket_it_names_and_that_ticket_fails_with_the_finding_as_its_reason(tmp_path, capsys, scripted):
    scripted.verdict = "REJECT"
    scripted.verdict_findings = [FINDING_B, "[c] The comma is not quoted."]
    repo = make_repo(tmp_path, THREE_TICKETS)

    code, out, err = run(repo, capsys)

    assert code == 1
    events = read_journal(out)
    final = [e for e in events if e["event"] == "verdict"][-1]
    assert final["mapped"] == {"b": ["The header row is missing."], "c": ["The comma is not quoted."]}
    assert final["unmapped"] == []
    ends = {e["ticket"]: e for e in events if e["event"] == "step-end"}
    assert [ends[t]["state"] for t in "abc"] == ["built", "failed", "failed"]
    assert "The header row is missing." in ends["b"]["reason"] and "The comma" not in ends["b"]["reason"]
    assert "delegate run: ticket b:" in err and "delegate run: ticket c:" in err and "delegate run: ticket a:" not in err
    run_end = next(e for e in events if e["event"] == "run-end")
    assert (run_end["built"], run_end["failed"]) == (["a"], ["b", "c"])


def test_a_finding_with_an_unknown_label_or_no_label_is_recorded_as_unmapped_and_not_dropped(tmp_path, capsys, scripted):
    scripted.verdict = "REJECT"
    scripted.verdict_findings = [FINDING_B, "[zz] A ticket that does not exist.", "The tone is wrong."]
    repo = make_repo(tmp_path, THREE_TICKETS)

    code, out, err = run(repo, capsys)

    assert code == 1
    final = [e for e in read_journal(out) if e["event"] == "verdict"][-1]
    assert final["mapped"] == {"b": ["The header row is missing."]}
    assert final["unmapped"] == ["[zz] A ticket that does not exist.", "The tone is wrong."]
    assert final["findings"] == [FINDING_B, "[zz] A ticket that does not exist.", "The tone is wrong."]
    assert "unmapped finding: [zz] A ticket that does not exist." in err
    assert "unmapped finding: The tone is wrong." in err


def test_when_no_finding_maps_to_a_ticket_every_ticket_of_the_stack_fails(tmp_path, capsys, scripted):
    scripted.verdict = "REJECT"
    scripted.verdict_findings = ["The tone is wrong."]
    repo = make_repo(tmp_path, THREE_TICKETS)

    code, out, err = run(repo, capsys)

    assert code == 1
    run_end = next(e for e in read_journal(out) if e["event"] == "run-end")
    assert (run_end["built"], run_end["failed"]) == ([], ["a", "b", "c"])
    assert "unmapped finding: The tone is wrong." in err


def test_a_stack_that_follows_a_rejected_stack_is_not_verified_and_its_tickets_fail_with_that_reason(tmp_path, capsys, scripted):
    scripted.verdict = "REJECT"
    scripted.verdict_findings = ["[a] The CSV file is empty."]
    repo = make_repo(tmp_path, TWO_STACKS)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert [call.agent for call in scripted.verifier_calls] == ["python-verifier"] * 2  # the first pass and the fix-up pass of one stack
    events = read_journal(out)
    ends = {e["ticket"]: e for e in events if e["event"] == "step-end"}
    assert ends["a"]["state"] == "failed" and ends["b"]["state"] == "failed" and ends["c"]["state"] == "built"
    assert "not verified" in ends["b"]["reason"] and "python" in ends["b"]["reason"]


def test_a_failed_ticket_stays_out_of_the_chain_and_the_built_tickets_are_still_verified(tmp_path, capsys, scripted):
    scripted.failing_tickets = {"b"}
    scripted.files_by_ticket["c"] = {"c.txt": "c\n"}
    repo = make_repo(tmp_path, THREE_TICKETS.replace('blocked-by = ["a"]', "blocked-by = []"))

    code, out, err = run(repo, capsys)

    assert code == 1
    (call,) = scripted.verifier_calls
    assert "Ticket a" in call.prompt and "Ticket c" in call.prompt and "Ticket b" not in call.prompt
    assert tip(repo, "run/demo") == tip(repo, "run/demo-c")
    run_end = next(e for e in read_journal(out) if e["event"] == "run-end")
    assert (run_end["built"], run_end["failed"]) == (["a", "c"], ["b"])


def test_a_resume_after_a_stop_before_the_verifier_verifies_the_chain_and_spawns_no_specialist_again(tmp_path, capsys, scripted):
    from .support import kill_a_run, read_journal_file, resume_main

    # The child process of `kill_a_run` has its own scripted adapter, which makes the file <ticket id>.txt for each ticket.
    repo = make_repo(tmp_path, THREE_TICKETS.replace("test -f feature.txt", "test -f a.txt"))
    run_id, _ = kill_a_run(repo, 4)  # killed as the verifier of the chain starts
    assert tip(repo, "run/demo") == tip(repo, "main")

    code, out, err = resume_main(repo, capsys, run_id, "--break-lock")

    assert (code, err) == (0, "")
    assert scripted.calls == []
    (call,) = scripted.verifier_calls
    assert "Ticket a" in call.prompt and "Ticket b" in call.prompt and "Ticket c" in call.prompt
    assert tip(repo, "run/demo") == tip(repo, "run/demo-c")
    events = read_journal_file(repo, run_id)
    run_end = next(e for e in events if e["event"] == "run-end")
    assert (run_end["result"], run_end["built"]) == ("built", ["a", "b", "c"])
