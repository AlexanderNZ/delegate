"""Each command that a skill gives parses with the real parser of that command (#185).

The skill `agent-delegation` is the instruction set of the coordinator. A command in it that the CLI refuses
(`delegate run <workflow> --mode economy` was one: the mode is a field of the workflow file, not a flag) sends
the coordinator to a usage error. The reference pages have tests that keep their option tables equal to the
parsers. This test does the same for the commands in the two `SKILL.md` files.

The test reads each command line that starts with `delegate `, `agent-definitions ` or `verifier-brief `, in
fenced code and in inline code. It replaces each placeholder `<...>` with a sample value, splits the line as a
shell does, and parses it with the real parser of the command. It runs no command.

A line that is not a command (for example a synopsis with `[--flag]` or `...`) is marked with the comment
`<!-- not-a-command -->`: on the same line as an inline code span, or on the line directly above the opening
fence of a fenced block. The test skips the marked lines.

The skills are outside the package source. A Nix build that copies only the package finds them through the kit
root variable, or skips these cases with a reason.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from delegate.docs.neutrality import KIT_ROOT_VAR
from .support_skills import MARKER, commands_of, parse_error

KIT_ROOT = Path(os.environ.get(KIT_ROOT_VAR) or Path(__file__).resolve().parents[1] / "skills")
SKILLS = [KIT_ROOT / "agent-delegation" / "SKILL.md", KIT_ROOT / "agent-definitions" / "SKILL.md"]

in_the_kit = pytest.mark.skipif(not all(path.is_file() for path in SKILLS), reason="the skills are outside the package source, as in a Nix build")


def refused(path: Path) -> list[str]:
    text = path.read_text()
    return [f"{path.parent.name}/{path.name}:{c.line}: {c.text}  ->  {parse_error(c.text)}" for c in commands_of(text) if parse_error(c.text)]


@in_the_kit
@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_each_command_of_the_skill_parses_with_the_real_parser(skill):
    assert refused(skill) == []


@in_the_kit
@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_the_skill_gives_commands_to_check(skill):
    assert len(commands_of(skill.read_text())) >= 5


def test_a_command_with_an_option_that_the_parser_does_not_know_is_refused():
    text = "Run:\n\n```bash\ndelegate run <workflow> --mode economy\n```\n"

    assert [(c.line, parse_error(c.text)) for c in commands_of(text)] == [(4, "delegate run: error: unrecognized arguments: --mode economy")]


def test_an_option_of_the_umbrella_is_accepted_and_an_unknown_one_is_refused():
    text = "Check `delegate --version` and `delegate schema`, not `delegate --bogus`.\n"

    assert [(c.text, parse_error(c.text) is None) for c in commands_of(text)] == [
        ("delegate --version", True), ("delegate schema", True), ("delegate --bogus", False),
    ]


def test_an_inline_command_is_read_also_when_it_wraps_over_two_lines():
    text = "Resume with\n`delegate run --resume <run-id>\n--bogus` now.\n"

    assert [(c.line, parse_error(c.text)) for c in commands_of(text)] == [(2, "delegate run: error: unrecognized arguments: --bogus")]


def test_a_command_in_a_fence_with_a_continuation_is_one_command():
    text = "```bash\nagent-definitions render d.toml \\\n  -o /tmp/out\n```\n"

    assert [(c.line, c.text, parse_error(c.text)) for c in commands_of(text)] == [(2, "agent-definitions render d.toml -o /tmp/out", None)]


def test_a_marked_inline_code_and_a_marked_fence_are_skipped():
    text = (
        f"A synopsis `delegate run [--dry-run] ...` {MARKER}\n"
        "\n"
        f"{MARKER}\n"
        "```bash\n"
        "verifier-brief full [--base main]\n"
        "```\n"
        "\n"
        "```bash\n"
        "verifier-brief full [--base main]\n"
        "```\n"
    )

    assert [c.line for c in commands_of(text)] == [9]


def test_a_line_that_starts_with_another_word_is_no_command():
    text = "Use `git diff` and `delegate-context` and\n\n```bash\nls -l\n```\n"

    assert commands_of(text) == []


def test_a_valid_option_before_the_subcommand_is_accepted_and_an_unknown_subcommand_is_refused():
    assert parse_error("delegate --tiers t.toml render d.toml -o out") is None
    assert "no subcommand" in (parse_error("delegate frobnicate now") or "")
