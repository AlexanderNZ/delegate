"""The helpers of the tests that read the commands and the sections of the skills (#354)."""

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
from delegate.cli import agent_definitions, brief, clean, delegate, docs_command, pause, run_command, runs, status, stop, watch
from delegate.docs.neutrality import KIT_ROOT_VAR


MARKER = "<!-- not-a-command -->"


SAMPLE = "1"  # a value that each kind of option accepts: a string, a path or an integer


FENCE = re.compile(r"^\s*(`{3,})")


INLINE = re.compile(r"(?<!`)`([^`]+)`(?!`)")


PLACEHOLDER = re.compile(r"<[^<>\n]+>")


# The parser of each subcommand of `delegate`. `render`, `schema`, `validate` and `bootstrap` go to `agent-definitions`.
SUBCOMMAND_PARSERS: dict[str, Callable] = {
    "run": run_command.build_parser,
    "pause": pause.build_parser,
    "stop": stop.build_parser,
    "status": status.build_parser,
    "runs": runs.build_parser,
    "watch": watch.build_parser,
    "clean": clean.build_parser,
    "docs": docs_command.build_parser,
    "brief": brief.build_parser,
}


AGENT_DEFINITIONS_SUBCOMMANDS = {"render", "schema", "validate", "bootstrap"}


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
        if subcommand.startswith("-") and not tiers:
            # An option of the umbrella itself, such as --version: the command decides (#236).
            with contextlib.redirect_stderr(io.StringIO()) as stderr, contextlib.redirect_stdout(io.StringIO()):
                try:
                    code = delegate.main(args)
                except SystemExit as stop_:
                    code = stop_.code
            return None if code in (0, None) else (stderr.getvalue().strip().splitlines() or [f"exit {code}"])[-1]
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


KIT_ROOT = Path(os.environ.get(KIT_ROOT_VAR) or Path(__file__).resolve().parents[1] / "skills")


SKILL = KIT_ROOT / "agent-delegation" / "SKILL.md"


in_the_kit = pytest.mark.skipif(not SKILL.is_file(), reason="the protocol skill is outside the package source, as in a Nix build")


def section(text: str, heading: str) -> str:
    """The lines under `heading` (a full heading line) up to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines()
    assert heading in lines, f"the skill has no heading {heading!r}"
    body: list[str] = []
    for line in lines[lines.index(heading) + 1:]:
        if re.match(rf"^#{{1,{level}}} ", line):
            break
        body.append(line)
    return "\n".join(body)
