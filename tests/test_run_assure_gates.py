"""In `assure` mode the verifier brief holds the green gate output of the engine, as in a chain (#157).

The engine runs the gates of a ticket after its build, and again after the
rebase onto the run branch. The verifier reviews the rebased tree, so its brief
gives the output of the green gates on that commit, and tells the verifier not
to run them again. A fix-up round does the same with the gates on the fix-up
commit.

Each case hands `run_workflow` the real shell gate runner, on a real temporary
git repository. The scripted adapter plays the specialists and the verifiers.
"""

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
mode = "assure"
adapter = "scripted"

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = ['{gate}']
hotspots = []
'''

FINDING = "FINDING-6093 the header row is missing"
OWN_GATES_RULE = "the gates that you ran"


def workflow(gate: str, tickets: str) -> str:
    """A workflow of one stack in `assure` mode. No ticket waits for another, so each starts from the base branch."""
    text = HEAD.format(gate=gate)
    for ticket in tickets:
        text += f'\n[[tickets]]\nid = "{ticket}"\ntext = "Ticket {ticket} adds its file."\nstack = "python"\nblocked-by = []\n'
    return text


def start_run(repo, adapter):
    tiers = load_tiers()
    workflow_path = repo / "workflow.toml"
    loaded = load_workflow(workflow_path, tiers, adapters.registered_names())
    return run_workflow(loaded, workflow_path, repo, tiers, adapter, GitVersionControl(), ShellGateRunner())


def tip(repo, ref):
    return git(repo, "rev-parse", ref).strip()


def gates_section(prompt: str) -> str:
    """The text of the `## Gates` section of a brief, up to the next section."""
    return prompt.split("\n## Gates\n", 1)[1].split("\n## ", 1)[0]


def test_the_verifier_brief_holds_the_green_gate_output_of_the_engine_for_the_rebased_commit(tmp_path, scripted):
    gate = "echo GREEN-GATE-2817; ls a.txt b.txt 2>/dev/null || true"
    repo = make_repo(tmp_path, workflow(gate, "ab"))

    result = start_run(repo, scripted)

    assert result.ok and result.built == ["a", "b"]
    events = read_journal_file(repo, result.run_id)
    (rebase_b,) = [e for e in events if e["event"] == "rebase" and e["ticket"] == "b"]
    # b started from the base branch, and the rebase onto the run branch, which holds a, made a new commit.
    rebased = tip(repo, "run/demo-b")
    assert rebase_b["to_commit"] == rebased != rebase_b["from_commit"]
    first, second = scripted.verifier_calls
    section = gates_section(second.prompt)
    assert gate in section
    output = section.split(gate, 1)[1]
    # The output is the output on the rebased tree: it holds a.txt, which the build of b did not have.
    assert "GREEN-GATE-2817\n" in output and "a.txt\nb.txt" in output
    assert f"`{rebased}`" in section and rebase_b["from_commit"] not in section
    assert "Do not run these gates again" in section
    assert OWN_GATES_RULE not in second.prompt
    assert "`gate_output` holds the gate output that the brief gives" in second.prompt
    # a: the rebase kept its commit, so the brief holds the output of the green build gates on that commit.
    section = gates_section(first.prompt)
    assert f"`{tip(repo, 'run/demo-a')}`" in section and "GREEN-GATE-2817\n" in section
    assert "Do not run these gates again" in section and OWN_GATES_RULE not in first.prompt


def test_the_fixup_verifier_brief_holds_the_green_gate_output_of_the_engine_for_the_fixup_commit(tmp_path, scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = [FINDING]
    gate = "echo GREEN-GATE-2817; ls fixup-*.txt 2>/dev/null || true"
    repo = make_repo(tmp_path, workflow(gate, "a"))

    result = start_run(repo, scripted)

    assert result.ok
    first, scoped = scripted.verifier_calls
    assert scoped.prompt.startswith("## Findings under verification")
    fixup = tip(repo, "run/demo-a")
    rejected = tip(repo, "run/demo-a~1")
    section = gates_section(scoped.prompt)
    assert gate in section
    # The output is the output on the fix-up commit: it holds fixup-1.txt, which the rejected commit did not have.
    assert "GREEN-GATE-2817\nfixup-1.txt" in section.split(gate, 1)[1]
    assert f"`{fixup}`" in section and rejected not in section
    assert "Do not run these gates again" in section
    assert OWN_GATES_RULE not in scoped.prompt
    assert "`gate_output` holds the gate output that the brief gives" in scoped.prompt
