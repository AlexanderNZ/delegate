"""The text of the skills of an agent, for the prompt of a new session (#215).

Measured on Claude Code 2.1.287 on 2026-11-01: under `claude --print --agent <name>`, the `skills` list of an agent does
not preload a skill, for a project agent, for an agent of `--agents` and for a plugin agent. The skill is in the skill
list of the session, but its text is not in the context. A prompt that starts with `/<skill>` expands that skill, but
only the first such line. So an adapter puts the text of each skill in the prompt, before the brief.

A load-time block of a skill is a fenced block that opens with ```! , or an inline command `!` and a command in backticks.
The harness runs its command when it loads the skill, and puts the output in the place of the block. This module does the same, in the working directory of the
session, for a block that is one plain command: one line, with no shell syntax and no expansion (see `_plain_argv`).
The command runs with no shell. A block of another form does not run: its text stays, with a line that tells the agent
to run the command. So the engine runs no command that the harness could not analyse (ADR 0006).
"""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..definitions.validate import split_frontmatter

# The time that one load-time block can take.
BLOCK_TIMEOUT: float = 30.0

# The heading of the part of the prompt that holds the skills, and the heading of the brief after it.
SKILLS_HEADING: str = "# The skills of this session"
BRIEF_HEADING: str = "# The brief"

# A character that the shell gives a special meaning outside quotes. A plain command holds none of them there.
SPECIAL_OUTSIDE_QUOTES: frozenset[str] = frozenset("$`;&|<>(){}[]*?~!#\n")

# A character that the shell expands in double quotes.
SPECIAL_IN_DOUBLE_QUOTES: frozenset[str] = frozenset("$`")

# An inline load-time command: `!` and a command in backticks, after a space or at the start of a line, outside a fenced block.
INLINE = re.compile(r"(?<![^\s])!`([^`\n]+)`")

# A first word that the shell reads as a keyword, and a first word that is an assignment.
KEYWORDS: frozenset[str] = frozenset({"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "function", "select", "time", "coproc"})
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass(frozen=True)
class Skill:
    """One skill that an agent lists: the name to show, and its directory, or `None` when the adapter did not find it."""

    name: str
    directory: Path | None


def with_skills(prompt: str, skills: Sequence[Skill], cwd: Path, environment: Mapping[str, str]) -> str:
    """The prompt of a new session: the text of each skill in `skills`, and then `prompt` with no change.

    `prompt` with no change when `skills` is empty. Each load-time block runs in `cwd` with `environment`.
    """
    if not skills:
        return prompt
    found = [skill for skill in skills if skill.directory is not None and (skill.directory / "SKILL.md").is_file()]
    missing = [skill.name for skill in skills if skill not in found]
    parts = [
        SKILLS_HEADING,
        "",
        f"Your agent definition lists these skills: {', '.join(skill.name for skill in skills)}. The harness does not load "
        "a skill for the main agent of a session, so the engine put the text of each skill here, before the brief. The "
        "engine ran each load-time block of a skill in the working directory of this session, and the output of the "
        "block is in the place of the block. Do not load these skills again with the skill tool.",
    ]
    if missing:
        parts += ["", f"The engine did not find the text of these skills: {', '.join(missing)}. Load each one with the skill tool before you start."]
    for skill in found:
        assert skill.directory is not None
        parts += ["", f'<skill name="{skill.name}">', f"Base directory for this skill: {skill.directory}", "", _body(skill.directory, cwd, environment).strip("\n"), "</skill>"]
    return "\n".join(parts) + f"\n\n{BRIEF_HEADING}\n\n{prompt}"


def _body(directory: Path, cwd: Path, environment: Mapping[str, str]) -> str:
    """The text of `SKILL.md` in `directory` with no frontmatter, and with each load-time block replaced (see `_expand`)."""
    try:
        text = (directory / "SKILL.md").read_text()
    except (OSError, UnicodeDecodeError) as failure:
        return f"The engine could not read {directory / 'SKILL.md'}: {failure}. Load the skill with the skill tool."
    frontmatter, body, error = split_frontmatter(text)
    return _expand(body if error is None and frontmatter is not None else text, cwd, environment)


def _expand(text: str, cwd: Path, environment: Mapping[str, str]) -> str:
    """Replace each load-time block of `text` with the output of its command. Another fenced block stays as it is."""
    lines: list[str] = []
    block: list[str] | None = None  # the lines of the load-time block that is open
    fence: str | None = None  # the characters that opened another fenced block that is open
    for line in text.splitlines():
        stripped = line.strip()
        if block is not None:
            if stripped == "```":
                lines.append(_run_block(block, cwd, environment))
                block = None
            else:
                block.append(line)
        elif fence is not None:
            lines.append(line)
            if stripped.startswith(fence[0] * 3) and stripped.strip(fence[0]) == "" and len(stripped) >= len(fence):
                fence = None
        elif stripped.startswith("```") and stripped[3:].strip() == "!":
            block = []
        else:
            opened = re.match(r"^(`{3,}|~{3,})", stripped)
            fence = opened.group(1) if opened else None
            lines.append(line if fence is not None else INLINE.sub(lambda found: _run_inline(found.group(1), cwd, environment), line))
    if block is not None:  # a block with no end: its text stays
        lines += ["```!", *block]
    return "\n".join(lines) + "\n"


def _run_block(block: list[str], cwd: Path, environment: Mapping[str, str]) -> str:
    """The output of the command of a load-time block, or the block with a line that tells why it did not run."""
    commands = [line.strip() for line in block if line.strip()]
    argv = _plain_argv(commands[0]) if len(commands) == 1 else None
    if argv is None:
        reason = "it is not one plain command" if commands else "it holds no command"
        return "\n".join(["```!", *block, "```", f"The engine did not run this load-time block: {reason}. Run the command yourself, and read its output."])
    try:
        done = subprocess.run(argv, cwd=cwd, env=dict(environment), capture_output=True, text=True, timeout=BLOCK_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as failure:
        return "\n".join(["```!", *block, "```", f"The engine did not run this load-time block: {failure}. Run the command yourself, and read its output."])
    if done.returncode == 0:
        return done.stdout.rstrip("\n")
    return "\n".join(
        [f"The load-time block `{commands[0]}` exited with status {done.returncode}:", done.stdout.rstrip("\n"), done.stderr.rstrip("\n")]
    ).rstrip("\n")


def _run_inline(command: str, cwd: Path, environment: Mapping[str, str]) -> str:
    """The output of an inline load-time command on one line, or the command with a note when it did not run."""
    output = _run_block([command], cwd, environment)
    if output.startswith("```!"):
        return f"!`{command}` (the engine did not run this load-time command: run it yourself, and read its output)"
    return output


def _plain_argv(command: str) -> list[str] | None:
    """The words of `command` when it is one plain command, else `None`.

    A plain command has, outside quotes, no character of SPECIAL_OUTSIDE_QUOTES, and in double quotes no `$` and no
    backtick. Its first word is not a keyword and not an assignment. Such a command gives the same words with no shell
    as in a POSIX shell.
    """
    quote: str | None = None
    for character in command:
        if quote is None:
            if character in "'\"":
                quote = character
            elif character in SPECIAL_OUTSIDE_QUOTES:
                return None
        elif character == quote:
            quote = None
        elif quote == '"' and character in SPECIAL_IN_DOUBLE_QUOTES:
            return None
    if quote is not None:
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words or words[0] in KEYWORDS or ASSIGNMENT.match(words[0]):
        return None
    return words
