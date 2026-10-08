"""The bootstrap how-to pages: a reader who follows a page word for word gets a pair that validates.

Each case writes the files of the page into a real temporary git repository
and runs the commands of the page.
"""

from agent_definitions import delegate

from .pages import HOW_TO, follow, new_repo, outside_the_package, write_files

SINGLE = HOW_TO / "bootstrap-a-single-stack-repository.md"


@outside_the_package
def test_following_the_single_stack_page_gives_one_pair_that_may_run_the_gate_and_a_workflow_that_passes_the_dry_run(
    tmp_path, monkeypatch, git_identity
):
    repo = new_repo(tmp_path / "repo")
    write_files(SINGLE, repo)

    steps = follow(SINGLE, repo, monkeypatch)

    delegate_steps = [step for step in steps if step.command.startswith("delegate ")]
    assert [step.command.split()[1] for step in delegate_steps] == ["bootstrap", "validate", "run"]
    assert [(step.code, step.err) for step in delegate_steps] == [(0, "")] * 3
    assert "ticket 1 " in delegate_steps[-1].out  # the dry run prints the plan
    assert "```bash\npython3 -m pytest -rs\n```" in (repo / "docs" / "agents" / "delegation.md").read_text()
    agents = repo / ".claude" / "agents"
    assert sorted(path.name for path in agents.glob("*.md")) == ["python-specialist.md", "python-verifier.md"]
    assert "python3 -m pytest" in (agents / "python-verifier.md").read_text()  # the gate command of the page
    assert (repo / ".claude" / "skills" / "python-context" / "SKILL.md").is_file()
    assert delegate.main(["validate", "--claude-code", str(agents), "--skills-dir", str(repo / ".claude" / "skills")]) == 0
