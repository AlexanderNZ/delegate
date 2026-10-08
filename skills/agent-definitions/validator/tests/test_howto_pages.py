"""The rules that bind every how-to page: the set of pages, working links, short sentences, and no repeated reference.

A how-to page gives the steps and one example. It links the reference for each
flag and each exit code, and does not restate them.
"""

import re

import pytest

from .pages import HOW_TO, ROOT, blocks, outside_the_package

PAGES = [
    "bootstrap-a-single-stack-repository.md",
    "bootstrap-a-monorepo.md",
    "run-an-economy-chain.md",
    "watch-and-resume-a-run.md",
    "point-the-tiers-at-a-gateway.md",
    "add-a-harness-adapter.md",
    "use-the-kit-after-to-spec-and-to-tickets.md",
]


def prose(page: str) -> str:
    """The text of the page outside its fenced blocks."""
    text = (HOW_TO / page).read_text()
    return re.sub(r"^(`{3,}).*?^\1$", "", text, flags=re.DOTALL | re.MULTILINE)


def anchors(markdown: str) -> set[str]:
    """The anchors of the headings of a page, as GitHub builds them."""
    found = set()
    for heading in re.findall(r"^#{1,6} (.+)$", re.sub(r"^(`{3,}).*?^\1$", "", markdown, flags=re.DOTALL | re.MULTILINE), flags=re.MULTILINE):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        found.add(slug)
    return found


@outside_the_package
def test_the_how_to_directory_holds_the_seven_pages_of_the_ticket_and_no_other():
    assert sorted(path.name for path in HOW_TO.glob("*.md")) == sorted(PAGES)


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_every_relative_link_reaches_a_file_and_a_heading(page):
    broken = []
    for target in re.findall(r"\]\(([^)\s]+)\)", (HOW_TO / page).read_text()):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        file = (HOW_TO / path).resolve() if path else HOW_TO / page
        if not file.is_file():
            broken.append(target)
        elif anchor and anchor not in anchors(file.read_text()):
            broken.append(target)
    assert broken == []


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_a_page_links_the_reference_and_repeats_no_table_of_flags(page):
    text = (HOW_TO / page).read_text()
    assert re.search(r"\]\((\.\./reference/[^)]+|\.\./\.\./skills/agent-definitions/SKILL\.md[^)]*)\)", text), "no link to the reference"
    flag_rows = [line for line in text.splitlines() if re.match(r"\|\s*`-", line)]
    assert flag_rows == []


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_a_page_holds_an_example_in_a_fenced_block(page):
    assert {block.lang for block in blocks(HOW_TO / page)} & {"bash", "toml", "python"}


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_the_prose_keeps_each_sentence_to_25_words_or_fewer(page):
    lines = [line for line in prose(page).splitlines() if line.strip() and not line.startswith(("#", "|", "<!--"))]
    text = re.sub(r"`[^`]*`", "CODE", re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", " ".join(re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", line) for line in lines)))  # an inline code span counts as one word
    sentences = re.split(r"(?<=[.:?!])\s+", text)
    assert [s for s in sentences if len(s.split()) > 25] == []
