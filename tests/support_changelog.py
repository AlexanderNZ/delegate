"""The checks of the CHANGELOG, as functions of the file paths that they read (#354).

`tests/test_changelog.py` runs each check on the real files of the repository. `tests/test_release.py` runs the same
checks on a copy of the files after `scripts/release.py apply`, so no test file calls a test of another test file.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

# A release heading: `## [0.1.0] - 2026-10-09`.
RELEASE = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})$", flags=re.MULTILINE)

# The subsections that an entry can hold: `Consumer impact` for the actions
# that a consumer must take (#237), the types of Keep a Changelog, and the
# groups of the release notes, which `scripts/release.py apply` writes into an
# entry when the Unreleased section is empty.
SUBSECTIONS = (
    "Consumer impact",
    "Added", "Changed", "Deprecated", "Removed", "Fixed", "Security",
    "Features", "Fixes", "Documentation", "Tests", "CI", "Other changes",
)


def entry(changelog: Path, version: str) -> str:
    """The text of the entry for the version, up to the next `## ` heading."""
    match = re.search(rf"^## \[{re.escape(version)}\] - .*?\n(.*?)(?=^## |\Z)", changelog.read_text(), flags=re.MULTILINE | re.DOTALL)
    assert match, f"CHANGELOG has no entry for version {version}"
    return match.group(1)


def entries(changelog: Path) -> dict[str, str]:
    """The body of each `## ` section of the CHANGELOG, by its heading."""
    parts = re.split(r"^## (.+)\n", changelog.read_text(), flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


def check_an_entry_with_a_date_has_the_version_of_the_package(changelog: Path, pyproject: Path) -> None:
    version = tomllib.loads(pyproject.read_text())["project"]["version"]

    assert version in [found[0] for found in RELEASE.findall(changelog.read_text())]


def check_the_first_release_is_the_oldest_entry_and_unreleased_comes_before_it(changelog: Path) -> None:
    headings = re.findall(r"^## (.+)$", changelog.read_text(), flags=re.MULTILINE)

    assert headings[0] == "[Unreleased]"
    assert re.fullmatch(r"\[0\.1\.0\] - \d{4}-\d{2}-\d{2}", headings[-1])


def check_the_entry_for_the_first_release_names_each_part_of_the_kit(changelog: Path) -> None:
    added = entry(changelog, "0.1.0").lower()

    for part in ("skills", "render", "validate", "bootstrap", "brief", "delegate run", "claude-code", "opencode", "delegate status", "delegate watch", "delegate docs", "economy"):
        assert part in added, part
    assert "### added" in added


def check_each_subsection_is_a_type_of_keep_a_changelog_or_consumer_impact(changelog: Path) -> None:
    found = {(heading, sub) for heading, body in entries(changelog).items() for sub in re.findall(r"^### (.+)$", body, flags=re.MULTILINE)}

    assert {(heading, sub) for heading, sub in found if sub not in SUBSECTIONS} == set()


def check_a_consumer_impact_subsection_is_the_first_subsection_of_its_entry(changelog: Path) -> None:
    late = [heading for heading, body in entries(changelog).items() if re.findall(r"^### (.+)$", body, flags=re.MULTILINE)[1:].count("Consumer impact")]

    assert late == [], "a consumer reads the entry from the top, so `### Consumer impact` comes first"
