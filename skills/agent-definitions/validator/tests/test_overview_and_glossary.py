"""The Start-here overview and the glossary: the first two layers of the docs.

The glossary holds one entry for each term that the docs use and do not define
on the spot. The entries are in alphabetical order, each entry links the page
that gives the detail, and each link reaches a page and a heading that exist.
The glossary is in ASD-STE100, so each sentence has 25 words or fewer.

The pages are outside the package source, so a Nix build that copies only the
package skips these cases with a reason.
"""

import re
from pathlib import Path

from .pages import ROOT, outside_the_package

DOCS = ROOT / "docs"
GLOSSARY = DOCS / "glossary.md"

FENCED = re.compile(r"^(`{3,}).*?^\1$", flags=re.DOTALL | re.MULTILINE)
LINK = re.compile(r"\]\(([^)\s]+)\)")


def prose(text: str) -> str:
    """The text of a page outside its fenced blocks."""
    return FENCED.sub("", text)


def slug(heading: str) -> str:
    """The anchor of a heading, as GitHub builds it."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def anchors(markdown: str) -> set[str]:
    return {slug(heading) for heading in re.findall(r"^#{1,6} (.+)$", prose(markdown), flags=re.MULTILINE)}


def term(text: str) -> str:
    """The form of a term that the order and the match use: no backticks, no case."""
    return text.replace("`", "").strip().casefold()


def entries() -> list[tuple[str, str]]:
    """The entries of the glossary: the text of each level-two heading, and the body under it."""
    parts = re.split(r"^## (.+)$", prose(GLOSSARY.read_text()), flags=re.MULTILINE)
    return list(zip(parts[1::2], parts[2::2]))


def broken_links(page: Path) -> list[str]:
    """Each relative link of the page whose file or heading does not exist."""
    broken = []
    for target in LINK.findall(page.read_text()):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        file = (page.parent / path).resolve() if path else page
        if not file.is_file():
            broken.append(target)
        elif anchor and anchor not in anchors(file.read_text()):
            broken.append(target)
    return broken


@outside_the_package
def test_the_glossary_lists_its_entries_in_alphabetical_order():
    terms = [term(heading) for heading, _ in entries()]
    assert terms, "the glossary holds no entry, so the order check would check nothing"
    out_of_order = [(a, b) for a, b in zip(terms, terms[1:]) if a > b]
    assert out_of_order == []


@outside_the_package
def test_the_glossary_holds_each_term_once():
    terms = [term(heading) for heading, _ in entries()]
    assert sorted({t for t in terms if terms.count(t) > 1}) == []


@outside_the_package
def test_every_link_of_the_glossary_reaches_a_page_and_a_heading_that_exist():
    assert broken_links(GLOSSARY) == []


@outside_the_package
def test_every_glossary_entry_links_the_page_that_gives_the_detail():
    assert [heading for heading, body in entries() if not LINK.search(body)] == []


@outside_the_package
def test_the_first_sentence_of_each_glossary_entry_starts_with_its_term():
    def opening(body: str) -> str:
        first = body.strip().splitlines()[0].replace("`", "")
        return re.sub(r"^(?:A|An|The) ", "", first).casefold()

    assert [heading for heading, body in entries() if not opening(body).startswith(term(heading))] == []


@outside_the_package
def test_the_glossary_keeps_each_sentence_to_25_words_or_fewer():
    lines = [line for line in prose(GLOSSARY.read_text()).splitlines() if line.strip() and not line.startswith(("#", "|"))]
    text = re.sub(r"`[^`]*`", "CODE", re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", " ".join(lines)))  # an inline code span counts as one word
    sentences = re.split(r"(?<=[.:?!])\s+", text)
    assert [s for s in sentences if len(s.split()) > 25] == []
