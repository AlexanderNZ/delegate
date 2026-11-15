"""CHANGELOG: it holds an entry for the first release, and the entry matches the version of the package.

CHANGELOG sits outside the package source, so a Nix build that copies only the
package skips these cases with a reason.
"""

import re
from pathlib import Path

from .pages import ROOT, outside_the_package
from .support_changelog import (
    check_a_consumer_impact_subsection_is_the_first_subsection_of_its_entry,
    check_an_entry_that_a_later_issue_changed_names_that_issue,
    check_an_entry_with_a_date_has_the_version_of_the_package,
    check_each_function_that_an_adr_names_exists_or_is_said_to_be_removed,
    check_each_subsection_is_a_type_of_keep_a_changelog_or_consumer_impact,
    check_the_entry_for_the_first_release_names_each_part_of_the_kit,
    check_the_first_release_is_the_oldest_entry_and_unreleased_comes_before_it,
    check_the_unreleased_section_holds_no_claim_that_a_later_issue_made_false,
    check_the_unreleased_section_tells_a_consumer_what_to_do,
)

CHANGELOG = ROOT / "CHANGELOG.md"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

@outside_the_package
def test_the_changelog_has_an_entry_with_a_date_for_the_version_of_the_package():
    check_an_entry_with_a_date_has_the_version_of_the_package(CHANGELOG, PYPROJECT)


@outside_the_package
def test_the_first_release_is_the_oldest_entry_and_an_unreleased_section_comes_before_it():
    check_the_first_release_is_the_oldest_entry_and_unreleased_comes_before_it(CHANGELOG)


@outside_the_package
def test_the_entry_for_the_first_release_names_each_part_of_the_kit_that_a_user_can_run():
    check_the_entry_for_the_first_release_names_each_part_of_the_kit(CHANGELOG)


@outside_the_package
def test_contributing_tells_a_contributor_to_list_a_change_under_unreleased():
    text = CONTRIBUTING.read_text()
    send = re.search(r"^## Send a change\n(.*?)(?=^## |\Z)", text, flags=re.MULTILINE | re.DOTALL)

    assert send, "CONTRIBUTING has no section 'Send a change'"
    assert "CHANGELOG.md" in send.group(1) and "Unreleased" in send.group(1)


@outside_the_package
def test_the_readme_links_the_changelog():
    assert "CHANGELOG.md" in re.findall(r"\]\(([^)\s]+)\)", (ROOT / "README.md").read_text())


@outside_the_package
def test_each_subsection_of_an_entry_is_a_type_of_keep_a_changelog_or_consumer_impact():
    check_each_subsection_is_a_type_of_keep_a_changelog_or_consumer_impact(CHANGELOG)


@outside_the_package
def test_a_consumer_impact_subsection_is_the_first_subsection_of_its_entry():
    check_a_consumer_impact_subsection_is_the_first_subsection_of_its_entry(CHANGELOG)


@outside_the_package
def test_the_preamble_of_the_changelog_tells_when_an_entry_has_a_consumer_impact_subsection():
    preamble = CHANGELOG.read_text().split("\n## ", 1)[0]

    assert "### Consumer impact" in preamble
    assert "render" in preamble and "validate" in preamble


@outside_the_package
def test_the_unreleased_section_holds_no_claim_that_a_later_issue_made_false():
    check_the_unreleased_section_holds_no_claim_that_a_later_issue_made_false(CHANGELOG)


@outside_the_package
def test_an_entry_that_a_later_issue_changed_names_that_issue():
    check_an_entry_that_a_later_issue_changed_names_that_issue(CHANGELOG)


@outside_the_package
def test_the_unreleased_section_tells_a_consumer_what_to_do():
    check_the_unreleased_section_tells_a_consumer_what_to_do(CHANGELOG)


@outside_the_package
def test_adr_0009_names_no_function_that_is_gone_unless_it_says_the_function_was_removed():
    adr = ROOT / "docs" / "adr" / "0009-the-journal-has-one-projection-the-run-state.md"

    check_each_function_that_an_adr_names_exists_or_is_said_to_be_removed(adr, ROOT / "src" / "delegate")
