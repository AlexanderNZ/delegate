"""The page for use after `to-spec` and `to-tickets`: ticket files on disk become a workflow that the engine runs.

The scripted adapter stands in for the live harness. The page tells a reader to
save tracker content to disk first, and the engine reads only the files.
"""

import re

import pytest

from agent_definitions import adapters

from .pages import HOW_TO, file_block, follow, new_repo, outside_the_package, write_files
from .support import ScriptedAdapter

PAGE = HOW_TO / "use-the-kit-after-to-spec-and-to-tickets.md"

FILES = {
    ticket: {f"test_{ticket}.py": f"import unittest\n\n\nclass T(unittest.TestCase):\n    def test_{ticket}(self):\n        self.assertTrue(True)\n"}
    for ticket in ("1", "2")
}


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter(files_by_ticket=FILES)
    adapters.register("claude-code", adapter)  # the scripted adapter in place of the live harness
    yield adapter
    adapters.unregister("claude-code")


@outside_the_package
def test_the_ticket_files_of_the_page_reach_the_specialists_in_the_order_of_their_blocking_edges(tmp_path, monkeypatch, git_identity, scripted):
    repo = new_repo(tmp_path / "repo")
    write_files(PAGE, repo)

    steps = follow(PAGE, repo, monkeypatch)

    assert [(step.code, step.err) for step in steps if step.command.startswith("delegate ")] == [(0, ""), (0, "")]  # the dry run, then the run
    assert [call.cwd.name for call in scripted.calls] == ["1", "2"]
    # The text of each ticket file, and not a path, is in the brief of its specialist.
    assert "The export command reads the data set." in scripted.calls[0].prompt
    assert "The export command writes a header row." in scripted.calls[1].prompt


@outside_the_package
def test_the_blocked_by_section_of_each_ticket_file_of_the_page_matches_the_blocked_by_field_of_the_workflow():
    workflow = file_block(PAGE, "workflow.toml").body
    for ticket in ("1", "2"):
        section = file_block(PAGE, f"tickets/{ticket}.md").body.split("## Blocked by")[1]
        in_file = re.findall(r"^- #(\w+)$", section, flags=re.MULTILINE)
        in_workflow = re.search(rf'id = "{ticket}"\n.*?blocked-by = \[(.*?)\]', workflow, flags=re.DOTALL).group(1)
        assert in_file == re.findall(r'"(\w+)"', in_workflow)
    assert re.findall(r"^- #(\w+)$", file_block(PAGE, "tickets/2.md").body.split("## Blocked by")[1], flags=re.MULTILINE) == ["1"]
