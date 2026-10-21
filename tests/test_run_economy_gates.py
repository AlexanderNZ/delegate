"""In `economy` mode the engine runs the gates once, on the tip of the chain, before the verifier of each stack.

A ticket of the chain is built when its report is valid and says committed, and
its branch holds a commit. The engine runs no gate for one ticket. At the end of
the chain, red gates start a fix-up round with the gate output, and green gates
go on to the verifier, whose brief holds the green output. The rounds of red
gates and of a REJECT count together against the fix-up limit of the mode.
`assure` mode runs the gates of each ticket, as before.

Each case hands `run_workflow` a gate runner that records each call and runs the
command in the real shell, on a real temporary git repository. The gate runner
stands at a system boundary: it starts a process. The scripted adapter plays the
specialists and the verifiers.
"""

import pytest

from delegate import adapters
from delegate.adapters.git import GitVersionControl
from delegate.adapters.shell import ShellGateRunner
from delegate.run.engine import run_workflow
from delegate.run.workflow import load_workflow
from delegate.shared.tiers import load_tiers

from .support import git, make_repo, read_journal_file

HEAD = '''\
base-branch = "main"
run-branch = "run/demo"
mode = "{mode}"
adapter = "scripted"
# The cases read the ticket branches after the run, so the run keeps them (#167).
keep-worktrees = true

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = [{gates}]
hotspots = []
'''

FINDING = "[c] The comma is not quoted."


def workflow(gates: list[str], mode: str = "economy", tickets: str = "abc") -> str:
    """A workflow of one stack whose tickets form a chain: each ticket waits for the one before it."""
    text = HEAD.format(mode=mode, gates=", ".join(f"'{gate}'" for gate in gates))
    for position, ticket in enumerate(tickets):
        blockers = f'"{tickets[position - 1]}"' if position else ""
        text += f'\n[[tickets]]\nid = "{ticket}"\ntext = "Ticket {ticket} adds its file."\nstack = "python"\nblocked-by = [{blockers}]\n'
    return text


class SpyGates:
    """A gate runner that records each call: the command, the worktree, and the verifier runs before it."""

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.calls: list[tuple[str, str, int]] = []

    def run(self, command, worktree, log=None):
        self.calls.append((command, worktree.name, len(self.adapter.verifier_calls)))
        return ShellGateRunner().run(command, worktree, log=log)


def start_run(repo, adapter, gates):
    tiers = load_tiers()
    workflow_path = repo / "workflow.toml"
    loaded = load_workflow(workflow_path, tiers, adapters.registered_names())
    return run_workflow(loaded, workflow_path, repo, tiers, adapter, GitVersionControl(), gates)


def tip(repo, ref):
    return git(repo, "rev-parse", ref).strip()


def gates_section(prompt: str) -> str:
    """The text of the `## Gates` section of a brief, up to the next section."""
    return prompt.split("\n## Gates\n", 1)[1].split("\n## ", 1)[0]


def test_a_green_chain_of_three_tickets_runs_each_gate_command_once_on_the_chain_tip_before_the_verifier(tmp_path, scripted):
    commands = ["ls a.txt b.txt c.txt", "test -f c.txt"]
    repo = make_repo(tmp_path, workflow(commands))
    gates = SpyGates(scripted)

    result = start_run(repo, scripted, gates)

    assert result.ok and result.built == ["a", "b", "c"]
    # Each command runs once, in the worktree of the last ticket, and no verifier has run yet.
    assert gates.calls == [(commands[0], "c", 0), (commands[1], "c", 0)]
    assert len(scripted.verifier_calls) == 1
    # The brief of each specialist says that the gates run once, on the tip of the chain, and not after its ticket.
    for call in scripted.calls:
        assert "once, on the tip of the chain" in call.prompt and "after you finish" not in call.prompt
    events = read_journal_file(repo, result.run_id)
    results = [(e["ticket"], e["phase"], e["command"], e["commit"], e["green"]) for e in events if e["event"] == "gate-result"]
    assert results == [("c", "chain", command, tip(repo, "run/demo-c"), True) for command in commands]
    assert tip(repo, "run/demo") == tip(repo, "run/demo-c")


def test_red_gates_at_the_end_of_the_chain_start_a_fixup_round_with_the_gate_output_and_green_gates_go_on_to_the_verifier(
    tmp_path, scripted
):
    # The gate is red until the fix-up specialist adds fixup-1.txt.
    gate = "test -f fixup-1.txt || { echo RED-GATE-4410 fixup-1.txt is missing; exit 1; }"
    repo = make_repo(tmp_path, workflow([gate]))
    gates = SpyGates(scripted)

    result = start_run(repo, scripted, gates)

    assert result.ok and result.built == ["a", "b", "c"]
    (fixup,) = scripted.fixup_calls
    assert fixup.agent == "python-specialist" and fixup.cwd.name == "c"
    assert "RED-GATE-4410 fixup-1.txt is missing" in fixup.prompt and gate in fixup.prompt
    red_commit = tip(repo, "run/demo-c~1")
    assert red_commit in fixup.prompt
    # The gates ran on the red tip, and again on the fix-up commit. Then the full verifier ran, once.
    assert gates.calls == [(gate, "c", 0), (gate, "c", 0)]
    (verifier,) = scripted.verifier_calls
    assert verifier.prompt.startswith("## Task") and "Ticket a" in verifier.prompt and "Ticket c" in verifier.prompt
    assert "+++ b/fixup-1.txt" in verifier.prompt  # the verifier sees the fix-up commit of the gates
    assert git(repo, "show", "--name-only", "--format=", "run/demo-c").split() == ["fixup-1.txt"]
    assert tip(repo, "run/demo") == tip(repo, "run/demo-c")
    events = read_journal_file(repo, result.run_id)
    (start,) = [e for e in events if e["event"] == "fixup-start"]
    assert (start["ticket"], start["round"], start["rejected_commit"], start["trigger"]) == ("c", 1, red_commit, "gates-red")
    assert "RED-GATE-4410" in (repo / ".git" / "delegate" / "runs" / result.run_id / "findings" / "c.fixup-1.md").read_text()


def test_the_brief_of_the_chain_verifier_holds_the_green_gate_output_and_does_not_tell_the_verifier_to_run_the_gates(
    tmp_path, scripted
):
    gate = "echo GREEN-GATE-7731"
    repo = make_repo(tmp_path, workflow([gate]))

    result = start_run(repo, scripted, SpyGates(scripted))

    assert result.ok
    (verifier,) = scripted.verifier_calls
    section = gates_section(verifier.prompt)
    assert gate in section and "GREEN-GATE-7731\n" in section.split(gate, 1)[1]
    assert tip(repo, "run/demo-c") in section and "green" in section
    assert "Do not run these gates again" in section
    assert "the gates that you ran" not in verifier.prompt


def test_the_scoped_verifier_of_the_chain_also_gets_the_green_gate_output_of_the_fixup_commit(tmp_path, scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = [FINDING]
    gate = "echo GREEN-GATE-7731; ls fixup-*.txt 2>/dev/null || true"
    repo = make_repo(tmp_path, workflow([gate]))

    result = start_run(repo, scripted, SpyGates(scripted))

    assert result.ok
    first, scoped = scripted.verifier_calls
    assert scoped.prompt.startswith("## Findings under verification")
    section = gates_section(scoped.prompt)
    assert "fixup-1.txt" in section and tip(repo, "run/demo-c") in section
    assert "Do not run these gates again" in section and "the gates that you ran" not in scoped.prompt


@pytest.mark.parametrize("third", ["reject", "red-gates"])
def test_red_gates_then_a_reject_use_the_two_fixup_rounds_and_a_third_problem_fails_the_chain(tmp_path, scripted, third):
    scripted.verdict = "REJECT"
    scripted.verdict_findings = [FINDING]
    # Red on the tip of the chain, green after the first fix-up. With `red-gates`, red again after the second fix-up.
    gate = "test -f fixup-1.txt" if third == "reject" else "test -f fixup-1.txt && test ! -f fixup-2.txt"
    repo = make_repo(tmp_path, workflow([gate]))
    main_tip = tip(repo, "main")
    gates = SpyGates(scripted)

    result = start_run(repo, scripted, gates)

    assert not result.ok
    assert len(scripted.fixup_calls) == 2
    # The gates of the chain ran three times: on the tip, after the first fix-up, and after the second fix-up.
    assert [worktree for _, worktree, _ in gates.calls] == ["c", "c", "c"]
    assert tip(repo, "run/demo") == main_tip
    events = read_journal_file(repo, result.run_id)
    assert [(e["round"], e["trigger"]) for e in events if e["event"] == "fixup-start"] == [(1, "gates-red"), (2, "reject")]
    assert "run-branch-advance" not in [e["event"] for e in events]
    if third == "reject":
        assert len(scripted.verifier_calls) == 2
        assert result.failed == ["c"]
        assert "after 2 fix-up rounds" in result.failures["c"] and "The comma is not quoted." in result.failures["c"]
    else:
        assert len(scripted.verifier_calls) == 1
        assert sorted(result.failed) == ["a", "b", "c"]
        for ticket in "abc":
            assert "gates red" in result.failures[ticket] and gate in result.failures[ticket]
            assert "after 2 fix-up rounds" in result.failures[ticket]


def test_an_assure_run_runs_the_gates_of_each_ticket_after_its_build_and_its_rebase_before_its_verifier(tmp_path, scripted):
    gate = "ls *.txt"
    repo = make_repo(tmp_path, workflow([gate], mode="assure", tickets="ab"))
    gates = SpyGates(scripted)

    result = start_run(repo, scripted, gates)

    assert result.ok and result.built == ["a", "b"]
    # a: the build (the rebase keeps its commit, so no second run). b waits on a, so it starts from the run branch that
    # holds the work of a: the build, and its rebase keeps its commit too.
    assert gates.calls == [(gate, "a", 0), (gate, "b", 1)]
    events = read_journal_file(repo, result.run_id)
    phases = [(e["ticket"], e["phase"]) for e in events if e["event"] in ("gate-result", "gate-skip")]
    assert phases == [("a", "build"), ("a", "rebase"), ("b", "build"), ("b", "rebase")]
    assert len(scripted.verifier_calls) == 2
    for call in scripted.calls:
        assert "The engine runs these gates after your report" in call.prompt and "tip of the chain" not in call.prompt
