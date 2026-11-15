"""The helpers of the tests of the Claude Code adapter: the seal of an agent file (#354)."""

from __future__ import annotations

from pathlib import Path

from delegate import adapters
from delegate.definitions.declaration import Agent
from delegate.definitions.render import render


GATE = "python3 -m pytest -rs"


def write_agents(root: Path, tiers, skills: list[str] | None = None) -> Path:
    """Render the pair `python` for Claude Code into `root/.claude/agents`, and give that directory."""
    target = root / ".claude" / "agents"
    target.mkdir(parents=True, exist_ok=True)
    agent = Agent(key="python", description="The python stack.", gate_commands=[GATE], skills=skills or [])
    for name, content in render([agent], tiers)["claude-code"].items():
        (target / name).write_text(content)
    return target


def seal(repo: Path, into: Path, agent: str = "python-verifier"):
    sealed = adapters.get("claude-code").seal(agent, repo, into)
    assert sealed is not None
    return sealed
