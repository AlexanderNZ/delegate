"""One loop validates the agent files of each harness; the rules of a harness sit beside it (#364).

`validate_dir(d, tiers, harness)` is the loop. The order of the findings of a file is part of the interface: a person
reads them top down, and the finding tables of the docs follow the same order. Each case writes a directory with a
defect for each step of the loop and reads the codes in order. The expected lists are literals of the order that the
validator has had since #1.
"""

from __future__ import annotations

import pytest

from delegate.definitions.validate import validate_claude_code_dir, validate_dir, validate_opencode_dir


def _write(directory, name, text):
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(text)


def _pairs(findings):
    return [(f.code, f.file) for f in findings]


def _claude_code_directory(tmp_path, tiers):
    d = tmp_path / "claude-code"
    # Each defect sits at one step of the loop: name, description, key, model, effort, then the push guard.
    _write(d, "01.md", "---\nname: -bad-specialist\nfoo: 1\nmodel: gpt-nope\neffort: bogus\n---\nbody\n")
    # A verifier with no allowlist, and a description that spends the whole budget.
    _write(d, "02.md", f"---\nname: big-verifier\ndescription: {'x' * (tiers.description_budget_chars + 1)}\n---\nbody\n")
    # The same name again: the duplicate comes before the verifier findings of the file.
    _write(d, "03.md", "---\nname: big-verifier\ndescription: y\n---\nbody\n")
    _write(d, "04.md", "---\ndescription: y\n---\nbody\n")
    _write(d, "05.md", "no frontmatter\n")
    return d


def _opencode_directory(tmp_path):
    d = tmp_path / "opencode"
    # Each defect sits at one step of the loop: description, key, model, variant, mode, tools, shadowed rule, push guard.
    _write(
        d,
        "a-specialist.md",
        "---\nfoo: 1\nmodel: gpt-nope\nvariant: bogus\nmode: subagent\ntools: edit\n"
        "permission:\n  bash:\n    git push*: deny\n    '*': allow\n---\nbody\n",
    )
    _write(d, "b-verifier.md", "---\ndescription: y\n---\nbody\n")
    _write(d, "c.md", "no frontmatter\n")
    return d


def test_claude_code_findings_come_in_the_order_of_the_steps_of_the_loop(tmp_path, tiers):
    findings = validate_dir(_claude_code_directory(tmp_path, tiers), tiers, "claude-code")

    claude_dir = str(tmp_path / "claude-code")
    assert _pairs(findings) == [
        ("NAME_INVALID", "01.md"),
        ("MISSING_DESCRIPTION", "01.md"),
        ("UNKNOWN_KEY", "01.md"),
        ("BAD_MODEL", "01.md"),
        ("BAD_EFFORT", "01.md"),
        ("SPECIALIST_CAN_PUSH", "01.md"),
        ("VERIFIER_NOT_READONLY", "02.md"),
        ("DUPLICATE_NAME", "03.md"),
        ("VERIFIER_NOT_READONLY", "03.md"),
        ("MISSING_NAME", "04.md"),
        ("BAD_FRONTMATTER", "05.md"),
        ("MISSING_TWIN", claude_dir),
        ("MISSING_TWIN", claude_dir),
        ("DESCRIPTION_BUDGET", claude_dir),
    ]


def test_opencode_findings_come_in_the_order_of_the_steps_of_the_loop(tmp_path, tiers):
    findings = validate_dir(_opencode_directory(tmp_path), tiers, "opencode")

    opencode_dir = str(tmp_path / "opencode")
    assert _pairs(findings) == [
        ("MISSING_DESCRIPTION", "a-specialist.md"),
        ("UNKNOWN_KEY", "a-specialist.md"),
        ("BAD_MODEL", "a-specialist.md"),
        ("BAD_EFFORT", "a-specialist.md"),
        ("SUBAGENT_MODE", "a-specialist.md"),
        ("BAD_TYPE", "a-specialist.md"),
        ("RULE_SHADOWED", "a-specialist.md"),
        ("SPECIALIST_CAN_PUSH", "a-specialist.md"),
        ("VERIFIER_NOT_READONLY", "b-verifier.md"),
        ("BAD_FRONTMATTER", "c.md"),
        ("MISSING_TWIN", opencode_dir),
        ("MISSING_TWIN", opencode_dir),
    ]


def test_opencode_has_no_description_budget_and_names_an_agent_by_its_file(tmp_path, tiers):
    d = tmp_path / "opencode"
    _write(d, "big.md", f"---\ndescription: {'x' * (tiers.description_budget_chars + 1)}\n---\nbody\n")
    # The name key does not name the agent on OpenCode: the file name does, so this file is no duplicate of big.md.
    _write(d, "other.md", "---\nname: big\ndescription: y\n---\nbody\n")

    assert validate_dir(d, tiers, "opencode") == []


def test_the_two_named_functions_give_what_validate_dir_gives(tmp_path, tiers):
    claude_dir = _claude_code_directory(tmp_path, tiers)
    opencode_dir = _opencode_directory(tmp_path)

    assert validate_claude_code_dir(claude_dir, tiers) == validate_dir(claude_dir, tiers, "claude-code")
    assert validate_opencode_dir(opencode_dir, tiers) == validate_dir(opencode_dir, tiers, "opencode")


def test_an_unknown_harness_is_refused_with_the_known_names(tmp_path, tiers):
    with pytest.raises(ValueError, match="claude-code.*opencode"):
        validate_dir(tmp_path, tiers, "cursor")
