"""Behaviour tests for the load-time block check of `agent-definitions validate` (issue #95).

Claude Code runs a load-time block (a fenced block that opens with ```!) only
when it can analyse the command. A block with shell syntax stops a subagent
that preloads the skill from starting (ADR 0006). `validate` reads each skill
that an agent names and gives one finding for each such block, with the code,
the file and the line. Each case goes through `cli.main`.
"""

import pytest

from delegate.cli.agent_definitions import main
from tests.test_bootstrap import _args, _paths, repo  # noqa: F401  (repo is a fixture)

CODE = "LOAD_BLOCK_NOT_PLAIN"

# One block for each refused form. Each command holds that form and no other.
REFUSED = {
    "$(": "cat $(git rev-parse --show-toplevel)/docs/gates.md",
    "a backtick": "cat `git rev-parse --show-toplevel`/docs/gates.md",
    ";": "cd docs; cat gates.md",
    "&&": "cd docs && cat gates.md",
    "||": "cat docs/gates.md || true",
    "|": "cat docs/gates.md | head -n 5",
    "if": "if test -f docs/gates.md\nthen cat docs/gates.md\nfi",
    "a redirection": "cat docs/gates.md 2>/dev/null",
}

# Plain commands. A sign in quotes is text, not shell syntax.
PLAIN = (
    "sed -n '1,20p' docs/gates.md",
    "grep -n -E 'gate|line' docs/gates.md",
    'grep -n "a;b" docs/gates.md',
)


def _validate(repo, user_skills, capsys):
    capsys.readouterr()
    code = main([
        "validate",
        "--claude-code", str(repo / ".claude" / "agents"),
        "--opencode", str(repo / ".opencode" / "agents"),
        "--skills-dir", str(repo / ".claude" / "skills"),
        "--user-skills-dir", str(user_skills),
    ])
    return code, capsys.readouterr().out


@pytest.fixture
def user_skills(tmp_path):
    """An empty user skills directory, so the skills of the host never reach the test."""
    d = tmp_path / "user-skills"
    d.mkdir()
    return d


def test_each_refused_form_in_a_context_skill_gives_one_finding_with_the_code_the_file_and_the_line(repo, user_skills, capsys):
    assert main(_args(repo)) == 0
    skill = _paths(repo)["skill"]
    blocks = [f"```!\n{command}\n```" for command in (*REFUSED.values(), *PLAIN)]
    skill.write_text(skill.read_text() + "\n## Edited by hand\n\n" + "\n\n".join(blocks) + "\n")

    # The expected line of each finding is the line that holds the form.
    lines = skill.read_text().splitlines()
    expected = {}
    for form, command in REFUSED.items():
        first = command.splitlines()[0]
        expected[form] = lines.index(first) + 1

    code, out = _validate(repo, user_skills, capsys)
    assert code == 1, out
    findings = [line for line in out.splitlines() if line.startswith(CODE + " ")]
    assert len(findings) == len(REFUSED), out
    for form, number in expected.items():
        hits = [f for f in findings if f.startswith(f"{CODE} {skill}:{number}: ")]
        assert len(hits) == 1, f"no single finding for {form!r} at line {number}:\n{out}"
        assert f"holds {form if form.startswith('a ') else '`' + form + '`'}" in hits[0], hits[0]


def test_the_context_skill_that_bootstrap_writes_gives_no_finding(repo, user_skills, capsys):
    assert main(_args(repo)) == 0
    assert _paths(repo)["skill"].read_text().count("```!\n") == 2
    code, out = _validate(repo, user_skills, capsys)
    assert code == 0, out
    assert CODE not in out
    assert out.strip().endswith("ok")


def test_a_skill_in_the_user_skills_dir_is_checked_too(repo, user_skills, capsys):
    # An agent can preload a skill from the user skills directory. Claude Code
    # loads it from there, so its blocks stop the subagent the same way.
    assert main(_args(repo)) == 0
    tdd = user_skills / "tdd" / "SKILL.md"
    tdd.parent.mkdir()
    tdd.write_text("---\nname: tdd\ndescription: The tdd skill.\n---\n\n```!\ncat a.md; cat b.md\n```\n")
    code, out = _validate(repo, user_skills, capsys)
    assert code == 1, out
    assert f"{CODE} {tdd}:7: " in out, out
