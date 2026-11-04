"""The how-to page "Write a workflow for a run": its example passes the dry run, and the quality check is as the page shows.

A coordinator copies the workflow of a page. A workflow on the page that misses a setting, or a quality check on the page
that the dry run does not print, teaches the wrong file (#248). The case writes the workflow of the page into a real
temporary git repository and runs its dry run. The dry run starts the guard probe of each verifier, so the case puts
the Claude Code stand-in on PATH.
"""

from delegate.cli import delegate

from .pages import HOW_TO, blocks, new_repo, outside_the_package, write_files
from .support import write_project_agents

PAGE = HOW_TO / "write-a-workflow-for-a-run.md"


@outside_the_package
def test_the_workflow_of_the_page_passes_the_dry_run_and_its_quality_check_is_the_one_on_the_page(
    tmp_path, home, git_identity, claude_stand_in, capsys
):
    repo = new_repo(tmp_path / "repo")
    assert write_files(PAGE, repo) == ["workflow.toml"]
    write_project_agents(repo)
    shown = next(block.body for block in blocks(PAGE) if block.lang == "text" and block.body.startswith("quality check"))

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run"])

    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    lines = out.splitlines()
    start = lines.index("quality check")
    assert "\n".join(lines[start : start + 7]) == shown.rstrip("\n")
    assert not any(line.startswith("warn ") for line in lines)
    tickets = [line.split()[1] for line in lines if line.startswith("ticket ")]
    assert tickets == ["1", "2", "3"]
    assert "ticket 1 (1/3): stack python, blocked by none, specialist tier strong" in lines
    assert "ticket 2 (2/3): stack python, blocked by 1, specialist tier standard" in lines
