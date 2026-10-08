"""The protocol skill states the modes of the engine, and its table is the same as the engine's settings.

The skill is outside the package source. A Nix build that copies only the
package finds it through the kit root variable, or skips these cases with a
reason. The expected cells come from the engine's own mode settings, and from
a real run of each mode with a scripted adapter, so the table cannot drift.
"""

import os
import re
from pathlib import Path

import pytest

from agent_definitions import delegate, engine, workflow
from agent_definitions.neutrality import KIT_ROOT_VAR

from .support import ScriptedAdapter, WORKFLOW, git, make_repo, read_journal

KIT_ROOT = Path(os.environ.get(KIT_ROOT_VAR) or Path(__file__).resolve().parents[3])
SKILL = KIT_ROOT / "agent-delegation" / "SKILL.md"

in_the_kit = pytest.mark.skipif(not SKILL.is_file(), reason="the protocol skill is outside the package source, as in a Nix build")

# The seven invariants of the spec, in its order. They hold in every mode.
INVARIANTS: list[str] = [
    "blind verifier",
    "gates outside the specialist",
    "no run-branch change without an accept",
    "no specialist push",
    "single-writer hotspots",
    "a fix-up is a new commit",
    "no lower verifier tier",
]


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


def mode_table() -> dict[str, dict[str, str]]:
    """The settings table of the skill: setting name -> mode -> cell text."""
    rows = [line for line in section(SKILL.read_text(), "### The two modes").splitlines() if line.startswith("|")]
    header, _rule, *body = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows]
    assert header[0] == "Setting"
    modes = [cell.strip("`") for cell in header[1:]]
    return {row[0]: dict(zip(modes, row[1:])) for row in body}


TWO_TICKETS = WORKFLOW.replace('"test -f feature.txt"', '"true"') + '''
[[tickets]]
id = "b"
text = "The export command writes a header row."
stack = "python"
blocked-by = []
'''


def observed(tmp_path, capsys, mode: str, scripted: ScriptedAdapter) -> dict[str, str]:
    """What a real run of two tickets in one stack shows, in the words of the table."""
    (tmp_path / mode).mkdir()
    repo = make_repo(tmp_path / mode, TWO_TICKETS.replace('mode = "assure"', f'mode = "{mode}"'))
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    out = capsys.readouterr().out
    assert code == 0
    starts = {e["ticket"]: e["base_commit"] for e in read_journal(out) if e["event"] == "step-start"}
    from_previous = starts["b"] == git(repo, "rev-parse", "run/demo-a").strip()
    from_base = starts["b"] == git(repo, "rev-parse", "main").strip()
    assert from_previous != from_base
    return {
        "Verifier runs": "each branch, at once" if len(scripted.verifier_calls) == 2 else "each stack, at the end of the chain",
        "Branch starts from": "previous ticket" if from_previous else "base branch",
    }


@in_the_kit
def test_the_table_names_the_two_modes_of_the_engine_and_the_six_settings():
    table = mode_table()

    assert set(table) == {"Verifier runs", "Branch starts from", "Specialist tier", "Verifier tier", "Fix-up rounds", "Continuations"}
    for cells in table.values():
        assert tuple(cells) == workflow.MODES


@in_the_kit
@pytest.mark.parametrize("mode", workflow.MODES)
def test_the_tiers_and_the_limits_in_the_table_are_the_ones_the_engine_uses(mode):
    cells = {setting: row[mode] for setting, row in mode_table().items()}

    assert cells["Specialist tier"] == f"`{engine.SPECIALIST_TIER[mode]}`"
    assert cells["Verifier tier"] == f"`{engine.VERIFIER_TIER}`"
    assert cells["Fix-up rounds"] == str(engine.FIXUP_ROUND_LIMIT[mode])
    assert cells["Continuations"] == str(engine.CONTINUATION_LIMIT[mode])


@in_the_kit
@pytest.mark.parametrize("mode", workflow.MODES)
def test_the_verification_point_and_the_branch_start_in_the_table_are_what_a_real_run_does(tmp_path, capsys, scripted, mode):
    cells = {setting: row[mode] for setting, row in mode_table().items()}

    shown = observed(tmp_path, capsys, mode, scripted)

    assert {setting: cells[setting] for setting in shown} == shown


@in_the_kit
def test_the_section_lists_the_seven_invariants_and_no_other():
    items = re.findall(r"^\d+\. \*\*(.+?)\.\*\*", section(SKILL.read_text(), "### The invariants of every mode"), re.MULTILINE)

    assert [item.lower() for item in items] == INVARIANTS


@in_the_kit
def test_every_rule_that_tells_the_coordinator_to_verify_at_once_names_the_assure_mode():
    paragraphs = re.split(r"\n\n|\n(?=- )", SKILL.read_text())  # a blank line or a list item starts a rule
    at_once = [p for p in paragraphs if re.search(r"immediately|do not batch|at once", p, re.IGNORECASE) and "verifier" in p.lower()]

    assert at_once
    for paragraph in at_once:
        assert "`assure`" in paragraph, paragraph[:120]


@in_the_kit
def test_the_skill_states_end_of_chain_verification_as_the_practice_of_economy():
    text = " ".join(section(SKILL.read_text(), "### Verification at the end of the chain").split())

    assert "`economy`" in text and "one verifier runs for each stack" in text
