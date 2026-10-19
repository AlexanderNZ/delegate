"""A verifier reports a command that its guard refuses, and does not run it in another form (#124).

The read list of the verifier holds `bash -c` and `python3`, because the
verifiers need them for probes. The guard does not parse a nested shell, so a
verifier can run a refused command through a wrapper: `bash -c '<cmd>'`, or
`python3 -c 'import subprocess; ...'`. A verifier did this in a real run, after
its guard refused the bare command. The guard cannot stop it, so the rule is in
the text that the verifier reads: the rendered verifier of each harness, and the
verifier brief of a `delegate run`. The enforcement page states the rule, and
why the guard does not enforce it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from delegate.definitions.validate import split_frontmatter
from delegate.run.brief import verifier_run_sections

from .pages import outside_the_package

ROOT = Path(__file__).resolve().parents[1]
ENFORCEMENT_PAGE = ROOT / "docs" / "explanation" / "the-enforcement-model-and-its-limits.md"

#: The words of the rule, in each text that states it.
RULE = "not run in another form"

#: Each other form of a refused command that the rule names.
FORMS = ("`bash -c`", "`python3 -c`", "`sh -c`", "`env`", "`xargs`", "a script file")


def _body(rendered: Path, harness: str) -> str:
    _fm, body, err = split_frontmatter((rendered / harness / "java-spring-verifier.md").read_text())
    assert err is None, err
    return body


def _section(text: str, title: str) -> str:
    """The text of the section `## <title>` of `text`, up to the next section."""
    return text.split(f"\n## {title}\n", 1)[1].split("\n## ", 1)[0]


def _paragraph(text: str, phrase: str) -> str:
    """The one paragraph of `text` that holds `phrase`."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if phrase in p]
    assert len(paragraphs) == 1, f"{len(paragraphs)} paragraphs hold {phrase!r}"
    return paragraphs[0]


def _assert_the_rule(paragraph: str) -> None:
    assert [form for form in FORMS if form not in paragraph] == [], paragraph
    assert "`unverified`" in paragraph, paragraph
    assert "with the command and the message" in paragraph, paragraph


@pytest.mark.parametrize("harness", ["claude-code", "opencode"])
def test_the_rendered_verifier_tells_the_verifier_to_report_a_refused_command_and_not_to_wrap_it(rendered, harness):
    _assert_the_rule(_paragraph(_section(_body(rendered, harness), "Your shell"), RULE))


@pytest.mark.parametrize("mode", ["full", "fix-up"])
def test_the_verifier_brief_tells_the_verifier_to_report_a_refused_command_and_not_to_wrap_it(tmp_path, mode):
    brief = verifier_run_sections(tmp_path / "copy", tmp_path / "verdict.json", mode)

    _assert_the_rule(_paragraph(brief, RULE))


# The docs are outside the package source, as in a Nix build.
@outside_the_package
def test_the_enforcement_page_states_the_rule_and_why_the_guard_does_not_enforce_it():
    section = ENFORCEMENT_PAGE.read_text().split("## The limits of the guards", 1)[1].split("\n## ", 1)[0]
    (item,) = [line for line in section.splitlines() if RULE in line]

    assert [form for form in FORMS if form not in item] == [], item
    assert "`unverified`" in item
    assert "does not parse a nested shell" in item
    assert "after the verifier" in item
