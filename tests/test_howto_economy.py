"""The economy how-to page: a reader who follows it builds a chain, verifies it once, and merges the run branch.

The scripted adapter stands in for the live harness. It is the adapter
interface that the page itself describes.
"""

import subprocess

import pytest

from agent_definitions import adapters

from .pages import HOW_TO, follow, new_repo, outside_the_package, write_files
from .support import ScriptedAdapter, git

PAGE = HOW_TO / "run-an-economy-chain.md"

# What the scripted specialists commit. The gate of the page is `python3 -m unittest`.
TESTS = {
    ticket: {f"test_{name}.py": f"import unittest\n\n\nclass T(unittest.TestCase):\n    def test_{name}(self):\n        self.assertTrue(True)\n"}
    for ticket, name in (("1", "read"), ("2", "header"), ("3", "quote"))
}


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter(files_by_ticket=TESTS)
    adapters.register("claude-code", adapter)  # the scripted adapter in place of the live harness
    yield adapter
    adapters.unregister("claude-code")


@outside_the_package
def test_following_the_economy_page_builds_a_chain_from_one_ticket_to_the_next_and_verifies_it_once(
    tmp_path, monkeypatch, git_identity, scripted
):
    repo = new_repo(tmp_path / "repo")
    write_files(PAGE, repo)
    (repo / "seed.txt").write_text("seed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")

    steps = follow(PAGE, repo, monkeypatch)

    delegate_steps = {step.command.split()[1]: step for step in steps if step.command.startswith("delegate ")}
    assert set(delegate_steps) == {"run"} | {"status"}
    assert all(step.code == 0 and step.err == "" for step in delegate_steps.values())
    assert [call.cwd.name for call in scripted.calls] == ["1", "2", "3"]  # one specialist for each ticket, in chain order
    assert len(scripted.verifier_calls) == 1  # one verifier for the one stack, at the end of the chain
    for text in ("reads the data set", "writes a header row", "quotes a comma"):
        assert text in scripted.verifier_calls[0].prompt
    # Each ticket branch starts from the previous ticket.
    assert subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", "run/chain-1", "run/chain-2"]).returncode == 0
    assert subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", "run/chain-2", "run/chain-3"]).returncode == 0
    for ticket in "123":
        assert f"ticket {ticket}: state built, verdict ACCEPT" in delegate_steps["status"].out
    # The last step of the page merged the run branch into main.
    assert {"test_read.py", "test_header.py", "test_quote.py"} <= set(git(repo, "ls-tree", "-r", "--name-only", "main").split())
