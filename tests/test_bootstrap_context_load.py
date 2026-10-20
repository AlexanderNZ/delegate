"""Behaviour tests for `contextLoad`, the way a context skill loads its references (issue #148).

With `contextLoad = "blocks"` (the default), the context skill reads each
reference with a load-time block. With `contextLoad = "read"`, the skill has no
load-time block: its first instruction tells the agent to read each reference
with the Read tool. A host where a load-time block cannot run uses `"read"`.
Every case goes through `cli.main`.
"""

import tomllib

import pytest

from delegate.cli.agent_definitions import main
from tests.test_bootstrap import GATES_REF, OPERATE_REF, _agent_files, _args, _blocks, _paths, repo  # noqa: F401
from tests.test_bootstrap_options import _rerun_argv

CODE = "LOAD_BLOCK_IN_READ_SKILL"


@pytest.fixture
def user_skills(tmp_path):
    """An empty user skills directory, so the skills of the host never reach the test."""
    d = tmp_path / "user-skills"
    d.mkdir()
    return d


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


def test_a_read_context_skill_has_no_load_time_block_and_reads_each_reference_in_order(repo):
    assert main(_args(repo, extra=("--context-load", "read"))) == 0
    text = _paths(repo)["skill"].read_text()
    assert "```!" not in text
    assert _blocks(text) == []
    gates = text.index("Read `docs/gates.md` with the Read tool.")
    operate = text.index("Read `docs/operate.md` with the Read tool.")
    assert gates < operate, "the references are in the declared order"
    # The first instruction comes before the first reference.
    first = text.index("Before any other step, read each document below with the Read tool")
    assert first < gates
    assert "If a document is missing, stop and report" in text[first:gates]


def test_a_read_context_skill_keeps_the_note_of_each_reference(repo):
    assert main(_args(repo, extra=("--context-load", "read"))) == 0
    text = _paths(repo)["skill"].read_text()
    assert f"## {GATES_REF}" in text
    assert f"## {OPERATE_REF}" in text


def test_context_load_read_writes_the_key_and_the_pair_validates(repo, user_skills, capsys):
    assert main(_args(repo, extra=("--context-load", "read"))) == 0
    declaration = tomllib.loads(_paths(repo)["declaration"].read_text())
    assert declaration["agents"]["acme-api"]["contextLoad"] == "read"
    code, out = _validate(repo, user_skills, capsys)
    assert code == 0, out
    assert out.strip().endswith("ok")


def test_context_load_blocks_writes_the_key_and_the_skill_of_the_default(repo):
    assert main(_args(repo)) == 0
    default_skill = _paths(repo)["skill"].read_text()
    assert "contextLoad" not in _paths(repo)["declaration"].read_text()
    assert main(_args(repo, extra=("--context-load", "blocks"))) == 0
    declaration = tomllib.loads(_paths(repo)["declaration"].read_text())
    assert declaration["agents"]["acme-api"]["contextLoad"] == "blocks"
    blocks_skill = _paths(repo)["skill"].read_text()
    assert len(_blocks(blocks_skill)) == 2
    # Only the re-run comment names the flag.
    assert blocks_skill.replace(" --context-load blocks", "") == default_skill


def test_an_unknown_context_load_exits_one_names_the_permitted_values_and_writes_nothing(repo, capsys):
    assert main(_args(repo, extra=("--context-load", "inline"))) == 1
    err = capsys.readouterr().err
    assert "inline" in err
    assert "blocks" in err and "read" in err
    assert _agent_files(repo) == []
    assert not _paths(repo)["declaration"].exists()


def test_the_rerun_comment_names_context_load_and_writes_the_same_bytes(repo):
    assert main(_args(repo, extra=("--context-load", "read"))) == 0
    before = {label: path.read_bytes() for label, path in _paths(repo).items()}
    for label in ("declaration", "skill"):
        assert "--context-load read" in _paths(repo)[label].read_text(), label
    assert main(_rerun_argv(_paths(repo)["declaration"].read_text(), repo)) == 0
    after = {label: path.read_bytes() for label, path in _paths(repo).items()}
    assert after == before


def test_a_read_context_skill_with_a_load_time_block_is_a_finding_with_the_file_and_the_line(repo, user_skills, capsys):
    assert main(_args(repo, extra=("--context-load", "read"))) == 0
    skill = _paths(repo)["skill"]
    # A plain command: LOAD_BLOCK_NOT_PLAIN does not apply, the read rule does.
    skill.write_text(skill.read_text() + "\n## Edited by hand\n\n```!\nsed -n '1,20p' docs/gates.md\n```\n")
    number = skill.read_text().splitlines().index("```!") + 1
    code, out = _validate(repo, user_skills, capsys)
    assert code == 1, out
    findings = [line for line in out.splitlines() if line.startswith(CODE + " ")]
    assert len(findings) == 1, out
    assert findings[0].startswith(f"{CODE} {skill}:{number}: "), out
    assert "LOAD_BLOCK_NOT_PLAIN" not in out


def test_a_blocks_context_skill_with_a_plain_load_time_block_is_no_finding(repo, user_skills, capsys):
    assert main(_args(repo)) == 0
    skill = _paths(repo)["skill"]
    skill.write_text(skill.read_text() + "\n## Edited by hand\n\n```!\nsed -n '1,20p' docs/gates.md\n```\n")
    code, out = _validate(repo, user_skills, capsys)
    assert code == 0, out
    assert CODE not in out


def test_a_declaration_with_context_load_read_renders(tmp_path, capsys):
    decl = tmp_path / "agents.toml"
    decl.write_text('[agents.acme]\ndescription = "acme work."\ncontextLoad = "read"\n')
    assert main(["render", str(decl), "-o", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "claude-code" / "acme-specialist.md").is_file()


@pytest.mark.parametrize("value", ['"inline"', '"READ"', '""', "1"])
def test_a_declaration_with_an_unknown_context_load_does_not_render(tmp_path, value):
    decl = tmp_path / "agents.toml"
    decl.write_text(f'[agents.acme]\ndescription = "acme work."\ncontextLoad = {value}\n')
    with pytest.raises(ValueError, match=r'contextLoad must be "blocks" or "read"'):
        main(["render", str(decl), "-o", str(tmp_path / "out")])
    assert not (tmp_path / "out" / "claude-code" / "acme-specialist.md").exists()
