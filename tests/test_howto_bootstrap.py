"""The bootstrap how-to pages: a reader who follows a page word for word gets a pair that validates.

Each case writes the files of the page into a real temporary git repository
and runs the commands of the page.
"""

import subprocess

from delegate import delegate

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


MONOREPO = HOW_TO / "bootstrap-a-monorepo.md"


@outside_the_package
def test_following_the_monorepo_page_gives_one_pair_for_each_stack_and_a_brief_that_carries_only_the_gate_of_the_stack_that_changed(
    tmp_path, monkeypatch, capsys, git_identity
):
    repo = new_repo(tmp_path / "repo")
    write_files(MONOREPO, repo)

    steps = follow(MONOREPO, repo, monkeypatch)

    delegate_steps = [step for step in steps if step.command.startswith("delegate ")]
    assert [step.command.split()[1] for step in delegate_steps] == ["bootstrap", "bootstrap", "validate", "run"]
    assert [(step.code, step.err) for step in delegate_steps] == [(0, "")] * 4
    assert delegate_steps[-1].out.index("ticket 1 ") < delegate_steps[-1].out.index("ticket 2 ")  # the plan follows the blocking edge
    agents = sorted(path.name for path in (repo / ".claude" / "agents").glob("*.md"))
    assert agents == ["backend-specialist.md", "backend-verifier.md", "web-specialist.md", "web-verifier.md"]
    # Each verifier may run the gate of its own stack, and not the gate of the other stack.
    backend, web = (repo / ".claude" / "agents" / f"{name}-verifier.md" for name in ("backend", "web"))
    assert "python3 -m pytest" in backend.read_text() and "npm test" not in backend.read_text()
    assert "npm test" in web.read_text() and "python3 -m pytest" not in web.read_text()
    # The delegation document of the page claims one path for each stack, so the brief for a change in web/ holds one gate.
    (repo / "web").mkdir(exist_ok=True)
    (repo / "web" / "main.ts").write_text("export {};\n")
    for args in (["checkout", "-q", "-b", "web-change"], ["add", "-A"], ["commit", "-q", "-m", "web change"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    assert delegate.main(["brief", "full", "--repo", str(repo), "--branch", "web-change", "--task", "t"]) == 0
    gates = capsys.readouterr().out.rpartition("## Gates")[2]
    assert "npm test" in gates and "python3 -m pytest" not in gates
