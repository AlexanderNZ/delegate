"""The brief of a specialist tells it not to run the gates that the engine runs after its report.

The engine runs the gates of the stack after the report, and their result
decides the step. A specialist that runs them too runs the full suite twice for
each ticket. So the `## Gates` section of each specialist brief (the first
brief, the fix-up brief, and the continuation brief) names each gate command and
tells the specialist not to run one. The `## Report` section says what
`gates_green` means: the tests that the specialist ran pass.

Each case writes a workflow into a real temporary git repository, registers a
scripted adapter by name, and calls `delegate.main`. The scripted adapter plays
the specialist, the fix-up specialist and the verifier, and records each prompt.
"""

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import WORKFLOW, ScriptedAdapter, make_repo

GATES = ["test -f feature.txt", "grep -q feature feature.txt"]
TWO_GATES = WORKFLOW.replace('gates = ["test -f feature.txt"]', "gates = [" + ", ".join(f"'{g}'" for g in GATES) + "]")

RULE = "Do not run a gate command yourself. Run only the tests of the code that you change."
DECIDES = "their result decides"
GATES_GREEN = "`gates_green` states whether the tests that you ran pass."
ENGINE_DECIDES = "The engine does not trust this field: the result of the gates that the engine runs decides."


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys, *extra):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def section(prompt: str, title: str) -> str:
    """The text of the first section `title` of a brief, up to the next section."""
    assert f"\n{title}\n" in "\n" + prompt, f"the brief has no {title} section:\n{prompt}"
    return ("\n" + prompt).split(f"\n{title}\n", 1)[1].split("\n## ", 1)[0]


def first_brief(scripted):
    (call,) = scripted.calls
    return call.prompt


def fixup_brief(scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = ["The header row is missing."]
    return lambda: scripted.fixup_calls[0].prompt


def continuation_brief(scripted):
    scripted.outcomes = [("capped", 0), ("finished", 0)]
    return lambda: scripted.continuation_calls[0].prompt


def resumed_continuation_brief(scripted):
    scripted.supports_resume = True
    scripted.session_ids = ["sess-first", "sess-second"]
    scripted.outcomes = [("capped", 0), ("finished", 0)]
    return lambda: scripted.continuation_calls[0].prompt


SETUPS = {
    "specialist": lambda scripted: lambda: first_brief(scripted),
    "fix-up": fixup_brief,
    "continuation": continuation_brief,
    "resumed continuation": resumed_continuation_brief,
}


@pytest.mark.parametrize("kind", SETUPS)
def test_each_specialist_brief_names_each_gate_command_and_tells_the_specialist_not_to_run_it(tmp_path, capsys, scripted, kind):
    prompt_of = SETUPS[kind](scripted)
    repo = make_repo(tmp_path, TWO_GATES)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    gates = section(prompt_of(), "## Gates")
    for gate in GATES:
        assert gate in gates
    assert "The engine runs these gates after your report, and " + DECIDES + " the step." in gates
    assert RULE in gates


@pytest.mark.parametrize("kind", ["specialist", "fix-up", "continuation"])
def test_the_report_section_of_a_specialist_brief_says_that_gates_green_means_the_tests_that_the_specialist_ran(
    tmp_path, capsys, scripted, kind
):
    prompt_of = SETUPS[kind](scripted)
    repo = make_repo(tmp_path, TWO_GATES)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    report = section(prompt_of(), "## Report")
    assert GATES_GREEN in report and ENGINE_DECIDES in report
    assert "whether the gates passed in your run" not in report


def test_an_economy_specialist_brief_tells_the_specialist_not_to_run_the_gates_of_the_chain(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, TWO_GATES.replace('mode = "assure"', 'mode = "economy"'))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    gates = section(first_brief(scripted), "## Gates")
    for gate in GATES:
        assert gate in gates
    assert "once, on the tip of the chain" in gates and DECIDES in gates
    assert RULE in gates
