"""Load agent declarations from TOML or JSON."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

KNOWN_KEYS = {
    "description", "tier", "skills", "prompt", "references",
    "maxTurns", "effort", "pair", "readOnly", "gateCommands",
    "getOnlyCommands", "outputLanguage", "trackedFileBuild", "contextLoad",
}

#: The languages an agent can be told to write its report in. "none" adds no
#: language rule. "ste" adds the ASD-STE100 Simplified Technical English rule.
OUTPUT_LANGUAGES = ("none", "ste")

#: How the context skill loads its references. "blocks" (the default) reads
#: each reference with a load-time block. "read" writes no load-time block: the
#: first instruction of the skill tells the agent to read each reference with
#: the Read tool, for a host where a load-time block cannot run (issue #148).
CONTEXT_LOADS = ("blocks", "read")

#: The line that a context skill with contextLoad = "read" carries below its
#: header comment. The validator reads it from the file on disk: a skill with
#: this line must hold no load-time block.
CONTEXT_LOAD_READ_MARKER = "<!-- agent-definitions:context-load read -->"

#: A gate command is plain words separated by single spaces. The guard puts it
#: in a bash case pattern, and the OpenCode map in a glob: no quote, no shell
#: syntax, no glob character may reach either (ADR 0004).
_WORD = r"[A-Za-z0-9._/:=@+-]+"
GATE_COMMAND = re.compile(rf"{_WORD}( {_WORD})*")

#: An exact gate is a gate command with one more form of word: a word in double
#: quotes that holds one command substitution of plain words, such as
#: "zensical==$(cat .zensical-version)". One entry can hold more than one such
#: word. The first word stays plain. The guard permits an exact gate only as
#: the whole command, character for character, and the OpenCode map leaves it
#: out (issue #75).
_SUBSTITUTION_WORD = r'"[A-Za-z0-9._/:=@+-]*\$\(' + rf"{_WORD}( {_WORD})*" + r'\)[A-Za-z0-9._/:=@+-]*"'
EXACT_GATE_COMMAND = re.compile(rf"{_WORD}( ({_WORD}|{_SUBSTITUTION_WORD}))*")
_SUBSTITUTION_BODY = re.compile(r"\$\(([^)]*)\)")


@dataclass
class Agent:
    key: str
    description: str
    tier: str = "standard"
    skills: list[str] = field(default_factory=list)
    prompt: str = ""
    references: list[str] = field(default_factory=list)
    max_turns: int | None = None
    effort: str | None = None
    pair: bool = True
    read_only: bool = False
    gate_commands: list[str] = field(default_factory=list)
    get_only_commands: list[str] = field(default_factory=list)
    output_language: str = "none"
    tracked_file_build: bool = False
    context_load: str = "blocks"


def _gate_commands(key: str, values, field_name: str = "gateCommands") -> list[str]:
    """The verifier's extra allowed commands: the repository's gates (ADR 0004).

    A gate command opens itself, alone or with arguments, in this repository's
    verifier guard. It must not open a write: a temp-write or temp-destination
    command (those have their own path rules), or a prefix of a known write.
    An exact gate (EXACT_GATE_COMMAND, gateCommands only) opens itself as the
    full command only, and the command in each of its $(...) obeys the same
    write rule.
    A getOnlyCommands entry obeys the same rules, because the guard permits it
    in the same way while its arguments hold no method, body or output flag.
    """
    from .render import VERIFIER_TEMP_DEST_COMMANDS, VERIFIER_TEMP_WRITE_COMMANDS
    from .validate import VERIFIER_BASH_WRITE_PROBES

    def opens_write(c: str) -> bool:
        first = c.split(" ", 1)[0]
        return first in VERIFIER_TEMP_WRITE_COMMANDS + VERIFIER_TEMP_DEST_COMMANDS or any(
            p == c or p.startswith(c + " ") or c.startswith(p) for p in VERIFIER_BASH_WRITE_PROBES
        )

    exact_allowed = field_name == "gateCommands"
    out = []
    for c in values:
        plain = isinstance(c, str) and GATE_COMMAND.fullmatch(c)
        exact = not plain and exact_allowed and isinstance(c, str) and EXACT_GATE_COMMAND.fullmatch(c)
        if not plain and not exact:
            extra = (
                ", or such words with words in double quotes that each hold one $(...) of plain words"
                if exact_allowed
                else ""
            )
            raise ValueError(
                f"agent {key!r}: {field_name} entry {c!r} must be plain words "
                f"(letters, digits, . _ / : = @ + -) separated by single spaces{extra}"
            )
        bodies = _SUBSTITUTION_BODY.findall(c) if exact else []
        if opens_write(c) or any(opens_write(b) for b in bodies):
            raise ValueError(f"agent {key!r}: {field_name} entry {c!r} opens a write command")
        out.append(c)
    return out


def _output_language(key: str, value) -> str:
    if not isinstance(value, str) or value not in OUTPUT_LANGUAGES:
        raise ValueError(f'agent {key!r}: outputLanguage must be "none" or "ste", not {value!r}')
    return value


def _tracked_file_build(key: str, value) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"agent {key!r}: trackedFileBuild must be true or false, not {value!r}")
    return value


def _context_load(key: str, value) -> str:
    if not isinstance(value, str) or value not in CONTEXT_LOADS:
        raise ValueError(f'agent {key!r}: contextLoad must be "blocks" or "read", not {value!r}')
    return value


def parse_declaration(data: dict) -> list[Agent]:
    agents = data.get("agents", data)
    out: list[Agent] = []
    for key, spec in agents.items():
        unknown = set(spec) - KNOWN_KEYS
        if unknown:
            raise ValueError(f"agent {key!r}: unknown keys {sorted(unknown)}")
        if "description" not in spec or not spec["description"].strip():
            raise ValueError(f"agent {key!r}: description is required")
        out.append(
            Agent(
                key=key,
                description=spec["description"].strip(),
                tier=spec.get("tier", "standard"),
                skills=list(spec.get("skills", [])),
                prompt=spec.get("prompt", "").strip(),
                references=list(spec.get("references", [])),
                max_turns=spec.get("maxTurns"),
                effort=spec.get("effort"),
                pair=spec.get("pair", True),
                read_only=spec.get("readOnly", False),
                gate_commands=_gate_commands(key, spec.get("gateCommands", [])),
                get_only_commands=_gate_commands(key, spec.get("getOnlyCommands", []), "getOnlyCommands"),
                output_language=_output_language(key, spec.get("outputLanguage", "none")),
                tracked_file_build=_tracked_file_build(key, spec.get("trackedFileBuild", False)),
                context_load=_context_load(key, spec.get("contextLoad", "blocks")),
            )
        )
    return out


def load_declaration(path: Path) -> list[Agent]:
    path = Path(path)
    text = path.read_text()
    data = tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)
    return parse_declaration(data)
