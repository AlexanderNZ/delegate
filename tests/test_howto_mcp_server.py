"""The how-to page "Give the agents an MCP server": its workflow passes the dry run, and the fields it names are in the reference.

A coordinator copies the workflow of a page. A workflow on the page that the dry run refuses, or a quality check that warns
about the server of the page, teaches the wrong file (#259). The case writes the workflow of the page into a real temporary
git repository and runs its dry run. The page also names the fields of the workflow reference, so a field that the
reference does not hold is a defect of the page.
"""

import re

from delegate.cli import delegate

from .pages import HOW_TO, ROOT, blocks, new_repo, outside_the_package, write_files
from .support import write_project_agents

PAGE = HOW_TO / "give-the-agents-an-mcp-server.md"
REFERENCE = ROOT / "docs" / "reference" / "workflow.md"


@outside_the_package
def test_the_workflow_of_the_page_passes_the_dry_run_and_its_server_has_no_verifier_tools_warning(
    tmp_path, home, git_identity, claude_stand_in, capsys
):
    repo = new_repo(tmp_path / "repo")
    assert write_files(PAGE, repo) == ["workflow.toml", "settings.json"]
    write_project_agents(repo)

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run"])

    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert "ok verifier-tools: stack python: server codegraph: verifier-tools codegraph_explore" in lines
    assert not any(line.startswith("warn ") for line in lines)


@outside_the_package
def test_each_field_that_the_page_names_in_code_is_in_the_workflow_reference():
    page = PAGE.read_text()
    toml = next(block.body for block in blocks(PAGE) if block.path == "workflow.toml")
    fields = set(re.findall(r"^(?:\[stacks\.\w+\.mcp-servers\.\w+\]\n)?([a-z-]+) = ", toml, flags=re.MULTILINE))
    fields |= set(re.findall(r"`(mcp-servers|verifier-tools|prepare|env|command)`", page))
    reference = REFERENCE.read_text()

    assert {"command", "env", "verifier-tools", "prepare", "mcp-servers"} <= fields
    missing = sorted(field for field in fields if f"`{field}`" not in reference)
    assert missing == []


DONTASK_LINKED_TO_THE_MODE_OF_A_RUN = re.compile(r"`dontAsk`\s*\[\*{0,2}[^\]]*\]\([^)]*glossary\.md#mode\)")


@outside_the_package
def test_no_page_links_dontask_to_the_glossary_entry_of_the_mode_of_a_run():
    pages = sorted((ROOT / "docs").rglob("*.md"))
    linked = [
        str(page.relative_to(ROOT))
        for page in pages
        if DONTASK_LINKED_TO_THE_MODE_OF_A_RUN.search(page.read_text())
    ]

    assert pages
    assert linked == []


@outside_the_package
def test_the_page_links_the_permission_mode_entry_that_the_glossary_holds():
    glossary = (ROOT / "docs" / "glossary.md").read_text()

    assert "\n## Permission mode\n" in glossary
    assert "`dontAsk` [**permission mode**](../glossary.md#permission-mode)" in PAGE.read_text()
