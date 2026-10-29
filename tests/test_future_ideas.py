"""The page "Future ideas": it says its entries are ideas, and each entry has the idea, why it matters, and the open questions.

The pages are outside the package source, so a Nix build that copies only the
package skips these cases with a reason.
"""

import re
import tomllib

from .pages import ROOT, outside_the_package

PAGE = ROOT / "docs" / "future-ideas.md"
ENTRY_PARTS = ["The idea.", "Why it matters.", "Open questions."]


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
def test_the_page_has_its_three_entries():
    assert list(entries()) == ["A benchmark from real tickets", "Fixing the coordinator role", "Further up the ladder"]


@outside_the_package
def test_each_entry_gives_the_idea_why_it_matters_and_the_open_questions_in_that_order():
    for title, text in entries().items():
        positions = [text.find(f"**{part}**") for part in ENTRY_PARTS]
        assert -1 not in positions and positions == sorted(positions), title


@outside_the_package
def test_no_page_and_no_abbreviation_names_cursor_as_a_harness():
    # The kit names only the harnesses that it supports. The word "cursor" in lower case is the text cursor of a terminal.
    files = [*(ROOT / "docs").rglob("*.md"), ROOT / "includes" / "abbreviations.md"]

    assert [str(f.relative_to(ROOT)) for f in files if "Cursor" in f.read_text()] == []


@outside_the_package
def test_the_page_is_in_the_navigation_after_reference_and_before_the_glossary():
    nav = tomllib.loads((ROOT / "zensical.toml").read_text())["project"]["nav"]
    titles = [title for item in nav for title in item]

    assert titles.index("Future ideas") == titles.index("Reference") + 1
    assert titles.index("Future ideas") == titles.index("Glossary") - 1
    assert nav[titles.index("Future ideas")] == {"Future ideas": "future-ideas.md"}
