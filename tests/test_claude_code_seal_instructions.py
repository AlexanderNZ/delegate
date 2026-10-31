"""The Claude Code seal holds the instructions of the repository (#218).

Under `--setting-sources user` a session does not load the `CLAUDE.md` of the project, so the seal puts the text of
`CLAUDE.md` and `.claude/CLAUDE.md` of the main checkout, with the imports, at the end of the sealed agent file. Each case
seals with the real adapter, from files that the renderer made, with a stand-in `claude` command that answers `--version`.
"""

import hashlib
from pathlib import Path

import pytest
from delegate import adapters
from delegate.definitions.declaration import Agent
from delegate.definitions.render import render

pytestmark = pytest.mark.usefixtures("claude_version")

HEADING = "## Instructions of the repository"


def write_agents(root: Path, tiers) -> None:
    target = root / ".claude" / "agents"
    target.mkdir(parents=True, exist_ok=True)
    agent = Agent(key="python", description="The python stack.", gate_commands=["python3 -m pytest -rs"])
    for name, content in render([agent], tiers)["claude-code"].items():
        (target / name).write_text(content)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def sealed_text(repo: Path, into: Path) -> str:
    assert adapters.get("claude-code").seal("python-specialist", repo, into) is not None
    return (into / "agents" / "python-specialist.md").read_text()


def section_of(text: str) -> str:
    """The text of the sealed file after the heading of the section."""
    assert text.count(HEADING) == 1, "one section exactly"
    return text.split(HEADING, 1)[1]


def test_a_claude_md_with_an_import_gives_a_section_at_the_end_with_the_text_of_the_imported_file(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    write(repo / "CLAUDE.md", "@AGENTS.md\n")
    write(repo / "AGENTS.md", "# Agent instructions\n\nRun the tests before you commit.\n")

    text = sealed_text(repo, tmp_path / "seal")

    section = section_of(text)
    assert "Run the tests before you commit." in section
    assert "@AGENTS.md" not in section
    assert text.rstrip().endswith("Run the tests before you commit.")  # the section is the last part of the body


def test_the_section_holds_claude_md_and_then_the_dot_claude_claude_md_and_each_one_that_exists(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    write(repo / "CLAUDE.md", "FIRST-FILE\n")
    write(repo / ".claude" / "CLAUDE.md", "SECOND-FILE\n")

    section = section_of(sealed_text(repo, tmp_path / "both"))

    assert section.index("FIRST-FILE") < section.index("SECOND-FILE")

    (repo / "CLAUDE.md").unlink()
    only_second = section_of(sealed_text(repo, tmp_path / "second"))
    assert "SECOND-FILE" in only_second and "FIRST-FILE" not in only_second

    write(repo / "CLAUDE.md", "FIRST-FILE\n")
    (repo / ".claude" / "CLAUDE.md").unlink()
    only_first = section_of(sealed_text(repo, tmp_path / "first"))
    assert "FIRST-FILE" in only_first and "SECOND-FILE" not in only_first


def test_a_path_counts_from_the_directory_of_the_file_that_imports_it(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    write(repo / ".claude" / "CLAUDE.md", "@rules/style.md\n")
    write(repo / ".claude" / "rules" / "style.md", "STYLE\n@more.md\n")
    write(repo / ".claude" / "rules" / "more.md", "MORE-NEXT-TO-STYLE\n")
    write(repo / "more.md", "WRONG-FILE\n")

    section = section_of(sealed_text(repo, tmp_path / "seal"))

    assert "STYLE" in section and "MORE-NEXT-TO-STYLE" in section
    assert "WRONG-FILE" not in section


def test_an_import_in_an_imported_file_is_replaced_to_a_depth_of_5_and_not_further(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    write(repo / "CLAUDE.md", "ROOT\n@one.md\n")
    names = ["one", "two", "three", "four", "five", "six", "seven"]
    for depth, (name, following) in enumerate(zip(names, names[1:]), start=1):
        write(repo / f"{name}.md", f"LEVEL-{depth}\n@{following}.md\n")
    write(repo / "seven.md", "LEVEL-7\n")

    section = section_of(sealed_text(repo, tmp_path / "seal"))

    for depth in range(1, 6):
        assert f"LEVEL-{depth}\n" in section
    assert "LEVEL-6" not in section  # the sixth hop is not replaced
    assert "@six.md" in section  # the import line stays
    assert "LEVEL-7" not in section


def test_two_files_that_import_each_other_end_at_the_depth_and_do_not_loop(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    write(repo / "CLAUDE.md", "@a.md\n")
    write(repo / "a.md", "A-TEXT\n@b.md\n")
    write(repo / "b.md", "B-TEXT\n@a.md\n")

    section = section_of(sealed_text(repo, tmp_path / "seal"))

    assert section.count("A-TEXT") == 3 and section.count("B-TEXT") == 2


def test_a_missing_import_keeps_its_line_and_the_rest_is_replaced(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    write(repo / "CLAUDE.md", "Before.\n@docs/missing.md\n@AGENTS.md\nAfter.\n")
    write(repo / "AGENTS.md", "AGENTS-TEXT\n")

    section = section_of(sealed_text(repo, tmp_path / "seal"))

    lines = section.splitlines()
    assert "@docs/missing.md" in lines
    assert "AGENTS-TEXT" in lines and "@AGENTS.md" not in lines
    assert lines.index("Before.") < lines.index("@docs/missing.md") < lines.index("AGENTS-TEXT") < lines.index("After.")


@pytest.mark.parametrize("fence", ["```", "~~~", "```markdown"])
def test_an_import_in_a_code_block_is_not_replaced_and_an_import_after_the_block_is(tmp_path, home, tiers, fence):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    write(repo / "CLAUDE.md", f"{fence}\n@AGENTS.md\n{fence[:3]}\n@AGENTS.md\n")
    write(repo / "AGENTS.md", "AGENTS-TEXT\n")

    section = section_of(sealed_text(repo, tmp_path / "seal"))

    lines = section.splitlines()
    assert lines.count("@AGENTS.md") == 1  # the one in the block
    assert lines.count("AGENTS-TEXT") == 1  # the one after the block
    assert lines.index(fence) < lines.index("@AGENTS.md") < lines.index("AGENTS-TEXT")


def test_text_around_an_import_on_the_same_line_is_not_an_import(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    write(repo / "CLAUDE.md", "See @AGENTS.md for the rules.\n")
    write(repo / "AGENTS.md", "AGENTS-TEXT\n")

    section = section_of(sealed_text(repo, tmp_path / "seal"))

    assert "See @AGENTS.md for the rules." in section and "AGENTS-TEXT" not in section


def test_claude_local_md_is_not_sealed(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    write(repo / "CLAUDE.md", "PUBLIC\n")
    write(repo / "CLAUDE.local.md", "PERSONAL-SETTING\n")

    text = sealed_text(repo, tmp_path / "seal")

    assert "PUBLIC" in text and "PERSONAL-SETTING" not in text


def test_a_repository_with_no_claude_md_gets_no_section_and_the_file_is_the_same_as_before(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    source = (repo / ".claude" / "agents" / "python-specialist.md").read_text()

    text = sealed_text(repo, tmp_path / "seal")

    assert HEADING not in text
    assert text.endswith(source.split("\n---", 1)[1])


def test_a_change_of_claude_md_after_the_seal_does_not_change_the_seal_and_a_new_seal_holds_the_new_text(tmp_path, home, tiers):
    repo = tmp_path / "repo"
    write_agents(repo, tiers)
    write(repo / "CLAUDE.md", "OLD-RULE\n")
    sealed = adapters.get("claude-code").seal("python-specialist", repo, tmp_path / "old")
    assert sealed is not None

    write(repo / "CLAUDE.md", "NEW-RULE\n")

    assert all(file.sha256 == hashlib.sha256(file.path.read_bytes()).hexdigest() for file in sealed.files)
    assert "OLD-RULE" in (tmp_path / "old" / "agents" / "python-specialist.md").read_text()
    assert "NEW-RULE" in section_of(sealed_text(repo, tmp_path / "new"))
