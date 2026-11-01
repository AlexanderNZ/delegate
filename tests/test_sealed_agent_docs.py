"""The docs of the sealed agent: ADR 0007, the limits page, the protocol skill and the changelog.

The measurements are those of issues 187 and 213. A page must give them with the
version of the harness and the command, and no page may say that a project agent
loads its guard in an engine session. The pages are outside the package source,
so a Nix build that copies only the package skips these cases with a reason.
"""

import re

from .pages import ROOT, outside_the_package

ADR_DIRECTORY = ROOT / "docs" / "adr"
LIMITS_PAGE = ROOT / "docs" / "explanation" / "the-enforcement-model-and-its-limits.md"
INDEX_PAGE = ROOT / "docs" / "explanation" / "decision-records.md"
SKILL = ROOT / "skills" / "agent-delegation" / "SKILL.md"
CHANGELOG = ROOT / "CHANGELOG.md"
TITLE = "ADR 0007: The engine runs each agent from a sealed agent, outside the tree under review"


def section(text: str, heading: str, level: int = 2) -> str:
    """The text under a heading, up to the next heading of the same level or a higher one."""
    marks = "#" * level
    match = re.search(rf"^{marks} {re.escape(heading)}\n(.*?)(?=^#{{1,{level}}} |\Z)", text, flags=re.MULTILINE | re.DOTALL)
    assert match, f"no section {heading!r}"
    return match.group(1)


def plain(text: str) -> str:
    """The text with each glossary link read as its words, and the line breaks of a list item joined."""
    return re.sub(r"\s+", " ", re.sub(r"\[\*\*([^*\]]+)\*\*\]\([^)]*\)", r"\1", text))


def adr_0007() -> str:
    found = sorted(ADR_DIRECTORY.glob("0007-*.md"))
    assert len(found) == 1, "docs/adr/ holds exactly one ADR 0007"
    return found[0].read_text()


@outside_the_package
def test_adr_0007_has_the_title_of_the_ticket_and_the_sections_of_an_adr():
    text = adr_0007()
    assert text.splitlines()[0] == f"# {TITLE}"
    assert re.search(r"^- Status: accepted$", text, flags=re.MULTILINE)
    assert re.search(r"^- Date: 2026-11-01$", text, flags=re.MULTILINE)
    for heading in ("Context", "Decision", "Consequences"):
        assert f"\n## {heading}\n" in text


@outside_the_package
def test_adr_0007_names_the_five_measured_options_and_the_choice():
    options = section(adr_0007(), "The options")
    items = re.findall(r"^\d\. (.+)$", options, flags=re.MULTILINE)
    assert len(items) == 5
    text = plain(options)
    for flag in ("--agents", "--settings", "--setting-sources user", "--plugin-dir"):
        assert flag in text
    decision = plain(section(adr_0007(), "Decision"))
    assert "plugin" in decision and "--plugin-dir" in decision


@outside_the_package
def test_adr_0007_says_that_the_project_settings_and_the_project_claude_md_are_dropped_on_purpose():
    text = plain(adr_0007())
    assert re.search(r"project settings.*on purpose", text)
    assert "CLAUDE.md" in text and "DELEGATE_AGENT_SETTINGS" in text
    assert "2.1.287" in text


@outside_the_package
def test_adr_0007_gives_the_opencode_limit_of_the_decision_d_e():
    text = plain(adr_0007())
    assert "1.15.10" in text
    assert re.search(r"enforcement of (its|the) permission map.*not measured", text)


@outside_the_package
def test_the_adr_index_lists_adr_0007_with_its_measurement():
    text = INDEX_PAGE.read_text()
    assert f"[{TITLE}](https://github.com/AlexanderNZ/delegate/blob/main/docs/adr/0007-" in text
    assert "2.1.287" in plain(text.split("ADR 0007")[1])


@outside_the_package
def test_the_limits_page_lists_the_sealed_agent_and_the_guard_probe_among_the_checks_of_the_engine():
    text = plain(section(LIMITS_PAGE.read_text(), "What the engine enforces"))
    assert "It has seven checks." in text
    assert re.search(r"\*\*One seal for each run\.\*\*", text)
    assert re.search(r"\*\*A probe before the first session\.\*\*", text)
    assert "ADR 0007" in text


@outside_the_package
def test_the_limits_page_no_longer_says_that_the_verifier_loads_a_project_agent_from_the_copy():
    text = plain(section(LIMITS_PAGE.read_text(), "What the engine does not stop"))
    assert "load a project agent from the copy" not in text
    assert "#188" not in text
    assert re.search(r"state directory.*the seal", text)
    assert re.search(r"detects a change.*does not prevent the change", text)


@outside_the_package
def test_the_limits_page_corrects_limit_24_the_hooks_of_a_project_agent_do_not_run_under_print():
    items = re.findall(r"^\d+\. (.+(?:\n   .+)*)", section(LIMITS_PAGE.read_text(), "The limits of the guards"), flags=re.MULTILINE)
    limit = plain(items[23])
    assert "workspace trust" in limit.lower()
    assert re.search(r"do not run under `--print`, also in a trusted directory", limit)
    assert "2.1.287" in limit
    assert "A project verifier in a folder that is not trusted has a Bash tool with no guard" not in limit


@outside_the_package
def test_the_limits_page_gives_the_results_of_187_for_each_harness_with_versions_and_commands():
    text = LIMITS_PAGE.read_text()
    results = section(text, "What the harnesses do with a project agent")
    claude, opencode = section(results, "Claude Code", 3), section(results, "OpenCode", 3)
    assert "2.1.287" in claude and "1.15.10" in opencode
    assert "claude --print --output-format stream-json --verbose" in claude
    assert "--agent m187-probe" in claude
    assert "opencode debug agent" in opencode
    assert "(#187)" in plain(results)
    assert re.search(r"permission rules of a project agent.*after the rules of the user agent", plain(opencode))


@outside_the_package
def test_the_limits_page_names_the_limit_of_decision_d_e_on_opencode():
    text = plain(LIMITS_PAGE.read_text())
    assert re.search(r"resolution of the sealed agent was measured.*enforcement of its permission map in a session was not", text)


@outside_the_package
def test_the_limits_page_links_adr_0007_and_the_link_resolves():
    links = re.findall(r"\]\((https://github\.com/AlexanderNZ/delegate/blob/main/(docs/adr/0007-[^)\s]+\.md))\)", LIMITS_PAGE.read_text())
    assert links and all((ROOT / path).is_file() for _, path in links)


@outside_the_package
def test_no_page_says_that_a_project_agent_loads_its_guard_in_an_engine_session():
    stale = [
        str(path.relative_to(ROOT))
        for path in [*(ROOT / "docs").rglob("*.md"), *(ROOT / "skills").rglob("*.md")]
        if re.search(r"verifier can load a project agent from the copy|run only after the workspace trust dialog is accepted", plain(path.read_text()))
    ]
    assert stale == []


@outside_the_package
def test_the_protocol_skill_says_that_a_delegate_run_session_uses_the_sealed_agent():
    text = plain(SKILL.read_text())
    assert re.search(r"A `delegate run` session uses the sealed agent", text)
    assert re.search(r"committed pair stays for the Agent tool", text)


@outside_the_package
def test_the_protocol_skill_limits_the_preload_note_to_a_subagent_of_the_agent_tool():
    block = plain(section(SKILL.read_text(), "Confirm the permission mode before you spawn a pair agent", 3))
    assert re.search(r"applies to a subagent of the Agent tool, not to a `--agent` session", block)
    assert "#215" in block


@outside_the_package
def test_the_unreleased_changelog_has_a_changed_entry_that_names_220_and_187():
    unreleased = section(CHANGELOG.read_text(), "[Unreleased]")
    changed = section(unreleased, "Changed", 3)
    entries = [entry for entry in re.split(r"^- ", changed, flags=re.MULTILINE) if "(#220" in entry]
    assert len(entries) == 1
    assert "#187" in entries[0]
