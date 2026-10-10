"""Every docs page links each glossary term at its first use, in bold, and nowhere before it."""

import pytest

from .first_use import first_uses, terms
from .pages import ROOT, outside_the_package

DOCS = ROOT / "docs"
GLOSSARY = DOCS / "glossary.md"

# The glossary defines the terms. The agent rules and the ADRs are not pages of the site (zensical.toml excludes them).
PAGES = sorted(
    str(page.relative_to(DOCS)) for page in DOCS.rglob("*.md") if page != GLOSSARY and page.parent.name not in {"agents", "adr"}
) if DOCS.is_dir() else []


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_each_glossary_term_is_in_bold_and_links_the_glossary_at_its_first_use(page):
    text = (DOCS / page).read_text()
    unlinked = [f"{use.term.name}: …{text[max(0, use.start - 40):use.end + 10]!r}…" for use in first_uses(text, terms(GLOSSARY)) if not use.linked]
    assert unlinked == []


def test_context_is_a_term_in_its_architecture_sense_and_not_in_its_common_sense(tmp_path):
    glossary = tmp_path / "glossary.md"
    glossary.write_text("# Glossary\n\n## Context\n\nA context is a part of the package. See [x](x.md).\n")
    found = terms(glossary)

    assert first_uses("It fits one context window, and the context of a decision.\n", found) == []
    assert [use.term.name for use in first_uses("The run context and the two contexts.\n", found)] == ["Context"]
