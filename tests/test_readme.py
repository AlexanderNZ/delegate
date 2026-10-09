"""The README: it links every docs page that exists, and it holds the sections of a first read.

The README is outside the package source, so a Nix build that copies only the
package skips these cases with a reason.
"""

import re
from pathlib import Path

import pytest

from .pages import repository_root

ROOT = repository_root(Path(__file__))
README = ROOT / "README.md"

outside_the_package = pytest.mark.skipif(
    not README.is_file(), reason="the docs are outside the package source, as in a Nix build"
)

# docs/adr/ is left out: the README links the page that indexes the ADRs, and a test of the explanation pages fails
# when that page misses an ADR.
DOCS_PAGES = (
    sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").glob("**/*.md") if p.relative_to(ROOT / "docs").parts[0] != "adr")
    if (ROOT / "docs").is_dir()
    else []
)


def readme_links() -> set[str]:
    return {target.split("#")[0] for target in re.findall(r"\]\(([^)\s]+)\)", README.read_text())}


@outside_the_package
@pytest.mark.parametrize("page", DOCS_PAGES)
def test_the_readme_links_every_docs_page(page):
    assert page in readme_links()


@outside_the_package
def test_the_readme_links_to_the_tutorial():
    assert "docs/tutorial.md" in readme_links()


@outside_the_package
def test_every_relative_link_of_the_readme_reaches_a_file():
    relative = [t for t in readme_links() if t and not t.startswith(("http://", "https://", "#", "mailto:"))]
    assert [t for t in relative if not (ROOT / t).exists()] == []


@outside_the_package
def test_the_readme_has_a_quick_start_a_docs_map_and_the_licence_with_the_generated_files_statement():
    text = README.read_text()
    headings = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
    assert {"Quick start", "Docs", "Licence"} <= set(headings)
    licence = text.split("## Licence")[1]
    assert "MIT" in licence
    assert "belong to you" in text  # the generated files statement
