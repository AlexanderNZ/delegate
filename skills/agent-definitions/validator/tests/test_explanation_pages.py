"""The explanation pages: the reasons for the design, in the maintainer's voice.

A reader of an explanation page does not follow steps. The reader learns why
the kit works as it does. So these tests check that each page exists, that its
links work, that it speaks in the first person, and that it gives the reasons
its ticket names. The pages are outside the package source, so a Nix build that
copies only the package skips these cases with a reason.
"""

import re

import pytest

from .pages import ROOT, outside_the_package

EXPLANATION = ROOT / "docs" / "explanation"

PAGES = [
    "why-the-verifier-is-blind.md",
    "why-each-specialist-has-a-twin.md",
]

# The reasons that each page must give, as a pattern for each reason. The reasons come from the ticket and the spec.
REASONS: dict[str, list[tuple[str, str]]] = {
    "why-the-verifier-is-blind.md": [
        ("a verifier that reads the report anchors on its framing", r"anchor"),
        ("the verifier gets the task and the diff, never the report", r"never (gets? )?the (specialist's )?report"),
        ("the claim that the tests pass is not evidence", r"claim"),
        ("the engine runs the gates outside the specialist", r"engine runs (the|your) gates"),
        ("the verifier runs the gates in a copy it may break", r"temporary copy"),
    ],
    "why-each-specialist-has-a-twin.md": [
        ("the twin holds the same skills as the specialist", r"same skills"),
        ("a verifier without the skills reads the code and misses the discipline", r"misses the discipline"),
        ("the twin has a read-only tool set", r"read-only"),
        ("one declaration renders both agents, so the pair cannot drift", r"one declaration"),
        ("a generic verifier is right only for docs and configuration", r"generic verifier"),
        ("a monorepo has one pair for each stack", r"each stack"),
    ],
}


def prose(page: str) -> str:
    """The text of the page outside its fenced blocks."""
    text = (EXPLANATION / page).read_text()
    return re.sub(r"^(`{3,}).*?^\1$", "", text, flags=re.DOTALL | re.MULTILINE)


def anchors(markdown: str) -> set[str]:
    """The anchors of the headings of a page, as GitHub builds them."""
    found = set()
    for heading in re.findall(r"^#{1,6} (.+)$", re.sub(r"^(`{3,}).*?^\1$", "", markdown, flags=re.DOTALL | re.MULTILINE), flags=re.MULTILINE):
        found.add(re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-"))
    return found


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_every_relative_link_reaches_a_file_and_a_heading(page):
    broken = []
    for target in re.findall(r"\]\(([^)\s]+)\)", (EXPLANATION / page).read_text()):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        file = (EXPLANATION / path).resolve() if path else EXPLANATION / page
        if not file.is_file():
            broken.append(target)
        elif anchor and anchor not in anchors(file.read_text()):
            broken.append(target)
    assert broken == []


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_a_page_speaks_in_the_first_person_of_the_maintainer(page):
    assert re.search(r"\bI\b", prose(page)), "an explanation page is in the maintainer's own voice"


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_a_page_gives_the_reasons_of_its_ticket(page):
    text = prose(page).lower()
    missing = [reason for reason, pattern in REASONS[page] if not re.search(pattern, text)]
    assert missing == []
