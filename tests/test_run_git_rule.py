"""Each brief tells the agent to run `git` in its tree with no `cd` (#226).

A specialist ran `cd <worktree> && git status --short` in two sessions. Claude
Code refuses a `cd` followed by `git` with its own approval check, so in
`dontAsk` mode the call failed. A note in the repository did not stop it, so
the brief that the engine writes holds the rule.

Each case runs `delegate.main` against a real temporary git repository. The
scripted adapter plays each agent and records the brief that it gets.
"""

import pytest

from delegate import adapters

from .support import ScriptedAdapter
from .test_run_report_rule import SCENARIOS, run_from_above

# A resumed session keeps the context of its first brief, and that brief holds the rule.
BRIEFS = [scenario for scenario in SCENARIOS if scenario != "resumed continuation"]
VERIFIERS = {"full verifier", "fix-up verifier"}


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def commands_section(prompt: str) -> str:
    """The text of the `## Commands` section of a brief."""
    assert prompt.count("## Commands\n") == 1, "the brief does not hold one section of command rules"
    return prompt.split("## Commands\n", 1)[1].split("\n## ", 1)[0]


@pytest.mark.parametrize("scenario", BRIEFS)
def test_the_brief_tells_the_agent_to_run_git_in_its_tree_with_no_cd(tmp_path, monkeypatch, capsys, scripted, scenario):
    request_of = SCENARIOS[scenario](scripted)

    run_from_above(tmp_path, monkeypatch, capsys)

    request = request_of()
    tree = "copy" if scenario in VERIFIERS else "worktree"
    rules = commands_section(request.prompt)
    assert f"The session starts in the {tree}. Run `git` there with no `cd`" in rules, f"the {scenario} brief has no git rule"
    assert "never join `cd` and `git` in one command" in rules
    assert f"Use `git -C <path>` only for a path outside the {tree}." in rules
    # The session starts in the tree that the rule names.
    assert request.cwd.name == ("copy" if scenario in VERIFIERS else "a")
    assert request.prompt.count("never join `cd` and `git`") == 1, f"the {scenario} brief does not hold the git rule once"
