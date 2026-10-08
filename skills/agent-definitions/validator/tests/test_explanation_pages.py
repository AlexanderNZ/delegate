"""The explanation pages: the reasons for the design, in the maintainer's voice.

A reader of an explanation page does not follow steps. The reader learns why
the kit works as it does. So these tests check that each page exists, that its
links work, that it speaks in the first person, and that it gives the reasons
its ticket names. The pages are outside the package source, so a Nix build that
copies only the package skips these cases with a reason.
"""

import datetime
import re

import pytest

from .pages import ROOT, outside_the_package

EXPLANATION = ROOT / "docs" / "explanation"

PAGES = [
    "why-the-verifier-is-blind.md",
    "why-each-specialist-has-a-twin.md",
    "the-mode-trade-off.md",
    "prior-art.md",
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
    "the-mode-trade-off.md": [
        ("assure verifies each branch at once and gives up tokens and time", r"`assure`.*at once"),
        ("economy verifies at the end of the chain and gives up early discovery", r"`economy`.*early"),
        ("a defect of the first ticket in economy shows late, after later tickets built on it", r"built on it"),
        ("economy is for the fewest tokens", r"fewest tokens"),
        ("a cheaper mode never means a weaker verifier", r"weaker verifier"),
        ("the trust rules are the same in both modes", r"invariants"),
        ("the page says how to choose", r"choose"),
    ],
    "prior-art.md": [
        ("the page says when to choose this kit and when to choose another tool", r"choose"),
        ("the page says the kit takes the planning output of mattpocock/skills as input", r"to-tickets"),
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


# The prior art that the ticket names: the heading of its entry, and the primary source that the entry must link.
PRIOR_ART = {
    "superpowers": "https://github.com/obra/superpowers",
    "implement-spec": "https://github.com/mattpocock/skills",
    "Sandcastle": "https://github.com/mattpocock/sandcastle",
    "wshobson/agents": "https://github.com/wshobson/agents",
}


def entries(page: str) -> dict[str, str]:
    """The sections of the page, keyed by the text of their level-two heading."""
    parts = re.split(r"^## (.+)$", (EXPLANATION / page).read_text(), flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


@outside_the_package
def test_each_prior_art_entry_links_its_primary_source_and_gives_the_date_it_was_read():
    sections = entries("prior-art.md")
    problems = []
    for name, source in PRIOR_ART.items():
        body = next((text for heading, text in sections.items() if name in heading), None)
        if body is None:
            problems.append(f"{name}: no entry")
            continue
        if not re.search(r"\]\(" + re.escape(source) + r"[/)#]", body):
            problems.append(f"{name}: no link to {source}")
        read = re.search(r"\bRead (\d{4}-\d{2}-\d{2})\b", body)
        if not read:
            problems.append(f"{name}: no read date")
        elif datetime.date.fromisoformat(read.group(1)) > datetime.date.today():
            problems.append(f"{name}: the read date {read.group(1)} is in the future")
    assert problems == []


@outside_the_package
def test_the_prior_art_page_holds_one_entry_for_each_tool_of_the_ticket_and_no_other():
    named = [heading for heading in entries("prior-art.md") if heading.strip() != "How to choose"]
    assert sorted(next(name for name in PRIOR_ART if name in heading) for heading in named) == sorted(PRIOR_ART)
