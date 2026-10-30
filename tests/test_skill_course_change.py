"""The protocol skill tells the coordinator to tell the owner before it changes the course of a run (#203).

The section has a rule, a recovery table with one row for each case, and two bans. Each command of the table
parses with the real parser of `delegate` (the check of #185), so the table cannot name a command that the CLI
refuses. The skill is outside the package source: a Nix build that copies only the package skips these cases.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from delegate.docs.neutrality import KIT_ROOT_VAR

from .test_skill_commands import commands_of, parse_error

KIT_ROOT = Path(os.environ.get(KIT_ROOT_VAR) or Path(__file__).resolve().parents[1] / "skills")
SKILL = KIT_ROOT / "agent-delegation" / "SKILL.md"

in_the_kit = pytest.mark.skipif(not SKILL.is_file(), reason="the protocol skill is outside the package source, as in a Nix build")

HEADING = "## Tell the owner before you change the course of a run"

# Each case of the ticket: a word of the first cell of its row, and the command that the row must hold.
CASES: dict[str, str] = {
    "invalid verdict": "delegate run --detach --resume <run-id> --retry <ticket>",
    "economy chain": "delegate run --detach --resume <run-id> --retry <ticket>",
    "new ruling": "--retry <ticket>",
    "stop now": "delegate stop <run-id>",
    "give up": "delegate clean --abandon <run-id> --reason",
    "dev server": "XDG_STATE_HOME",
}


def the_section() -> str:
    lines = SKILL.read_text().splitlines()
    start = lines.index(HEADING)
    end = next((n for n in range(start + 1, len(lines)) if re.match(r"^#{1,2} ", lines[n])), len(lines))
    return "\n".join(lines[start:end])


def table_rows(text: str) -> dict[str, str]:
    """The first cell (lower case) and the rest of each row of the first table of the text."""
    rows: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("|") and not re.match(r"^\|\s*-", line):
            first, _, rest = line.strip("|").partition("|")
            rows[first.strip().lower()] = rest
    return rows


@in_the_kit
def test_the_skill_has_the_section_with_the_rule_before_a_step_that_changes_the_course():
    text = the_section().lower()

    for needed in ("pause", "stop", "abandon", "retry", "run id", "reason", "exact command", "earlier approval"):
        assert needed in text, needed


@in_the_kit
def test_each_case_of_the_ticket_has_a_row_with_its_command():
    rows = table_rows(the_section())

    for case, command in CASES.items():
        matching = [rest for first, rest in rows.items() if case in first]
        assert matching, f"no row for {case!r}"
        assert command in matching[0], f"the row of {case!r} lacks {command!r}"


@in_the_kit
def test_each_command_of_the_section_parses_with_the_real_parser():
    commands = commands_of(the_section())

    assert len(commands) >= 5
    assert [(c.text, parse_error(c.text)) for c in commands if parse_error(c.text)] == []


@in_the_kit
def test_the_section_bans_kill_and_the_removal_of_state_directories_by_hand():
    text = " ".join(the_section().lower().split())

    assert "never use `kill`" in text
    assert "never remove" in text and "by hand" in text


@in_the_kit
def test_the_section_tells_the_coordinator_to_check_the_version_of_the_engine_before_a_run():
    text = " ".join(the_section().lower().split())

    assert "version" in text and "before you start a run" in text
