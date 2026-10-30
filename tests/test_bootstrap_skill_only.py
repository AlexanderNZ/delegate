"""Behaviour tests for `bootstrap --skill-only` and `bootstrap --gate-extra-flag` (issue #208).

A repository whose declaration holds a hand edit needs a command that makes
the context skill current and leaves the declaration alone: `--skill-only`.
`--gate-extra-flag` writes the `gateExtraFlags` key, so the setup check of
`delegate run --dry-run` needs no hand edit. Each case goes through `cli.main`.
"""

import json
import shlex
import shutil
import subprocess
import tomllib

from delegate.cli.agent_definitions import main
from delegate.definitions.validate import split_frontmatter
from tests.test_bootstrap import _agent_files, _args, _blocks, _paths, repo  # noqa: F401  (repo is a fixture)
from tests.test_bootstrap_options import _rerun_argv

SKILL_ONLY_ARGS = ("--skill-only",)


def _skill_only_argv(repo, *extra: str) -> list[str]:
    """The options that the context skill uses, and nothing else."""
    from tests.test_bootstrap import DOMAIN, GATES_REF, OPERATE_REF

    return [
        "bootstrap", "--repo", str(repo), "--name", "acme-api", "--domain", DOMAIN,
        "--reference", GATES_REF, "--reference", OPERATE_REF, "--skill-only", *extra,
    ]


def _tree(repo) -> dict[str, bytes]:
    return {
        str(p.relative_to(repo)): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }


def test_skill_only_makes_a_stale_skill_current_and_leaves_the_declaration_and_the_agents_alone(repo, tmp_path, capsys):
    assert main(_args(repo)) == 0
    p = _paths(repo)
    p["declaration"].write_text(p["declaration"].read_text() + 'gateExtraFlags = ["--noEmit"]\n')
    p["skill"].write_text(p["skill"].read_text() + "\n```!\ncd docs; cat gates.md\n```\n")
    declaration_before = p["declaration"].read_bytes()
    agents_before = {f: f.read_bytes() for f in _agent_files(repo)}
    user_skills = tmp_path / "user-skills"
    user_skills.mkdir()

    assert main(_skill_only_argv(repo, "--user-skills-dir", str(user_skills))) == 0

    assert p["declaration"].read_bytes() == declaration_before
    assert {f: f.read_bytes() for f in _agent_files(repo)} == agents_before
    capsys.readouterr()
    code = main([
        "validate",
        "--claude-code", str(repo / ".claude" / "agents"),
        "--opencode", str(repo / ".opencode" / "agents"),
        "--skills-dir", str(repo / ".claude" / "skills"),
        "--user-skills-dir", str(user_skills),
    ])
    assert code == 0, capsys.readouterr().out
    assert "cd docs; cat gates.md" not in p["skill"].read_text()


def test_skill_only_needs_no_prompt_file_and_no_skill_and_writes_only_the_skill(repo):
    before = _tree(repo)
    assert main(_skill_only_argv(repo)) == 0
    after = _tree(repo)
    skill = str(_paths(repo)["skill"].relative_to(repo))
    assert set(after) - set(before) == {skill}
    assert {k: v for k, v in after.items() if k != skill} == before


def test_skill_only_with_dry_run_prints_the_skill_and_writes_nothing(repo, capsys):
    before = _tree(repo)
    assert main(_skill_only_argv(repo, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "acme-api-context" in out
    assert "agents.toml" not in out
    assert ".claude/agents" not in out
    assert _tree(repo) == before


def test_skill_only_writes_the_same_load_time_blocks_as_a_full_bootstrap(repo):
    assert main(_args(repo)) == 0
    full = _blocks(_paths(repo)["skill"].read_text())
    _paths(repo)["skill"].unlink()
    assert main(_skill_only_argv(repo)) == 0
    assert full and _blocks(_paths(repo)["skill"].read_text()) == full


def test_skill_only_header_command_gives_the_same_skill_again(repo):
    assert main(_skill_only_argv(repo)) == 0
    skill = _paths(repo)["skill"]
    before = skill.read_bytes()
    text = skill.read_text()
    [command] = [line.strip() for line in text.splitlines() if line.strip().startswith("agent-definitions bootstrap")]
    argv = shlex.split(command)[1:]
    argv[argv.index("--repo") + 1] = str(repo)
    assert "--skill-only" in argv
    assert main(argv) == 0
    assert skill.read_bytes() == before


def test_gate_extra_flag_writes_the_key_and_the_verifier_permits_the_flag(repo):
    extra = (
        "--gate-command", "npx vitest run",
        "--gate-extra-flag", "--noEmit",
        "--gate-extra-flag", "--project <value>",
    )
    assert main(_args(repo, extra=extra)) == 0
    p = _paths(repo)
    declaration = tomllib.loads(p["declaration"].read_text())
    assert declaration["agents"]["acme-api"]["gateExtraFlags"] == ["--noEmit", "--project <value>"]
    assert 'gateExtraFlags = ["--noEmit", "--project <value>"]' in p["declaration"].read_text()

    fm, _body, err = split_frontmatter(p["cc-verifier"].read_text())
    assert err is None, err
    [bash] = [entry for entry in fm["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
    hook = bash["hooks"][0]["command"]

    def guard(command: str) -> int:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo)})
        return subprocess.run(
            [shutil.which("bash"), "-c", hook], input=payload, capture_output=True, text=True, cwd=repo,
        ).returncode

    assert guard("npx vitest run --project unit") == 0
    assert guard("npx vitest run --noEmit") == 0
    assert guard("npx vitest run -u") == 2


def test_gate_extra_flag_is_in_the_header_comment_and_the_command_gives_the_same_declaration(repo):
    extra = ("--gate-extra-flag", "--noEmit", "--gate-extra-flag", "--project <value>")
    assert main(_args(repo, extra=extra)) == 0
    p = _paths(repo)
    for label in ("declaration", "skill"):
        text = p[label].read_text()
        assert "--gate-extra-flag --noEmit" in text, label
        assert "--gate-extra-flag '--project <value>'" in text, label
    before = p["declaration"].read_bytes()
    argv = _rerun_argv(p["declaration"].read_text(), repo)
    assert main(argv) == 0
    assert p["declaration"].read_bytes() == before


def test_without_the_option_the_declaration_has_no_gate_extra_flags(repo):
    assert main(_args(repo)) == 0
    assert "gateExtraFlags" not in _paths(repo)["declaration"].read_text()
    assert "--gate-extra-flag" not in _paths(repo)["skill"].read_text()


def test_a_gate_extra_flag_that_is_not_a_flag_exits_one_and_writes_nothing(repo, capsys):
    assert main(_args(repo, extra=("--gate-extra-flag", "fix"))) == 1
    assert "gateExtraFlags" in capsys.readouterr().err
    assert _agent_files(repo) == []
    assert not _paths(repo)["declaration"].exists()


def test_the_load_block_finding_names_skill_only(repo, tmp_path, capsys):
    assert main(_args(repo)) == 0
    skill = _paths(repo)["skill"]
    skill.write_text(skill.read_text() + "\n```!\ncd docs; cat gates.md\n```\n")
    user_skills = tmp_path / "user-skills"
    user_skills.mkdir()
    capsys.readouterr()
    code = main([
        "validate",
        "--claude-code", str(repo / ".claude" / "agents"),
        "--skills-dir", str(repo / ".claude" / "skills"),
        "--user-skills-dir", str(user_skills),
    ])
    out = capsys.readouterr().out
    assert code == 1
    [finding] = [line for line in out.splitlines() if line.startswith("LOAD_BLOCK_NOT_PLAIN ")]
    assert "agent-definitions bootstrap --skill-only" in finding
