"""Each failure mode has a failing case and the clean rendered set as its passing case."""

import pytest
import yaml

from delegate.validate import (
    Finding,
    opencode_bash_action,
    opencode_pattern_matches,
    split_frontmatter,
    validate_claude_code_dir,
    validate_opencode_dir,
    validate_set,
)



def _codes(findings):
    return sorted(f.code for f in findings)


def _rewrite(path, old, new):
    text = path.read_text()
    assert old in text, f"{old!r} not in {path.name}"
    path.write_text(text.replace(old, new, 1))


def test_clean_rendered_set_passes(rendered, tiers):
    assert validate_set(tiers, rendered / "claude-code", rendered / "opencode") == []


def test_unknown_key_fails_claude_code(rendered, tiers):
    p = rendered / "claude-code" / "java-spring-verifier.md"
    _rewrite(p, "name: java-spring-verifier\n", "name: java-spring-verifier\nreadonly: true\n")
    f = validate_claude_code_dir(rendered / "claude-code", tiers)
    assert "UNKNOWN_KEY" in _codes(f)
    assert any("readonly" in x.message for x in f)


def test_unknown_key_fails_opencode(rendered, tiers):
    p = rendered / "opencode" / "java-spring-specialist.md"
    _rewrite(p, "mode: subagent\n", "mode: subagent\nskills:\n- tdd\n")
    assert "UNKNOWN_KEY" in _codes(validate_opencode_dir(rendered / "opencode", tiers))


def test_invalid_model_fails_claude_code(rendered, tiers):
    p = rendered / "claude-code" / "java-spring-specialist.md"
    _rewrite(p, "name: java-spring-specialist\n", "name: java-spring-specialist\nmodel: claude-4-opus-high\n")
    assert "BAD_MODEL" in _codes(validate_claude_code_dir(rendered / "claude-code", tiers))


def test_valid_model_passes_claude_code(rendered, tiers):
    p = rendered / "claude-code" / "java-spring-specialist.md"
    _rewrite(p, "name: java-spring-specialist\n", "name: java-spring-specialist\nmodel: sonnet\n")
    assert "BAD_MODEL" not in _codes(validate_claude_code_dir(rendered / "claude-code", tiers))


def test_invalid_model_fails_opencode(rendered, tiers):
    p = rendered / "opencode" / "java-spring-specialist.md"
    _rewrite(p, "model: anthropic/claude-sonnet-5", "model: anthropic/claude-sonnet-4")
    assert "BAD_MODEL" in _codes(validate_opencode_dir(rendered / "opencode", tiers))


def test_missing_verifier_twin_fails(rendered, tiers):
    (rendered / "claude-code" / "java-spring-verifier.md").unlink()
    f = validate_claude_code_dir(rendered / "claude-code", tiers)
    assert "MISSING_TWIN" in _codes(f)


def test_missing_specialist_twin_fails_opencode(rendered, tiers):
    (rendered / "opencode" / "java-spring-specialist.md").unlink()
    assert "MISSING_TWIN" in _codes(validate_opencode_dir(rendered / "opencode", tiers))


def test_duplicate_name_fails_claude_code(rendered, tiers):
    src = rendered / "claude-code" / "java-spring-specialist.md"
    (rendered / "claude-code" / "copy.md").write_text(src.read_text())
    f = validate_claude_code_dir(rendered / "claude-code", tiers)
    assert "DUPLICATE_NAME" in _codes(f)


def test_duplicate_name_fails_claude_code_even_with_different_filenames(rendered, tiers):
    # Claude Code identity is the frontmatter name, not the filename, so a
    # second file with a different stem and the same name is a collision the
    # harness resolves by read order.
    src = rendered / "claude-code" / "java-spring-verifier.md"
    (rendered / "claude-code" / "review-verifier-copy.md").write_text(src.read_text())
    f = validate_claude_code_dir(rendered / "claude-code", tiers)
    assert [x for x in f if x.code == "DUPLICATE_NAME" and "java-spring-verifier" in x.message]


def test_verifier_with_write_tool_fails(rendered, tiers):
    p = rendered / "claude-code" / "java-spring-verifier.md"
    _rewrite(p, "tools: Read, Grep, Glob", "tools: Read, Grep, Glob, Edit")
    f = validate_claude_code_dir(rendered / "claude-code", tiers)
    assert "VERIFIER_WRITE_TOOL" in _codes(f)


def test_verifier_with_unguarded_bash_fails(rendered, tiers):
    # Bash on a verifier is legal only with the guard hook beside it.
    p = rendered / "claude-code" / "java-spring-verifier.md"
    _edit_frontmatter(p, lambda fm: fm.pop("hooks"))
    codes = _codes(validate_claude_code_dir(rendered / "claude-code", tiers))
    assert "VERIFIER_BASH_UNGUARDED" in codes
    assert "VERIFIER_WRITE_TOOL" in codes


def test_guarded_verifier_bash_is_not_a_write_tool(rendered, tiers):
    f = validate_claude_code_dir(rendered / "claude-code", tiers)
    assert [x for x in f if x.code in ("VERIFIER_WRITE_TOOL", "VERIFIER_BASH_UNGUARDED")] == []


def test_verifier_bash_hook_without_the_guard_marker_is_not_a_guard(rendered, tiers):
    # A hook that exits 0 is a hook, not a guard. The marker is the evidence.
    p = rendered / "claude-code" / "java-spring-verifier.md"

    def blank(fm):
        fm["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "exit 0"

    _edit_frontmatter(p, blank)
    codes = _codes(validate_claude_code_dir(rendered / "claude-code", tiers))
    assert "VERIFIER_BASH_UNGUARDED" in codes
    assert "VERIFIER_WRITE_TOOL" in codes


def test_verifier_guard_on_another_tool_is_not_a_guard(rendered, tiers):
    p = rendered / "claude-code" / "java-spring-verifier.md"

    def retarget(fm):
        fm["hooks"]["PreToolUse"][0]["matcher"] = "Write"

    _edit_frontmatter(p, retarget)
    assert "VERIFIER_BASH_UNGUARDED" in _codes(validate_claude_code_dir(rendered / "claude-code", tiers))


def test_verifier_with_edit_fails_even_when_bash_is_guarded(rendered, tiers):
    p = rendered / "claude-code" / "java-spring-verifier.md"
    _rewrite(p, "tools: Read, Grep, Glob, Bash", "tools: Read, Grep, Glob, Bash, Edit")
    f = validate_claude_code_dir(rendered / "claude-code", tiers)
    assert "VERIFIER_WRITE_TOOL" in _codes(f)
    assert "VERIFIER_BASH_UNGUARDED" not in _codes(f)
    assert any("Edit" in x.message for x in f if x.code == "VERIFIER_WRITE_TOOL")


def test_opencode_verifier_bash_map_must_open_with_a_catch_all_deny(rendered, tiers):
    p = rendered / "opencode" / "java-spring-verifier.md"
    _edit_frontmatter(
        p,
        lambda fm: fm.__setitem__(
            "permission", {"*": "deny", "read": "allow", "bash": {"git diff *": "allow", "*": "deny"}}
        ),
    )
    codes = _codes(validate_opencode_dir(rendered / "opencode", tiers))
    assert "VERIFIER_WRITE_TOOL" in codes
    assert "RULE_SHADOWED" in codes


def test_opencode_verifier_bash_map_that_allows_a_write_command_fails(rendered, tiers):
    for pattern in ("git push*", "git commit*", "rm*"):
        p = rendered / "opencode" / "java-spring-verifier.md"
        _edit_frontmatter(
            p,
            lambda fm, pattern=pattern: fm.__setitem__(
                "permission",
                {"*": "deny", "read": "allow", "bash": {"*": "deny", "git diff *": "allow", pattern: "allow"}},
            ),
        )
        f = validate_opencode_dir(rendered / "opencode", tiers)
        assert "VERIFIER_WRITE_TOOL" in _codes(f), pattern


def test_opencode_verifier_bash_map_with_a_catch_all_allow_fails(rendered, tiers):
    p = rendered / "opencode" / "java-spring-verifier.md"
    _edit_frontmatter(
        p,
        lambda fm: fm.__setitem__("permission", {"*": "deny", "read": "allow", "bash": {"*": "allow"}}),
    )
    assert "VERIFIER_WRITE_TOOL" in _codes(validate_opencode_dir(rendered / "opencode", tiers))


def test_opencode_verifier_keeps_the_outer_catch_all_deny_first(rendered, tiers):
    # The bash map is a second, nested catch-all. It does not replace the outer
    # one: without the outer deny every other tool is allowed again.
    p = rendered / "opencode" / "java-spring-verifier.md"
    _edit_frontmatter(
        p,
        lambda fm: fm.__setitem__("permission", {"read": "allow", "*": "deny", "bash": {"*": "deny"}}),
    )
    assert "VERIFIER_NOT_READONLY" in _codes(validate_opencode_dir(rendered / "opencode", tiers))


def test_verifier_without_tools_allowlist_fails(rendered, tiers):
    p = rendered / "claude-code" / "java-spring-verifier.md"
    _rewrite(p, "tools: Read, Grep, Glob, Bash\n", "")
    assert "VERIFIER_NOT_READONLY" in _codes(validate_claude_code_dir(rendered / "claude-code", tiers))


def test_opencode_verifier_deny_must_come_first(rendered, tiers):
    p = rendered / "opencode" / "java-spring-verifier.md"
    text = p.read_text()
    text = text.replace("permission:\n  '*': deny\n  read: allow\n", "permission:\n  read: allow\n  '*': deny\n")
    p.write_text(text)
    assert "VERIFIER_NOT_READONLY" in _codes(validate_opencode_dir(rendered / "opencode", tiers))


def test_opencode_verifier_allowing_edit_fails(rendered, tiers):
    p = rendered / "opencode" / "java-spring-verifier.md"
    _rewrite(p, "  list: allow\n", "  list: allow\n  edit: allow\n")
    assert "VERIFIER_WRITE_TOOL" in _codes(validate_opencode_dir(rendered / "opencode", tiers))


def test_opencode_verifier_ask_counts_as_write(rendered, tiers):
    # "--auto" turns every ask into allow, so a read-only agent is built from
    # deny. A bare "bash: ask" replaces the guarded map and must be a finding.
    p = rendered / "opencode" / "java-spring-verifier.md"
    _edit_frontmatter(p, lambda fm: fm["permission"].__setitem__("bash", "ask"))
    assert "VERIFIER_WRITE_TOOL" in _codes(validate_opencode_dir(rendered / "opencode", tiers))


def test_bad_effort_fails_and_good_effort_passes(rendered, tiers):
    p = rendered / "claude-code" / "java-spring-specialist.md"
    _rewrite(p, "name: java-spring-specialist\n", "name: java-spring-specialist\neffort: turbo\n")
    assert "BAD_EFFORT" in _codes(validate_claude_code_dir(rendered / "claude-code", tiers))
    _rewrite(p, "effort: turbo\n", "effort: high\n")
    assert "BAD_EFFORT" not in _codes(validate_claude_code_dir(rendered / "claude-code", tiers))


def test_cross_harness_mismatch(rendered, tiers):
    (rendered / "opencode" / "java-spring-verifier.md").unlink()
    codes = _codes(validate_set(tiers, rendered / "claude-code", rendered / "opencode"))
    assert "CROSS_HARNESS_MISMATCH" in codes


def test_missing_skill_detected_against_skills_dir(rendered, tiers, tmp_path):
    skills = tmp_path / "skills"
    for s in ("clean-ddd-hexagonal", "api-design-principles", "tdd", "oauth2-resource-server"):
        (skills / s).mkdir(parents=True)
        (skills / s / "SKILL.md").write_text("---\nname: x\n---\n")
    f = validate_set(tiers, rendered / "claude-code", None, skills_dir=skills)
    assert [x for x in f if x.code == "MISSING_SKILL" and "test-quality" in x.message]
    (skills / "test-quality").mkdir()
    (skills / "test-quality" / "SKILL.md").write_text("---\nname: x\n---\n")
    assert validate_set(tiers, rendered / "claude-code", None, skills_dir=skills) == []


def test_skill_in_user_dir_only_produces_no_missing_skill_finding(rendered, tiers, tmp_path):
    """A skill present in the user directory but not the project directory passes.

    The harness also loads the skills in the user directory, so such a skill is
    not missing."""
    project = tmp_path / "project-skills"
    user = tmp_path / "user-skills"
    # The rendered java-spring agents preload: clean-ddd-hexagonal, api-design-principles, tdd, test-quality, oauth2-resource-server.
    # Put four in the project dir and one (test-quality) only in the user dir.
    for s in ("clean-ddd-hexagonal", "api-design-principles", "tdd", "oauth2-resource-server"):
        (project / s).mkdir(parents=True)
        (project / s / "SKILL.md").write_text("---\nname: x\n---\n")
    (user / "test-quality").mkdir(parents=True)
    (user / "test-quality" / "SKILL.md").write_text("---\nname: x\n---\n")
    f = validate_set(tiers, rendered / "claude-code", None, skills_dir=project, user_skills_dir=user)
    assert [x for x in f if x.code == "MISSING_SKILL"] == []


def test_skill_absent_from_both_dirs_produces_missing_skill_naming_both(rendered, tiers, tmp_path):
    """A skill absent from both directories produces MISSING_SKILL that names both."""
    project = tmp_path / "project-skills"
    user = tmp_path / "user-skills"
    for s in ("clean-ddd-hexagonal", "api-design-principles", "tdd", "oauth2-resource-server"):
        (project / s).mkdir(parents=True)
        (project / s / "SKILL.md").write_text("---\nname: x\n---\n")
    user.mkdir(parents=True)
    # test-quality is absent from both dirs.
    f = validate_set(tiers, rendered / "claude-code", None, skills_dir=project, user_skills_dir=user)
    missing = [x for x in f if x.code == "MISSING_SKILL" and "test-quality" in x.message]
    assert missing, "test-quality must produce MISSING_SKILL"
    assert str(project) in missing[0].message, "the finding must name the project directory"
    assert str(user) in missing[0].message, "the finding must name the user directory"


def test_user_skills_dir_none_checks_only_project_dir(rendered, tiers, tmp_path):
    """Passing user_skills_dir=None keeps the old behaviour: only the project dir is checked."""
    project = tmp_path / "project-skills"
    for s in ("clean-ddd-hexagonal", "api-design-principles", "tdd", "oauth2-resource-server"):
        (project / s).mkdir(parents=True)
        (project / s / "SKILL.md").write_text("---\nname: x\n---\n")
    # test-quality is absent; user_skills_dir is None.
    f = validate_set(tiers, rendered / "claude-code", None, skills_dir=project, user_skills_dir=None)
    missing = [x for x in f if x.code == "MISSING_SKILL" and "test-quality" in x.message]
    assert missing, "test-quality must produce MISSING_SKILL when user_skills_dir is None"
    # The message must name only the project directory, not "None".
    assert str(project) in missing[0].message
    assert "None" not in missing[0].message


def test_description_budget(rendered, tiers):
    from dataclasses import replace

    tight = replace(tiers, description_budget_chars=50)
    assert "DESCRIPTION_BUDGET" in _codes(validate_claude_code_dir(rendered / "claude-code", tight))


def test_frontmatter_not_on_first_line_is_reported(rendered, tiers):
    p = rendered / "claude-code" / "java-spring-verifier.md"
    p.write_text("\n" + p.read_text())
    assert "BAD_FRONTMATTER" in _codes(validate_claude_code_dir(rendered / "claude-code", tiers))


def test_declaration_rejects_unknown_key():
    from delegate.declaration import parse_declaration

    with pytest.raises(ValueError, match="unknown keys"):
        parse_declaration({"agents": {"x": {"description": "d", "model": "opus"}}})


def test_opencode_shadowed_deny_in_pattern_map_fails(rendered, tiers):
    p = rendered / "opencode" / "java-spring-specialist.md"
    text = p.read_text()
    fixed = "permission:\n  bash:\n    '*': allow\n    git push*: deny\n    git -C * push*: deny\n"
    inverted = "permission:\n  bash:\n    git push*: deny\n    '*': allow\n    git -C * push*: deny\n"
    assert fixed in text, "renderer must emit the catch-all first"
    assert "RULE_SHADOWED" not in _codes(validate_opencode_dir(rendered / "opencode", tiers))
    p.write_text(text.replace(fixed, inverted, 1))
    f = validate_opencode_dir(rendered / "opencode", tiers)
    assert "RULE_SHADOWED" in _codes(f)
    assert any("git push*" in x.message for x in f)


# --- The specialist push guard (ADR 0002) -------------------------------------------


def _edit_frontmatter(path, mutate):
    """Rewrite one file's frontmatter. The body is untouched."""
    fm, body, err = split_frontmatter(path.read_text())
    assert err is None, err
    mutate(fm)
    dumped = yaml.safe_dump(fm, sort_keys=False, default_flow_style=False, allow_unicode=True, width=1000)
    path.write_text("---\n" + dumped + "---\n" + body)


def test_opencode_wildcard_matches_the_documented_glob_examples():
    # The examples come from https://opencode.ai/docs/permissions/ (checked
    # 2026-09-19): "*" is zero or more characters, "?" is one character, and a
    # bare command name does not match the same command with arguments.
    assert opencode_pattern_matches("git status --porcelain", "git *")
    assert opencode_pattern_matches("grep pattern file.txt", "grep *")
    assert not opencode_pattern_matches("grep pattern file.txt", "grep")
    assert opencode_pattern_matches("anything at all", "*")
    assert opencode_pattern_matches("rm -rf /", "rm ?rf /")
    assert not opencode_pattern_matches("git push", "git pull*")


def test_opencode_last_matching_rule_wins_over_an_earlier_catch_all():
    # The catch-all allows, the later deny wins. Reverse the order and the
    # catch-all wins instead, which is the shadowing trap.
    guarded = {"bash": {"*": "allow", "git push*": "deny"}}
    shadowed = {"bash": {"git push*": "deny", "*": "allow"}}
    assert opencode_bash_action(guarded, "git push origin main") == "deny"
    assert opencode_bash_action(guarded, "git status") == "allow"
    assert opencode_bash_action(shadowed, "git push origin main") == "allow"


def test_claude_code_specialist_without_a_bash_hook_can_push(rendered, tiers):
    p = rendered / "claude-code" / "java-spring-specialist.md"
    _edit_frontmatter(p, lambda fm: fm.pop("hooks"))
    f = validate_claude_code_dir(rendered / "claude-code", tiers)
    assert "SPECIALIST_CAN_PUSH" in _codes(f)
    assert any("java-spring-specialist" in x.file for x in f if x.code == "SPECIALIST_CAN_PUSH")


def test_claude_code_specialist_hook_that_never_names_push_is_not_a_guard(rendered, tiers):
    p = rendered / "claude-code" / "java-spring-specialist.md"

    def blank(fm):
        fm["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "exit 0"

    _edit_frontmatter(p, blank)
    assert "SPECIALIST_CAN_PUSH" in _codes(validate_claude_code_dir(rendered / "claude-code", tiers))


def test_claude_code_push_hook_on_another_tool_is_not_a_guard(rendered, tiers):
    p = rendered / "claude-code" / "java-spring-specialist.md"

    def retarget(fm):
        fm["hooks"]["PreToolUse"][0]["matcher"] = "Write"

    _edit_frontmatter(p, retarget)
    assert "SPECIALIST_CAN_PUSH" in _codes(validate_claude_code_dir(rendered / "claude-code", tiers))


def test_claude_code_verifier_needs_no_push_hook(rendered, tiers):
    # The verifier has no Bash tool, so the guard does not apply to it.
    f = validate_claude_code_dir(rendered / "claude-code", tiers)
    assert [x for x in f if x.code == "SPECIALIST_CAN_PUSH"] == []


def test_opencode_specialist_without_a_push_deny_can_push(rendered, tiers):
    p = rendered / "opencode" / "java-spring-specialist.md"
    _edit_frontmatter(p, lambda fm: fm.__setitem__("permission", {"bash": {"*": "allow"}}))
    f = validate_opencode_dir(rendered / "opencode", tiers)
    assert "SPECIALIST_CAN_PUSH" in _codes(f)


def test_opencode_specialist_push_deny_shadowed_by_the_catch_all_is_not_a_guard(rendered, tiers):
    p = rendered / "opencode" / "java-spring-specialist.md"
    _edit_frontmatter(
        p,
        lambda fm: fm.__setitem__("permission", {"bash": {"git push*": "deny", "*": "allow"}}),
    )
    codes = _codes(validate_opencode_dir(rendered / "opencode", tiers))
    assert "SPECIALIST_CAN_PUSH" in codes
    assert "RULE_SHADOWED" in codes


def test_opencode_guard_accepts_a_different_spelling_of_the_push_pattern(rendered, tiers):
    # A space before the star is a second spelling of the same two denies. The
    # check resolves the map the way OpenCode does, so the spelling is free.
    p = rendered / "opencode" / "java-spring-specialist.md"
    _edit_frontmatter(
        p,
        lambda fm: fm.__setitem__(
            "permission",
            {"bash": {"*": "allow", "git push *": "deny", "git -C * push *": "deny"}},
        ),
    )
    assert "SPECIALIST_CAN_PUSH" not in _codes(validate_opencode_dir(rendered / "opencode", tiers))


def test_opencode_deny_on_a_different_git_command_is_not_a_push_guard(rendered, tiers):
    p = rendered / "opencode" / "java-spring-specialist.md"
    _edit_frontmatter(
        p,
        lambda fm: fm.__setitem__("permission", {"bash": {"*": "allow", "git pull*": "deny"}}),
    )
    assert "SPECIALIST_CAN_PUSH" in _codes(validate_opencode_dir(rendered / "opencode", tiers))


def test_a_trailing_star_pattern_also_matches_the_bare_command():
    # OpenCode compiles "git push *" to "^git push .*$", and then treats the
    # trailing " *" as optional, so the pattern also covers the bare command.
    # Without that special case a specialist can run "git push" past the deny.
    assert opencode_pattern_matches("git push", "git push *")
    assert opencode_pattern_matches("git push origin main", "git push *")
    assert not opencode_pattern_matches("git pushover", "git push *")


def test_opencode_guard_needs_the_widened_pattern_for_another_repository_path(rendered, tiers):
    # "git push*" compiles to "^git push.*$" and leaves "git -C <path> push"
    # allowed. The Claude Code hook stops that form, so the validator must not
    # accept the narrow deny as a push guard.
    p = rendered / "opencode" / "java-spring-specialist.md"
    _edit_frontmatter(
        p,
        lambda fm: fm.__setitem__("permission", {"bash": {"*": "allow", "git push*": "deny"}}),
    )
    f = validate_opencode_dir(rendered / "opencode", tiers)
    assert "SPECIALIST_CAN_PUSH" in _codes(f)
    assert opencode_bash_action({"bash": {"*": "allow", "git push*": "deny"}}, "git -C /tmp/x push") == "allow"


def test_a_finding_with_a_code_that_the_reference_does_not_list_is_an_error_that_names_the_code():
    """A code outside the documented set would be missing from the reference, so the validator refuses to write it."""
    with pytest.raises(ValueError, match="NOT_A_CODE"):
        Finding("NOT_A_CODE", "agent.md", "x")
