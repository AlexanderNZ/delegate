"""Find the first use of each glossary term on a docs page, and whether it links the glossary.

A reader meets a term once before it means something. So the first use of each
term on a page is in bold and links its glossary entry, and later uses are
plain. These helpers find that first use. They read the terms from the
glossary, so a new entry is checked on every page without a change here.

What is not a use: a heading, a fenced block, the front matter, a generated
section, an HTML comment, an image, the text of a link to another page, inline
code that is not the term itself, and the term inside a longer term ("verifier"
inside "verifier guard"). Each of these is masked, and the mask keeps the
length, so a position in the masked text is a position in the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# A term that is also a common verb or adjective counts only after a determiner: "the run", not "it runs".
AFTER_A_DETERMINER = {"run", "step", "report", "skill", "resume", "skip", "brief", "blind", "finding", "chain", "spec"}

# A term with a common sense beside its own counts only after one of these words: "the run context" and "both contexts"
# use the term, and "the context of a decision" and "a context window" do not.
AFTER_A_QUALIFIER = {"context": ("run", "definitions", "bounded", "two", "both")}

# A term that is only ever a state name in code, never a word of the prose.
NOT_PROSE = {"built"}

# A verdict is a term only in capitals: "REJECT" is the verdict, "rejects" is a verb.
CASE_SENSITIVE = {"accept", "reject"}

DETERMINER = r"(?:(?<=\ba )|(?<=\ban )|(?<=\bthe )|(?<=\beach )|(?<=\bevery )|(?<=\bone )|(?<=\bits )|(?<=\btheir )|(?<=\bthis )|(?<=\bthat )|(?<=\bmy )|(?<=\bno ))"

GLOSSARY_LINK = re.compile(r"\[\*\*([^*\]]+)\*\*\]\(((?:\.\./)*)glossary\.md#([^)\s]+)\)")


@dataclass(frozen=True)
class Term:
    name: str  # as the heading writes it, without backticks
    anchor: str


@dataclass(frozen=True)
class Use:
    term: Term
    start: int
    end: int
    linked: bool


def slug(heading: str) -> str:
    """The anchor of a heading, as GitHub builds it."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def terms(glossary: Path) -> list[Term]:
    """Each full entry of the glossary. A pointer entry ("See [X](#x).") names a synonym that the docs avoid."""
    found = []
    for part in re.split(r"^## ", glossary.read_text(), flags=re.MULTILINE)[1:]:
        heading, body = part.split("\n", 1)
        if body.strip().startswith("See ["):
            continue
        name = heading.strip().replace("`", "")
        if name.casefold() not in NOT_PROSE:
            found.append(Term(name, slug(heading.strip())))
    return found


def _blank(text: str, start: int, end: int) -> str:
    return text[:start] + re.sub(r"[^\n]", " ", text[start:end]) + text[end:]


def _mask_all(text: str, pattern: re.Pattern[str], keep_group: int | None = None) -> str:
    for match in reversed(list(pattern.finditer(text))):
        if keep_group is None:
            text = _blank(text, match.start(), match.end())
        else:
            text = _blank(text, match.start(), match.start(keep_group))
            text = _blank(text, match.end(keep_group), match.end())
    return text


def masked(page_text: str) -> str:
    """The page with every part that is not prose blanked out, at the same length."""
    text = page_text
    text = _mask_all(text, re.compile(r"\A---\n.*?\n---\n", re.DOTALL))
    text = _mask_all(text, re.compile(r"<!-- generated:begin .*?<!-- generated:end [^>]*-->", re.DOTALL))
    text = _mask_all(text, re.compile(r"<!--.*?-->", re.DOTALL))
    text = _mask_all(text, re.compile(r"^(`{3,}).*?^\1$", re.MULTILINE | re.DOTALL))
    text = _mask_all(text, re.compile(r"^#.*$", re.MULTILINE))
    text = _mask_all(text, re.compile(r"!\[[^\]]*\]\([^)]*\)"))
    # A link to another page: its text is a title, not a use. A glossary link keeps its text.
    text = _mask_all(text, re.compile(r"\[(?!\*\*)[^\]]*\]\([^)]*\)"))
    # The target of a glossary link is not a use.
    text = _mask_all(text, re.compile(r"\]\([^)]*\)"))
    return text


def _term_pattern(term: Term) -> re.Pattern[str]:
    word = re.escape(term.name).replace(r"\ ", r"\s+")
    body = r"(?<![\w/-])`?" + word + r"(?:s|es)?`?(?![\w-])"
    if term.name.casefold() in AFTER_A_DETERMINER:
        body = DETERMINER + body
    if term.name.casefold() in AFTER_A_QUALIFIER:
        body = "(?:" + "|".join(rf"(?<=\b{word} )" for word in AFTER_A_QUALIFIER[term.name.casefold()]) + ")" + body
    flags = 0 if term.name.casefold() in CASE_SENSITIVE else re.IGNORECASE
    return re.compile(body, flags)


def _without_inline_code(text: str, term: Term) -> str:
    """Blank each inline code span, unless the span is the term itself (`assure`)."""
    def keep(span: str) -> bool:
        return span.strip("`").casefold().rstrip("s") == term.name.casefold().rstrip("s")
    for match in reversed(list(re.finditer(r"`[^`\n]+`", text))):
        if not keep(match.group()):
            text = _blank(text, match.start(), match.end())
    return text


def _without_longer_terms(text: str, term: Term, all_terms: list[Term]) -> str:
    """Blank each longer term that holds this one as a word, and each glossary link to another term."""
    for other in all_terms:
        if other is term or len(other.name) <= len(term.name):
            continue
        if re.search(r"(?<![\w-])" + re.escape(term.name) + r"(?![\w-])", other.name, flags=re.IGNORECASE):
            text = _mask_all(text, re.compile(r"(?<![\w-])`?" + re.escape(other.name).replace(r"\ ", r"\s+") + r"(?:s|es)?`?(?![\w-])", re.IGNORECASE))
    for match in reversed(list(GLOSSARY_LINK.finditer(text))):
        if match.group(3) != term.anchor:
            text = _blank(text, match.start(1), match.end(1))
    return text


def first_uses(page_text: str, all_terms: list[Term]) -> list[Use]:
    """The first use of each term that the page uses, in the order of the glossary."""
    base = masked(page_text)
    uses = []
    for term in all_terms:
        text = _without_longer_terms(_without_inline_code(base, term), term, all_terms)
        match = _term_pattern(term).search(text)
        # A glossary link to the term is a use, with or without a determiner before it.
        link_to_term = next((m for m in GLOSSARY_LINK.finditer(page_text) if m.group(3) == term.anchor and text[m.start(1):m.end(1)].strip()), None)
        if link_to_term is not None and (match is None or link_to_term.start(1) < match.start()):
            uses.append(Use(term, link_to_term.start(1), link_to_term.end(1), True))
            continue
        if match is None:
            continue
        before = page_text[: match.start()]
        after = page_text[match.end():]
        link = re.match(r"\*\*\]\(((?:\.\./)*)glossary\.md#([^)\s]+)\)", after)
        linked = before.endswith("[**") and link is not None and link.group(2) == term.anchor
        uses.append(Use(term, match.start(), match.end(), linked))
    return uses
