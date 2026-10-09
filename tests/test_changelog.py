"""CHANGELOG: it holds an entry for the first release, and the entry matches the version of the package.

CHANGELOG sits outside the package source, so a Nix build that copies only the
package skips these cases with a reason.
"""

import re
import tomllib
from pathlib import Path

from .pages import ROOT, outside_the_package

CHANGELOG = ROOT / "CHANGELOG.md"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# A release heading: `## [0.1.0] - 2026-10-09`.
RELEASE = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})$", flags=re.MULTILINE)


def entry(version: str) -> str:
    """The text of the entry for the version, up to the next `## ` heading."""
    match = re.search(rf"^## \[{re.escape(version)}\] - .*?\n(.*?)(?=^## |\Z)", CHANGELOG.read_text(), flags=re.MULTILINE | re.DOTALL)
    assert match, f"CHANGELOG has no entry for version {version}"
    return match.group(1)


@outside_the_package
def test_the_changelog_has_an_entry_with_a_date_for_the_version_of_the_package():
    version = tomllib.loads(PYPROJECT.read_text())["project"]["version"]

    assert version in [found[0] for found in RELEASE.findall(CHANGELOG.read_text())]


@outside_the_package
def test_the_first_release_is_the_oldest_entry_and_an_unreleased_section_comes_before_it():
    headings = re.findall(r"^## (.+)$", CHANGELOG.read_text(), flags=re.MULTILINE)

    assert headings[0] == "[Unreleased]"
    assert re.fullmatch(r"\[0\.1\.0\] - \d{4}-\d{2}-\d{2}", headings[-1])


@outside_the_package
def test_the_entry_for_the_first_release_names_each_part_of_the_kit_that_a_user_can_run():
    added = entry("0.1.0").lower()

    for part in ("skills", "render", "validate", "bootstrap", "brief", "delegate run", "claude-code", "opencode", "delegate status", "delegate watch", "delegate docs", "economy"):
        assert part in added, part
    assert "### added" in added


@outside_the_package
def test_contributing_tells_a_contributor_to_list_a_change_under_unreleased():
    text = CONTRIBUTING.read_text()
    send = re.search(r"^## Send a change\n(.*?)(?=^## |\Z)", text, flags=re.MULTILINE | re.DOTALL)

    assert send, "CONTRIBUTING has no section 'Send a change'"
    assert "CHANGELOG.md" in send.group(1) and "Unreleased" in send.group(1)


@outside_the_package
def test_the_readme_links_the_changelog():
    assert "CHANGELOG.md" in re.findall(r"\]\(([^)\s]+)\)", (ROOT / "README.md").read_text())
