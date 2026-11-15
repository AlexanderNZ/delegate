"""The helpers of the tests of `agent-definitions bootstrap`: the fake repository, the argument list and the reference blocks (#354)."""

from __future__ import annotations

from pathlib import Path
import pytest
import re
import shlex
import subprocess

from .support import TEMPLATE_GIT, copy_of


DOMAIN = "acme-api is a Spring Boot service: controllers, JPA repositories, and Flyway migrations."


# Exactly 200 lines. A document of at most 200 lines is printed in full.
SHORT_DOC = "# Gates\n" + "".join(f"gate line {i}\n" for i in range(1, 200))


# Exactly 201 lines. One line over the boundary, so the block prints the index.
LONG_DOC = (
    "# Operate\n" + "".join(f"operate line {i}\n" for i in range(1, 200)) + "## Runbooks\n"
)


GATES_REF = "`docs/gates.md` — the gates and the hotspots"


OPERATE_REF = "`docs/operate.md` — the component map and the runbooks"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _build_acme_api(r: Path) -> None:
    (r / "docs").mkdir(parents=True)
    (r / "docs" / "gates.md").write_text(SHORT_DOC)
    (r / "docs" / "operate.md").write_text(LONG_DOC)
    for s in ("tdd", "test-quality"):
        d = r / ".claude" / "skills" / s
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {s}\ndescription: The {s} skill.\n---\n")
    (r / "prompt.md").write_text("Prove every change with the suite. Never push.\n")
    _git(r, *TEMPLATE_GIT, "init", "-q")


@pytest.fixture
def repo(tmp_path):
    """A git repository with two reference documents and two library skills. The process builds it once (see `copy_of`)."""
    return copy_of(tmp_path / "acme-api", ("bootstrap-acme-api",), _build_acme_api)


def _args(
    repo: Path,
    name: str = "acme-api",
    skills: tuple[str, ...] = ("tdd", "test-quality"),
    references: tuple[str, ...] = (GATES_REF, OPERATE_REF),
    extra: tuple[str, ...] = (),
) -> list[str]:
    argv = [
        "bootstrap",
        "--repo", str(repo),
        "--name", name,
        "--domain", DOMAIN,
        "--tier", "standard",
        "--prompt-file", str(repo / "prompt.md"),
        "--max-turns", "60",
    ]
    for s in skills:
        argv += ["--skill", s]
    for r in references:
        argv += ["--reference", r]
    return argv + list(extra)


def _paths(repo: Path, name: str = "acme-api", skills_root: str = ".claude/skills") -> dict[str, Path]:
    root = repo / skills_root / f"{name}-context"
    return {
        "skill": root / "SKILL.md",
        "declaration": root / "agents.toml",
        "cc-specialist": repo / ".claude" / "agents" / f"{name}-specialist.md",
        "cc-verifier": repo / ".claude" / "agents" / f"{name}-verifier.md",
        "oc-specialist": repo / ".opencode" / "agents" / f"{name}-specialist.md",
        "oc-verifier": repo / ".opencode" / "agents" / f"{name}-verifier.md",
    }


def _agent_files(repo: Path) -> list[Path]:
    return sorted((repo / ".claude" / "agents").glob("*.md")) + sorted(
        (repo / ".opencode" / "agents").glob("*.md")
    )


def _blocks(skill_text: str) -> list[str]:
    """The bodies of the ``!`` fenced blocks Claude Code runs at load time."""
    return re.findall(r"^```!\n(.*?)^```$", skill_text, flags=re.M | re.S)


def _rerun_argv(declaration_text: str, repo) -> list[str]:
    """The re-run command from the comment at the top of a generated file."""
    lines = []
    for line in declaration_text.splitlines():
        if line.startswith("#   agent-definitions "):
            lines.append(line.removeprefix("#   "))
            break
    [command] = lines
    argv = shlex.split(command)[1:]  # drop the program name
    assert argv[argv.index("--repo") + 1] == "."
    argv[argv.index("--repo") + 1] = str(repo)
    return argv
