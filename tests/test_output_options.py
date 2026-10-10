"""The two declaration options that keep a consumer's values out of the kit.

`outputLanguage` selects the language rule in the Report section of each
agent file. `trackedFileBuild` adds the flake reason to the staging rule of
the Claude Code specialist. Both default to the neutral form: no language
rule and no flake reason.
"""

import pytest

from delegate.definitions.declaration import parse_declaration
from delegate.definitions.render import render
from delegate.shared.tiers import load_tiers

STE = "Write in ASD-STE100 Simplified Technical English."
STAGE_RULE = "- Stage every new file with `git add` before you run the gates."
FLAKE_REASON = "An unstaged file is invisible to a flake."


def _render(**options):
    spec = {"description": "Widget services.", "skills": ["tdd"]}
    spec.update(options)
    agents = parse_declaration({"agents": {"widget": spec}})
    return render(agents, load_tiers())


def _files(out):
    """Each rendered file by harness and name."""
    return {
        (harness, name): text for harness, files in out.items() for name, text in files.items()
    }


def _report(text):
    return text.split("## Report\n\n", 1)[1]


def test_a_declaration_with_neither_option_renders_no_language_rule_and_no_flake_reason():
    files = _files(_render())
    assert len(files) == 4
    for key, text in files.items():
        assert "ASD-STE100" not in text, key
        assert "invisible to a flake" not in text, key


def test_without_a_language_the_report_section_opens_with_its_content():
    files = _files(_render())
    assert _report(files[("claude-code", "widget-specialist.md")]).startswith(
        "Include: the branch and worktree path;"
    )
    assert _report(files[("opencode", "widget-specialist.md")]).startswith(
        "Include: the branch and worktree path;"
    )
    assert _report(files[("claude-code", "widget-verifier.md")]).startswith(
        "Sections: Verdict (one line);"
    )
    assert _report(files[("opencode", "widget-verifier.md")]).startswith(
        "Sections: Verdict (one line);"
    )


def test_without_the_flake_reason_each_specialist_still_stages_new_files():
    files = _files(_render())
    for harness in ("claude-code", "opencode"):
        lines = files[(harness, "widget-specialist.md")].splitlines()
        assert STAGE_RULE in lines, harness


def test_a_declaration_with_both_options_renders_the_ste_rule_and_the_flake_reason():
    files = _files(_render(outputLanguage="ste", trackedFileBuild=True))
    cc_spec = files[("claude-code", "widget-specialist.md")]
    assert _report(cc_spec).startswith(f"{STE} Include: the branch and worktree path;")
    assert f"{STAGE_RULE} {FLAKE_REASON}" in cc_spec.splitlines()
    assert _report(files[("opencode", "widget-specialist.md")]).startswith(
        f"{STE} Include: the branch and worktree path;"
    )
    for harness in ("claude-code", "opencode"):
        assert _report(files[(harness, "widget-verifier.md")]).startswith(
            f"{STE} Sections: Verdict (one line);"
        ), harness


@pytest.mark.parametrize(
    "language, tracked, want_ste, want_reason",
    [
        ("none", False, False, False),
        ("none", True, False, True),
        ("ste", False, True, False),
        ("ste", True, True, True),
    ],
)
def test_each_option_controls_only_its_own_line(language, tracked, want_ste, want_reason):
    files = _files(_render(outputLanguage=language, trackedFileBuild=tracked))
    for key, text in files.items():
        assert (STE in text) is want_ste, key
    cc_spec = files[("claude-code", "widget-specialist.md")]
    assert (FLAKE_REASON in cc_spec) is want_reason
    # The verifiers and the OpenCode specialist never carry the flake reason.
    for key, text in files.items():
        if key != ("claude-code", "widget-specialist.md"):
            assert FLAKE_REASON not in text, key


def test_a_single_read_only_agent_takes_the_language_rule():
    files = _files(_render(pair=False, readOnly=True, outputLanguage="ste"))
    assert _report(files[("claude-code", "widget.md")]).startswith(
        f"{STE} Sections: Verdict (one line);"
    )


@pytest.mark.parametrize("value", ["english", "STE", "", 1])
def test_an_unknown_output_language_is_refused(value):
    with pytest.raises(ValueError, match=r"outputLanguage must be \"none\" or \"ste\""):
        parse_declaration({"agents": {"widget": {"description": "d", "outputLanguage": value}}})


@pytest.mark.parametrize("value", ["true", 1, None])
def test_a_tracked_file_build_that_is_not_a_boolean_is_refused(value):
    with pytest.raises(ValueError, match="trackedFileBuild must be true or false"):
        parse_declaration({"agents": {"widget": {"description": "d", "trackedFileBuild": value}}})
