"""Behaviour tests for the default of `--user-skills-dir` (issue #71).

`agent-definitions validate` and `bootstrap` document that `--user-skills-dir`
defaults to `~/.claude/skills`. Each case sets a temporary home directory, so
the skills of the host never reach the test, and goes through `cli.main`.
"""

import pytest

from delegate.cli.agent_definitions import main
from tests.test_bootstrap import _agent_files, _args, _paths, repo  # noqa: F401  (repo is a fixture)

# The rendered java-spring agents preload these skills.
JAVA_SPRING_SKILLS = ("clean-ddd-hexagonal", "api-design-principles", "tdd", "test-quality", "oauth2-resource-server")


def _skill(root, name):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: The {name} skill.\n---\n")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temporary home directory with an empty `~/.claude/skills`."""
    h = tmp_path / "home"
    (h / ".claude" / "skills").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def project_skills(tmp_path):
    """A project skills directory with every java-spring skill except test-quality."""
    p = tmp_path / "project-skills"
    for s in JAVA_SPRING_SKILLS:
        if s != "test-quality":
            _skill(p, s)
    return p


def _validate_argv(rendered, project_skills, *extra):
    return ["validate", "--claude-code", str(rendered / "claude-code"), "--skills-dir", str(project_skills), *extra]


def test_validate_finds_a_skill_in_the_home_skills_dir_with_no_option(rendered, home, project_skills, capsys):
    _skill(home / ".claude" / "skills", "test-quality")
    code = main(_validate_argv(rendered, project_skills))
    out = capsys.readouterr().out
    assert code == 0, out
    assert "MISSING_SKILL" not in out
    assert out.strip().endswith("ok")


def test_validate_names_the_home_skills_dir_when_a_skill_is_in_neither_dir(rendered, home, project_skills, capsys):
    code = main(_validate_argv(rendered, project_skills))
    out = capsys.readouterr().out
    assert code == 1
    missing = [line for line in out.splitlines() if "MISSING_SKILL" in line and "test-quality" in line]
    assert missing, out
    assert str(home / ".claude" / "skills") in missing[0]


def test_validate_explicit_user_skills_dir_wins_over_the_home_default(rendered, home, project_skills, tmp_path, capsys):
    _skill(home / ".claude" / "skills", "test-quality")
    explicit = tmp_path / "explicit-skills"
    explicit.mkdir()
    code = main(_validate_argv(rendered, project_skills, "--user-skills-dir", str(explicit)))
    out = capsys.readouterr().out
    assert code == 1, out
    missing = [line for line in out.splitlines() if "MISSING_SKILL" in line and "test-quality" in line]
    assert missing, out
    assert str(explicit) in missing[0]
    assert str(home) not in missing[0]


def test_validate_explicit_user_skills_dir_finds_a_skill_absent_from_home(rendered, home, project_skills, tmp_path, capsys):
    explicit = tmp_path / "explicit-skills"
    _skill(explicit, "test-quality")
    code = main(_validate_argv(rendered, project_skills, "--user-skills-dir", str(explicit)))
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.strip().endswith("ok")


def test_bootstrap_finds_a_library_skill_in_the_home_skills_dir_with_no_option(repo, home, capsys):
    _skill(home / ".claude" / "skills", "home-only-skill")
    code = main(_args(repo, skills=("tdd", "home-only-skill")))
    out = capsys.readouterr().out
    assert code == 0, out
    for label, p in _paths(repo).items():
        assert p.is_file(), f"{label} was not written to {p}"
    assert "home-only-skill" in _paths(repo)["cc-specialist"].read_text()


def test_bootstrap_explicit_user_skills_dir_wins_over_the_home_default(repo, home, tmp_path, capsys):
    _skill(home / ".claude" / "skills", "home-only-skill")
    explicit = tmp_path / "explicit-skills"
    explicit.mkdir()
    code = main(_args(repo, skills=("tdd", "home-only-skill"), extra=("--user-skills-dir", str(explicit))))
    out = capsys.readouterr().out
    assert code == 1
    missing = [line for line in out.splitlines() if "MISSING_SKILL" in line and "home-only-skill" in line]
    assert missing, out
    assert str(explicit) in missing[0]
    assert str(home) not in missing[0]
    assert _agent_files(repo) == []
