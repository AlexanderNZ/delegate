"""The README: the repository side of the project. It describes the tool in a few lines, links the docs site, and covers the
technical, development and contribution side. The docs site holds the rest, so the README does not list every page.

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

SITE = "https://alexandernz.github.io/delegate/"

# The sections of the README, in order: the docs link first, then the repository side.
SECTIONS = ["Docs", "Status", "Install", "The repository", "Development", "Contributing", "Generated files", "Licence"]


def readme_links() -> set[str]:
    return {target.split("#")[0] for target in re.findall(r"\]\(([^)\s]+)\)", README.read_text())}


@outside_the_package
def test_the_readme_gives_its_sections_in_order():
    assert re.findall(r"^## (.+)$", README.read_text(), flags=re.MULTILINE) == SECTIONS


@outside_the_package
def test_the_docs_section_links_the_site_and_the_entry_points_of_the_docs():
    docs = README.read_text().split("\n## Docs\n", 1)[1].split("\n## ", 1)[0]
    links = re.findall(r"\]\(([^)\s]+)\)", docs)
    assert SITE in links
    assert {"docs/overview.md", "docs/tutorial.md", "docs/glossary.md"} <= set(links)


@outside_the_package
def test_the_readme_links_the_contribution_files_and_each_one_exists():
    for name in ["CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md", "CHANGELOG.md", "LICENSE"]:
        assert name in readme_links(), name
        assert (ROOT / name).is_file(), name


@outside_the_package
def test_the_development_section_gives_the_commands_that_ci_runs_and_says_what_the_flake_gives():
    development = README.read_text().split("\n## Development\n", 1)[1].split("\n## ", 1)[0]
    assert "pip install . pytest" in development and "python -m pytest -rs" in development
    assert "Nix is optional" in development
    for output in ["nix develop", "nix flake check", "lib.skills"]:
        assert output in development, output


@outside_the_package
def test_the_readme_links_to_the_tutorial():
    assert "docs/tutorial.md" in readme_links()


@outside_the_package
def test_every_relative_link_of_the_readme_reaches_a_file():
    relative = [t for t in readme_links() if t and not t.startswith(("http://", "https://", "#", "mailto:"))]
    assert [t for t in relative if not (ROOT / t).exists()] == []


@outside_the_package
def test_the_readme_has_the_licence_with_the_generated_files_statement():
    text = README.read_text()
    licence = text.split("## Licence")[1]
    assert "MIT" in licence
    assert "belong to you" in text  # the generated files statement
