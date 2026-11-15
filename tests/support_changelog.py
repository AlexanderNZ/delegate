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


# The claims of an earlier entry of 1.22.0 that a later issue made false (#356): each is a text that the entry of the issue held, and it is not in the
# Unreleased section now. The entries of #312, #316, #317 and #318 said them; #323 (the run index on by default, the configuration file, the reason of an
# unreachable host) changed each one.
STALE_CLAIMS = (
    "Until a later release reads other hosts",
    "the view shows only `heard <duration> ago`, not the reason",
    "With no `DELEGATE_INDEX`, the engine writes nothing and adds no event",
    "shows only when `DELEGATE_INDEX` is not set",
    "limits the view to this machine and reads no index",
    "under the line of the missing run index",
)

# The issues that a later issue changed: the entry of the first issue names each later issue, so a reader of the release notes sees both.
CHANGED_LATER = {
    "307": ("317", "318", "329"),
    "309": ("317", "322"),
    "312": ("323",),
    "316": ("323", "327"),
    "317": ("318", "323"),
    "318": ("323", "328"),
}


def section_of_issue(changelog: Path, issue: str) -> str:
    """The body of the first `## ` section that has an entry of the issue: `[Unreleased]` before the release, and its version after `scripts/release.py apply` moved it."""
    for body in entries(changelog).values():
        if re.search(rf"^- .*\(#{issue}[,)]", body, flags=re.MULTILINE):
            return body
    raise AssertionError(f"CHANGELOG has no entry of #{issue}")


def unreleased_entries(changelog: Path) -> dict[str, str]:
    """The text of each bullet of the section of 1.22.0, by the number of its issue (the first `(#<n>` of the bullet)."""
    bullets = re.findall(r"^- (.+)$", section_of_issue(changelog, "323").split("### Added", 1)[-1], flags=re.MULTILINE)
    found = {}
    for bullet in bullets:
        match = re.search(r"\(#(\d+)", bullet)
        if match:
            found.setdefault(match.group(1), bullet)
    return found


def check_the_unreleased_section_holds_no_claim_that_a_later_issue_made_false(changelog: Path) -> None:
    text = section_of_issue(changelog, "323")

    assert [claim for claim in STALE_CLAIMS if claim in text] == []


def check_an_entry_that_a_later_issue_changed_names_that_issue(changelog: Path) -> None:
    found = unreleased_entries(changelog)

    assert {issue: [later for later in laters if f"#{later}" not in found[issue].split(f"(#{issue}", 1)[-1]] for issue, laters in CHANGED_LATER.items()} == {
        issue: [] for issue in CHANGED_LATER
    }


def check_the_unreleased_section_tells_a_consumer_what_to_do(changelog: Path) -> None:
    body = section_of_issue(changelog, "323")

    assert body.lstrip().startswith("### Consumer impact")
    impact = body.split("### Added", 1)[0]
    for part in ("config.toml", "hosts", "1.22.0", "--json"):
        assert part in impact, part


def check_each_function_that_an_adr_names_exists_or_is_said_to_be_removed(adr: Path, source: Path) -> None:
    code = "\n".join(path.read_text() for path in source.rglob("*.py"))
    missing = []
    for sentence in re.split(r"(?<=\.)\s+", adr.read_text()):
        for name in re.findall(r"`([a-z_][a-z0-9_.]*)`", sentence):
            last = name.split(".")[-1]
            if "_" in last and not re.search(rf"\b{re.escape(last)}\b", code) and "removed" not in sentence:
                missing.append(last)

    assert missing == []
