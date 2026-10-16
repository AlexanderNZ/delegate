"""Each brief tells the agent to run every command in the foreground.

A specialist used background tasks and a monitor to wait for a test command.
Under a headless harness the notifications never reached it, so it polled with
sleep loops and used most of its turns. Its expired monitors then kept the
session alive, and the engine waited.

Each case runs `delegate.main` against a real temporary git repository. The
scripted adapter plays each agent and records the brief that it gets.
"""

import pytest

from delegate import adapters

from .support import ScriptedAdapter
from .test_run_report_rule import SCENARIOS, run_from_above

FOREGROUND_RULE = "Run every command in the foreground."
# A resumed session keeps the context of its first brief, and that brief holds the rule.
BRIEFS = [scenario for scenario in SCENARIOS if scenario != "resumed continuation"]


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


@pytest.mark.parametrize("scenario", BRIEFS)
def test_the_brief_tells_the_agent_to_run_every_command_in_the_foreground(tmp_path, monkeypatch, capsys, scripted, scenario):
    request_of = SCENARIOS[scenario](scripted)

    run_from_above(tmp_path, monkeypatch, capsys)

    prompt = request_of().prompt
    assert prompt.count(FOREGROUND_RULE) == 1, f"the {scenario} brief does not hold the foreground rule once"
    rule = prompt[prompt.index(FOREGROUND_RULE):].split("\n\n", 1)[0]
    assert "background" in rule and "monitor" in rule
