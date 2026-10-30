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

import contextlib
import io
import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from delegate.cli import agent_definitions, brief, clean, docs_command, pause, run_command, status, stop, watch
from delegate.docs.neutrality import KIT_ROOT_VAR

KIT_ROOT = Path(os.environ.get(KIT_ROOT_VAR) or Path(__file__).resolve().parents[1] / "skills")
SKILLS = [KIT_ROOT / "agent-delegation" / "SKILL.md", KIT_ROOT / "agent-definitions" / "SKILL.md"]

in_the_kit = pytest.mark.skipif(not all(path.is_file() for path in SKILLS), reason="the skills are outside the package source, as in a Nix build")

MARKER = "<!-- not-a-command -->"
SAMPLE = "1"  # a value that each kind of option accepts: a string, a path or an integer
FENCE = re.compile(r"^\s*(`{3,})")
INLINE = re.compile(r"(?<!`)`([^`]+)`(?!`)")
PLACEHOLDER = re.compile(r"<[^<>\n]+>")

# The parser of each subcommand of `delegate`. `render`, `validate` and `bootstrap` go to `agent-definitions`.
SUBCOMMAND_PARSERS: dict[str, Callable] = {
    "run": run_command.build_parser,
    "pause": pause.build_parser,
    "stop": stop.build_parser,
    "status": status.build_parser,
    "watch": watch.build_parser,
    "clean": clean.build_parser,
    "docs": docs_command.build_parser,
    "brief": brief.build_parser,
}
AGENT_DEFINITIONS_SUBCOMMANDS = {"render", "validate", "bootstrap"}
PREFIXES = ("delegate ", "agent-definitions ", "verifier-brief ")


@dataclass(frozen=True)
class Command:
    line: int
    text: str


def commands_of(text: str) -> list[Command]:
    """Each unmarked command line of a skill: from fenced code and from inline code, with its line number."""
    found: list[Command] = []
    lines = text.splitlines()
    fenced = False
    skip_block = False
    start = 0
    prose: list[str] = []
    prose_start = 0

    def flush_prose() -> None:
        # Inline code can wrap over lines, so read the prose of a paragraph as one text.
        joined = "\n".join(prose)
        for match in INLINE.finditer(joined):
            first = prose_start + joined.count("\n", 0, match.start(1))
            last = prose_start + joined.count("\n", 0, match.end(1))
            if any(MARKER in lines[n] for n in range(first, last + 1)):
                continue
            code = " ".join(match.group(1).split())
            if code.startswith(PREFIXES):
                found.append(Command(first + 1, code))
        prose.clear()

    for number, line in enumerate(lines):
        if FENCE.match(line):
            if not fenced:
                flush_prose()
                fenced = True
                skip_block = number > 0 and MARKER in lines[number - 1]
                start = number
                block: list[str] = []
            else:
                fenced = False
                if not skip_block:
                    found.extend(fenced_commands(block, start + 2))
            continue
        if fenced:
            block.append(line)
        else:
            if not prose:
                prose_start = number
            prose.append(line)
    flush_prose()
    return found


def fenced_commands(block: list[str], first_line: int) -> list[Command]:
    """The command lines of a fenced block. A line that ends with a backslash goes on in the next line."""
    commands: list[Command] = []
    joined = ""
    joined_at = 0
    for offset, line in enumerate(block):
        if not joined:
            joined_at = first_line + offset
        stripped = line.strip()
        if stripped.endswith("\\"):
            joined += stripped[:-1].rstrip() + " "
            continue
        joined += stripped
        if joined.startswith(PREFIXES):
            commands.append(Command(joined_at, " ".join(joined.split())))
        joined = ""
    return commands


def parse_error(command: str) -> str | None:
    """None when the real parser of the command accepts it, otherwise the message of the parser."""
    words = shlex.split(PLACEHOLDER.sub(SAMPLE, command))
    name, args = words[0], words[1:]
    if name == "delegate":
        tiers = []
        while args[:1] == ["--tiers"]:
            tiers, args = tiers + args[:2], args[2:]
        if not args:
            return "delegate takes a subcommand"
        subcommand, rest = args[0], args[1:]
        if subcommand in AGENT_DEFINITIONS_SUBCOMMANDS:
            parser, argv = agent_definitions.build_parser(), [*tiers, subcommand, *rest]
        elif subcommand in SUBCOMMAND_PARSERS and not tiers:
            parser, argv = SUBCOMMAND_PARSERS[subcommand](), rest
        else:
            return f"delegate has no subcommand {subcommand!r} here"
    elif name == "agent-definitions":
        parser, argv = agent_definitions.build_parser(), args
    else:
        parser, argv = brief.build_parser(), args
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
        try:
            parser.parse_args(argv)
        except SystemExit as stop_:
            if stop_.code not in (0, None):
                return stderr.getvalue().strip().splitlines()[-1]
    return None


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
