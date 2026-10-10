"""Behaviour tests for `agent-definitions bootstrap`.

Every case goes through `cli.main`, the seam a user reaches. The expected
values come from the specification of the command: the file set, the
200-line boundary, the exit codes, and the rule that a failure writes no
agent file.
"""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from delegate.cli.agent_definitions import main

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


@pytest.fixture
def repo(tmp_path):
    """A git repository with two reference documents and two library skills."""
    r = tmp_path / "acme-api"
    (r / "docs").mkdir(parents=True)
    (r / "docs" / "gates.md").write_text(SHORT_DOC)
    (r / "docs" / "operate.md").write_text(LONG_DOC)
    for s in ("tdd", "test-quality"):
        d = r / ".claude" / "skills" / s
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {s}\ndescription: The {s} skill.\n---\n")
    (r / "prompt.md").write_text("Prove every change with the suite. Never push.\n")
    _git(r, "init", "-q")
    return r


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


# --- criterion 1: the file set, and validate says ok --------------------------


def test_bootstrap_writes_the_six_files_and_validate_prints_ok(repo, capsys):
    assert main(_args(repo)) == 0
    for label, p in _paths(repo).items():
        assert p.is_file(), f"{label} was not written to {p}"
    capsys.readouterr()
    code = main(
        [
            "validate",
            "--claude-code",
            str(repo / ".claude" / "agents"),
            "--opencode",
            str(repo / ".opencode" / "agents"),
            "--skills-dir",
            str(repo / ".claude" / "skills"),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.strip().endswith("ok")


def test_context_skill_frontmatter_carries_the_name_and_the_domain(repo):
    assert main(_args(repo)) == 0
    text = _paths(repo)["skill"].read_text()
    assert text.startswith("---\n"), "Claude Code reads frontmatter from line 1 only"
    fm = yaml.safe_load(text.split("---\n")[1])
    assert fm["name"] == "acme-api-context"
    # The domain sentence opens the description, and a trigger clause follows
    # it: a description with no "use when" is a skill the harness never reaches
    # by discovery.
    assert fm["description"].startswith(DOMAIN)
    assert "Use when" in fm["description"]
    assert "docs/gates.md" in fm["description"] and "docs/operate.md" in fm["description"]
    assert "agent-definitions bootstrap" in text, "the file must say it is generated"


def test_declaration_keeps_the_reference_note_and_the_declared_order(repo):
    assert main(_args(repo)) == 0
    toml = _paths(repo)["declaration"].read_text()
    assert GATES_REF in toml and OPERATE_REF in toml
    assert 'skills = ["acme-api-context", "tdd", "test-quality"]' in toml
    assert "maxTurns = 60" in toml
    body = _paths(repo)["cc-specialist"].read_text()
    assert f"- {OPERATE_REF}" in body, "the rendered specialist lists the reference note"


# --- criterion 2: a second run changes no byte --------------------------------


def test_second_run_with_the_same_arguments_changes_no_byte(repo):
    assert main(_args(repo)) == 0
    first = {k: p.read_bytes() for k, p in _paths(repo).items()}
    assert main(_args(repo)) == 0
    second = {k: p.read_bytes() for k, p in _paths(repo).items()}
    assert first == second


# --- criterion 3: three failures, each exit 1 and no agent file ---------------


def test_a_missing_reference_path_exits_one_and_writes_no_agent_file(repo, capsys):
    code = main(_args(repo, references=(GATES_REF, "`docs/absent.md` — a document that is not here")))
    err = capsys.readouterr().err
    assert code == 1
    assert "docs/absent.md" in err
    assert _agent_files(repo) == []
    assert not _paths(repo)["skill"].exists(), "an argument error writes nothing"


def test_a_reference_that_is_a_directory_exits_one_and_writes_no_agent_file(repo, capsys):
    # A load-time block reads a file. A directory is not a document, so the
    # command names it and stops, with no traceback.
    (repo / "docs" / "research").mkdir()
    code = main(_args(repo, references=(GATES_REF, "`docs/research` — the notes")))
    err = capsys.readouterr().err
    assert code == 1
    assert "docs/research" in err and "not a file" in err, err
    assert _agent_files(repo) == []


def test_a_skill_absent_from_the_skills_root_exits_one_and_writes_no_agent_file(repo, capsys):
    code = main(_args(repo, skills=("tdd", "ghost-skill")))
    out = capsys.readouterr().out
    assert code == 1
    assert "MISSING_SKILL" in out and "ghost-skill" in out
    assert _agent_files(repo) == []


def test_a_declaration_that_fails_validation_exits_one_and_writes_no_agent_file(repo, capsys):
    # A dot is legal in a directory name and illegal in an agent name, so the
    # skill is found and the rendered pair fails NAME_INVALID.
    code = main(_args(repo, name="acme.api"))
    out = capsys.readouterr().out
    assert code == 1
    assert "NAME_INVALID" in out
    assert _agent_files(repo) == []


# --- criterion 4: --dry-run writes nothing ------------------------------------


def test_dry_run_prints_every_file_with_content_and_writes_nothing(repo, capsys):
    assert main(_args(repo, extra=("--dry-run",))) == 0
    out = capsys.readouterr().out
    p = _paths(repo)
    for path in p.values():
        assert str(path) in out, f"{path} is not in the dry-run output"
    assert DOMAIN in out, "the context skill content is not in the dry-run output"
    assert "You are the acme-api specialist." in out, "the agent content is not in the output"
    for path in p.values():
        assert not path.exists(), f"--dry-run wrote {path}"


# --- criterion 5: the live-read blocks run under bash -------------------------


def test_every_live_read_block_is_one_plain_command(repo):
    # Claude Code runs a load-time block only when it can analyse the command.
    # A block with $(...), ;, if or a pipe stops a subagent from starting in the
    # manual, acceptEdits and auto permission modes (measured on Claude Code
    # 2.1.283, 2026-09-26). So each block is one line with no shell syntax.
    assert main(_args(repo)) == 0
    blocks = _blocks(_paths(repo)["skill"].read_text())
    assert len(blocks) == 2
    for block in blocks:
        command = block.strip()
        assert "\n" not in command, f"a block holds more than one line: {block!r}"
        assert not re.search(r"[$;|&`<>()]", command), f"a block holds shell syntax: {command!r}"
        assert not re.search(r"\b(if|then|else|fi|for|while|do|done)\b", command), command
        # An assignment prefix (R=x cmd) is not one plain command either.
        assert "=" not in command.split()[0], f"a block opens with an assignment: {command!r}"


def test_live_read_block_prints_a_reference_of_200_lines_in_full(repo):
    assert main(_args(repo)) == 0
    block = _blocks(_paths(repo)["skill"].read_text())[0]
    r = subprocess.run(["bash", "-c", block], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "gate line 199" in r.stdout, r.stdout[-400:]
    assert r.stdout.startswith("# Gates")


def test_live_read_block_prints_the_heading_index_for_a_reference_of_201_lines(repo):
    assert main(_args(repo)) == 0
    text = _paths(repo)["skill"].read_text()
    block = _blocks(text)[1]
    r = subprocess.run(["bash", "-c", block], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "## Runbooks" in r.stdout, r.stdout[:400]
    assert "operate line 42" not in r.stdout, "a long document must not be printed in full"
    assert "sed -n '<start>,<end>p' docs/operate.md" in text, "the note must give the section command"


# --- Gate commands (ADR 0004) --------------------------------------------------


def test_gate_commands_reach_the_declaration_and_the_verifier(repo):
    extra = ("--gate-command", "npm test", "--gate-command", "npx vitest")
    assert main(_args(repo, extra=extra)) == 0
    p = _paths(repo)
    declaration = p["declaration"].read_text()
    assert 'gateCommands = ["npm test", "npx vitest"]' in declaration
    assert "--gate-command 'npm test'" in declaration, "the rerun command must carry the flag"
    assert '"npm test"' in p["cc-verifier"].read_text()
    assert "npm test *" in p["oc-verifier"].read_text()
    assert '"npm test"' not in p["cc-specialist"].read_text()


def test_a_gate_command_with_shell_syntax_exits_one_and_writes_no_agent_file(repo, capsys):
    assert main(_args(repo, extra=("--gate-command", "npm test; git push"))) == 1
    assert "gateCommands" in capsys.readouterr().err
    assert _agent_files(repo) == []
