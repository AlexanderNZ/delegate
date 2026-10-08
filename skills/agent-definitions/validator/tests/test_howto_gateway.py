"""The gateway how-to page: a reader who writes the tier file of the page sees the engine ask the harness for the models in it.

The scripted adapter stands in for the live harness. The model that it receives
is the model that the live harness would receive on its command line.
"""

import pytest

from agent_definitions import adapters, delegate

from .pages import HOW_TO, follow, new_repo, outside_the_package, write_files
from .support import ScriptedAdapter, git

PAGE = HOW_TO / "point-the-tiers-at-a-gateway.md"


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter(files={"test_feature.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_feature(self):\n        self.assertTrue(True)\n"})
    adapters.register("claude-code", adapter)  # the scripted adapter in place of the live harness
    yield adapter
    adapters.unregister("claude-code")


def a_repo(tmp_path):
    repo = new_repo(tmp_path / "repo")
    write_files(PAGE, repo)
    (repo / "seed.txt").write_text("seed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


@outside_the_package
def test_the_engine_asks_for_the_gateway_model_of_the_tier_file_of_the_page_for_the_specialist_and_for_the_verifier(
    tmp_path, monkeypatch, git_identity, scripted
):
    repo = a_repo(tmp_path)

    steps = follow(PAGE, repo, monkeypatch)

    assert [(step.code, step.err) for step in steps] == [(0, "")] * len(steps)
    assert [call.model for call in scripted.calls] == ["gateway/large-model"]  # the mode assure builds on the tier strong
    assert [call.model for call in scripted.verifier_calls] == ["gateway/verifier-model"]
