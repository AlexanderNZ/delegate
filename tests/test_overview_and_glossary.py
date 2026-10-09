"""The Start-here overview and the glossary: the first two layers of the docs.

The glossary holds one entry for each term that the docs use and do not define
on the spot. The entries are in alphabetical order, each entry links the page
that gives the detail, and each link reaches a page and a heading that exist.
The glossary is in ASD-STE100, so each sentence has 25 words or fewer.

The overview is the kit at a glance. It gives the problem, the idea, the ladder
visual, the mental model, what the kit is not, the parts of the kit and where to
go next, in that order. Each term that it puts in bold links its glossary entry. The README
docs map links the overview and the glossary first, as the place to start.

The pages are outside the package source, so a Nix build that copies only the
package skips these cases with a reason.
"""

import re
from pathlib import Path

from .pages import ROOT, outside_the_package

DOCS = ROOT / "docs"
GLOSSARY = DOCS / "glossary.md"
OVERVIEW = DOCS / "overview.md"
README = ROOT / "README.md"
REPO_BLOB = re.compile(r"https://github\.com/[^/]+/delegate/blob/main/")  # the full URL of a file of this repository

# The sections of the overview, in the order of the ticket: the pain, the idea, the visual,
# the mental model, and what the kit is not. Then the parts and the next pages.
OVERVIEW_SECTIONS = [
    "The problem",
    "The idea",
    "The ladder",
    "The mental model",
    "What delegate is not",
    "The parts of the kit",
    "Where to go next",
]

# The four verbs that name the loop from a ticket to a merge, in their order.
LOOP = ["Brief", "Build", "Verify", "Merge"]

# A term in bold, as the overview writes it at its first use: the bold text is the link text of its glossary entry.
BOLD = re.compile(r"\*\*([^*]+)\*\*")
BOLD_GLOSSARY_LINK = re.compile(r"\[\*\*([^*]+)\*\*\]\(glossary\.md#([^)\s]+)\)")

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


def singular(text: str) -> str:
    """The term of a bold link text, which may be plural: "sessions" is the term "session"."""
    known = {term(heading) for heading, _ in entries()}
    word = term(text)
    return next((form for form in (word, word.removesuffix("es"), word.removesuffix("s")) if form in known), word)


def entries() -> list[tuple[str, str]]:
    """The entries of the glossary: the text of each level-two heading, and the body under it."""
    parts = re.split(r"^## (.+)$", prose(GLOSSARY.read_text()), flags=re.MULTILINE)
    return list(zip(parts[1::2], parts[2::2]))


def broken_links(page: Path) -> list[str]:
    """Each relative link of the page whose file or heading does not exist."""
    broken = []
    for target in LINK.findall(page.read_text()):
        # A link to a file outside docs/ is a full URL (the site build cannot follow a path out of docs/). The file is in this tree.
        in_repo = REPO_BLOB.match(target)
        if not in_repo and target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = (target[in_repo.end():] if in_repo else target).partition("#")
        file = ((ROOT if in_repo else page.parent) / path).resolve() if path else page
        if not file.is_file():
            broken.append(target)
        # Only a Markdown page has headings. The fragment of an image (`#gh-dark-mode-only`) selects a colour scheme.
        elif anchor and file.suffix == ".md" and anchor not in anchors(file.read_text()):
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

    full = [(heading, body) for heading, body in entries() if not POINTER.fullmatch(body.strip())]
    assert [heading for heading, body in full if not opening(body).startswith(term(heading))] == []


# A pointer entry is the entry of a synonym: it says "See <term>." and links the entry of the preferred term.
POINTER = re.compile(r"See \[([^\]]+)\]\(#([^)\s]+)\)\.")


@outside_the_package
def test_a_pointer_entry_names_a_term_that_has_a_full_entry():
    pointers = {term(heading): POINTER.fullmatch(body.strip()) for heading, body in entries() if POINTER.fullmatch(body.strip())}
    headings = {term(heading): slug(heading) for heading, body in entries() if not POINTER.fullmatch(body.strip())}

    assert pointers, "the glossary holds no pointer entry for a synonym"
    # The target is a full entry (not another pointer), and the link text is the heading of that entry.
    wrong = [name for name, match in pointers.items() if term(match.group(1)) not in headings or headings[term(match.group(1))] != match.group(2)]
    assert wrong == []


@outside_the_package
def test_the_synonyms_that_a_reader_is_likely_to_look_up_have_a_pointer_entry():
    terms = {term(heading) for heading, _ in entries()}
    assert {"twin", "verifier twin", "pair", "specialist-verifier pair", "reviewer", "implementer"} <= terms


@outside_the_package
def test_the_glossary_keeps_each_sentence_to_25_words_or_fewer():
    lines = [line for line in prose(GLOSSARY.read_text()).splitlines() if line.strip() and not line.startswith(("#", "|"))]
    text = re.sub(r"`[^`]*`", "CODE", re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", " ".join(lines)))  # an inline code span counts as one word
    sentences = re.split(r"(?<=[.:?!])\s+", text)
    assert [s for s in sentences if len(s.split()) > 25] == []


def overview_sections() -> dict[str, str]:
    """The sections of the overview, keyed by the text of their level-two heading."""
    parts = re.split(r"^## (.+)$", prose(OVERVIEW.read_text()), flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


@outside_the_package
def test_the_overview_gives_its_sections_in_the_order_of_the_ticket():
    headings = re.findall(r"^## (.+)$", prose(OVERVIEW.read_text()), flags=re.MULTILINE)
    assert headings == OVERVIEW_SECTIONS


@outside_the_package
def test_the_overview_says_near_the_top_that_it_is_the_kit_at_a_glance():
    opening = prose(OVERVIEW.read_text()).split("\n## ", 1)[0]
    assert "at a glance" in opening


@outside_the_package
def test_the_mental_model_names_the_loop_with_the_four_verbs_in_order():
    model = overview_sections()["The mental model"]
    positions = [model.find(verb) for verb in LOOP]
    assert -1 not in positions, f"a verb of the loop is missing: {dict(zip(LOOP, positions))}"
    assert positions == sorted(positions)


@outside_the_package
def test_every_term_in_bold_in_the_overview_has_a_glossary_entry():
    bold = BOLD.findall(prose(OVERVIEW.read_text()))
    assert bold, "the overview puts no term in bold, so this check would check nothing"
    glossary = {term(heading) for heading, _ in entries()}
    assert sorted({b for b in bold if singular(b) not in glossary}) == []


@outside_the_package
def test_every_term_in_bold_in_the_overview_links_its_glossary_entry():
    text = prose(OVERVIEW.read_text())
    anchor_of = {term(heading): slug(heading) for heading, _ in entries()}
    linked = BOLD_GLOSSARY_LINK.findall(text)
    unlinked = [b for b in BOLD.findall(text) if b not in {name for name, _ in linked}]
    wrong_anchor = [(name, anchor) for name, anchor in linked if anchor_of.get(singular(name)) != anchor]
    assert (unlinked, wrong_anchor) == ([], [])


@outside_the_package
def test_the_overview_puts_each_term_in_bold_once():
    bold = [singular(b) for b in BOLD.findall(prose(OVERVIEW.read_text()))]
    assert sorted({b for b in bold if bold.count(b) > 1}) == []


@outside_the_package
def test_every_link_of_the_overview_reaches_a_page_and_a_heading_that_exist():
    assert broken_links(OVERVIEW) == []


@outside_the_package
def test_the_readme_docs_map_starts_with_the_overview_and_then_the_glossary():
    docs_map = README.read_text().split("\n## Docs\n", 1)[1].split("\n## ", 1)[0]
    assert LINK.findall(docs_map)[:2] == ["docs/overview.md", "docs/glossary.md"]
