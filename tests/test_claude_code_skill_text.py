"""A new Claude Code session gets the text of each skill that its agent lists, before the brief (#215).

Measured on Claude Code 2.1.287: under `claude --print --agent <name>`, the `skills` list of the agent does not preload a
skill. The skill is in the skill list of the session, but its text is not in the context. So the adapter puts the text
of each listed skill in the prompt of a new session, and runs each load-time block of a skill in the working directory
of the session, as the harness does when it loads a skill. Each case seals with the real adapter, from files that the
renderer made, and starts the session with a stand-in `claude` command that records its standard input.
"""

from __future__ import annotations

from pathlib import Path

from delegate import adapters
from delegate.definitions.declaration import Agent
from delegate.definitions.render import render
from delegate.ports.harness import AdapterRequest

from . import claude_fake
from .support import committed_repo, git

BRIEF = "## Task\n\nTicket a: SECRET-BRIEF-TEXT\n"
AGENT = "python-specialist"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def skill(name: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: The skill {name}. Use it in a test.\n---\n\n{body}"


def block(command: str) -> str:
    return f"```!\n{command}\n```\n"


def repository(tmp_path: Path, tiers, skills: list[str]) -> Path:
    """A real git repository with the pair `python` rendered for Claude Code, and one commit."""
    agent = Agent(key="python", description="The python stack.", gate_commands=["python3 -m pytest -rs"], skills=skills)
    files = {f".claude/agents/{name}": content for name, content in render([agent], tiers)["claude-code"].items()}
    return committed_repo(tmp_path / "repo", {**files, "NOTE.md": "NOTE-ON-MAIN\n"}, message="init")


def start(tmp_path: Path, monkeypatch, repo: Path, cwd: Path, **changes) -> str:
    """Seal the specialist of `repo`, start one session in `cwd`, and give the standard input of the harness."""
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    adapter = adapters.get("claude-code")
    sealed = adapter.seal(AGENT, repo, tmp_path / "seal")
    assert sealed is not None
    (tmp_path / "streams").mkdir(exist_ok=True)
    request = AdapterRequest(
        agent=AGENT, model="opus", prompt=BRIEF, cwd=cwd, report_path=tmp_path / "a.specialist.json",
        event_stream=tmp_path / "streams" / "a.jsonl", sealed=sealed, **changes,
    )
    adapter.run(request)
    (call,) = fake.calls()
    return str(call["stdin"])


def test_a_new_session_gets_the_text_of_the_project_skill_and_of_the_user_skill_before_the_brief(tmp_path, monkeypatch, home, tiers):
    repo = repository(tmp_path, tiers, ["python-context", "user-rules"])
    write(repo / ".claude" / "skills" / "python-context" / "SKILL.md", skill("python-context", "The code word is PELICAN-7731.\n"))
    write(home / ".claude" / "skills" / "user-rules" / "SKILL.md", skill("user-rules", "The second code word is HERON-4410.\n"))

    stdin = start(tmp_path, monkeypatch, repo, repo)

    assert "The code word is PELICAN-7731." in stdin
    assert "The second code word is HERON-4410." in stdin
    assert stdin.index("PELICAN-7731") < stdin.index("HERON-4410") < stdin.index("SECRET-BRIEF-TEXT")
    assert stdin.endswith(BRIEF)  # the brief is the last part, with no change
    assert "description: The skill python-context" not in stdin  # the frontmatter is not the text of the skill
    assert f"Base directory for this skill: {tmp_path / 'seal' / 'skills' / 'python-context'}" in stdin
    assert f"Base directory for this skill: {home / '.claude' / 'skills' / 'user-rules'}" in stdin


def test_a_load_time_block_runs_in_the_working_directory_of_the_session_and_its_output_replaces_the_block(tmp_path, monkeypatch, home, tiers):
    repo = repository(tmp_path, tiers, ["python-context"])
    command = "git grep --untracked --no-exclude-standard -h -E -m 200 -e '^' -- :/NOTE.md"
    write(repo / ".claude" / "skills" / "python-context" / "SKILL.md", skill("python-context", f"The note:\n\n{block(command)}\nAfter the note.\n"))
    worktree = tmp_path / "worktree"
    git(repo, "worktree", "add", "-q", "-b", "ticket-a", str(worktree))
    write(worktree / "NOTE.md", "NOTE-ON-THE-BRANCH\n")  # the session reads its own tree, not the main checkout

    stdin = start(tmp_path, monkeypatch, repo, worktree / ".claude")  # a subdirectory: `:/` names the top of the tree

    assert "NOTE-ON-THE-BRANCH" in stdin
    assert "NOTE-ON-MAIN" not in stdin
    assert "```!" not in stdin and command not in stdin
    assert stdin.index("The note:") < stdin.index("NOTE-ON-THE-BRANCH") < stdin.index("After the note.")


def test_an_inline_load_time_command_runs_and_its_output_replaces_it_and_inline_code_stays(tmp_path, monkeypatch, home, tiers):
    repo = repository(tmp_path, tiers, ["python-context"])
    body = "The note is: !`git grep --untracked --no-exclude-standard -h -E -e '^' -- :/NOTE.md` here.\nKeep `git status` as text.\n"
    write(repo / ".claude" / "skills" / "python-context" / "SKILL.md", skill("python-context", body))

    stdin = start(tmp_path, monkeypatch, repo, repo)

    assert "The note is: NOTE-ON-MAIN here." in stdin
    assert "Keep `git status` as text." in stdin


def test_a_block_that_is_not_one_plain_command_does_not_run_and_the_prompt_tells_the_agent_to_run_it(tmp_path, monkeypatch, home, tiers):
    repo = repository(tmp_path, tiers, ["python-context"])
    compound = "touch made-by-the-block; echo DONE"
    write(repo / ".claude" / "skills" / "python-context" / "SKILL.md", skill("python-context", block(compound) + block("ls $HOME") + "Inline: !`touch made-inline; echo DONE`\n"))

    stdin = start(tmp_path, monkeypatch, repo, repo)

    assert not (repo / "made-by-the-block").exists() and not (repo / "made-inline").exists()
    assert compound in stdin and "ls $HOME" in stdin  # the text of each block stays
    assert stdin.count("The engine did not run this load-time block") == 2
    assert "!`touch made-inline; echo DONE` (the engine did not run this load-time command" in stdin


def test_a_block_that_fails_gives_its_exit_status_and_its_error_in_the_place_of_the_block(tmp_path, monkeypatch, home, tiers):
    repo = repository(tmp_path, tiers, ["python-context"])
    write(repo / ".claude" / "skills" / "python-context" / "SKILL.md", skill("python-context", block("git show no-such-revision-215")))

    stdin = start(tmp_path, monkeypatch, repo, repo)

    assert "exited with status 128" in stdin
    assert "no-such-revision-215" in stdin


def test_a_skill_that_the_adapter_cannot_find_is_named_and_the_agent_is_told_to_load_it(tmp_path, monkeypatch, home, tiers):
    repo = repository(tmp_path, tiers, ["python-context", "other-plugin:missing-skill"])
    write(repo / ".claude" / "skills" / "python-context" / "SKILL.md", skill("python-context", "The code word is PELICAN-7731.\n"))

    stdin = start(tmp_path, monkeypatch, repo, repo)

    assert "PELICAN-7731" in stdin
    assert "other-plugin:missing-skill" in stdin.split("SECRET-BRIEF-TEXT")[0]


def test_an_agent_with_no_skill_gets_the_brief_with_no_change(tmp_path, monkeypatch, home, tiers):
    repo = repository(tmp_path, tiers, [])

    assert start(tmp_path, monkeypatch, repo, repo) == BRIEF


def test_a_resumed_session_gets_the_brief_only_because_it_has_the_skills_already(tmp_path, monkeypatch, home, tiers):
    repo = repository(tmp_path, tiers, ["python-context"])
    write(repo / ".claude" / "skills" / "python-context" / "SKILL.md", skill("python-context", "The code word is PELICAN-7731.\n"))

    assert start(tmp_path, monkeypatch, repo, repo, resume_session="af2a3741-3f76-4944-91f5-3d5102c79b6b") == BRIEF
