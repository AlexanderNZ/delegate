"""The page "Future ideas": it says its entries are ideas, and each entry has the idea, why it matters, and the open questions.

The pages are outside the package source, so a Nix build that copies only the
package skips these cases with a reason.
"""

import re
import tomllib

from .pages import ROOT, outside_the_package

PAGE = ROOT / "docs" / "future-ideas.md"
ENTRY_PARTS = ["The idea.", "Why it matters.", "Open questions."]
CURSOR_ISSUE = "/delegate/issues/18"  # the Cursor issue; the owner name stays out of the kit


def entries() -> dict[str, str]:
    """The level-two sections of the page: the title of each entry, and its text."""
    parts = re.split(r"^## (.+)$", PAGE.read_text(), flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


@outside_the_package
def test_the_page_says_at_the_top_that_its_entries_are_ideas_and_not_promises():
    intro = PAGE.read_text().split("\n## ")[0]

    assert "idea to investigate" in intro
    assert "not a promise" in intro


@outside_the_package
def test_the_page_has_the_three_entries_of_the_ticket():
    assert list(entries()) == ["A benchmark from your own tickets", "Fixing the coordinator role", "The Cursor adapter"]


@outside_the_package
def test_each_entry_gives_the_idea_why_it_matters_and_the_open_questions_in_that_order():
    for title, text in entries().items():
        positions = [text.find(f"**{part}**") for part in ENTRY_PARTS]
        assert -1 not in positions and positions == sorted(positions), title


@outside_the_package
def test_the_cursor_entry_links_the_issue():
    assert CURSOR_ISSUE in entries()["The Cursor adapter"]


@outside_the_package
def test_the_page_is_in_the_navigation_after_reference_and_before_the_glossary():
    nav = tomllib.loads((ROOT / "zensical.toml").read_text())["project"]["nav"]
    titles = [title for item in nav for title in item]

    assert titles.index("Future ideas") == titles.index("Reference") + 1
    assert titles.index("Future ideas") == titles.index("Glossary") - 1
    assert nav[titles.index("Future ideas")] == {"Future ideas": "future-ideas.md"}
