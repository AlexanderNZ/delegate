"""The ADRs are in `docs/adr/`, and every reference to an ADR points at a file that exists.

A reference is a Markdown link to an ADR file, a path to the ADR directory in
code ticks, or the citation `ADR 0003` in a comment, a test or a page. The
ADRs record decisions about the whole kit, so they sit in `docs/adr/` and not
inside one skill. A moved or renamed ADR must not leave a dead reference.
These files are outside the package source, so a Nix build that copies only
the package skips these cases with a reason.
"""

import re
from pathlib import Path

from .pages import ROOT, outside_the_package

ADR_DIRECTORY = Path("docs") / "adr"

# Directories that hold no source of the repository: the build, the caches, and the design handoff snapshot.
NOT_SOURCES = {".git", ".cache", ".venv", "node_modules", "site", "__pycache__", ".pytest_cache", "design", "build"}
SOURCE_SUFFIXES = {".md", ".py", ".toml", ".txt", ".yml", ".nix"}

LINK = re.compile(r"\]\(([^)\s#]+)")
ADR_FILE = re.compile(r"(?:^|/)adr/\d{4}-[^/]+\.md$")
ADR_PATH_IN_TICKS = re.compile(r"`([\w./-]*adr/[\w./-]*)`")
CITATION = re.compile(r"\bADRs? (\d{4})\b")


def source_files(root: Path) -> list[Path]:
    # This file is not a source to scan: its examples hold dead references on purpose.
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in SOURCE_SUFFIXES
        and not NOT_SOURCES & set(path.relative_to(root).parts)
        and path != Path(__file__).resolve()
    )


def adr_numbers(root: Path) -> set[str]:
    return {path.name[:4] for path in (root / ADR_DIRECTORY).glob("[0-9][0-9][0-9][0-9]-*.md")}


def dead_adr_references(root: Path) -> list[str]:
    """Each reference to an ADR in the tree under `root` that names no file, as `file: reference`."""
    numbers = adr_numbers(root)
    dead = []
    for path in source_files(root):
        text = path.read_text()
        shown = path.relative_to(root)
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://")):
                # A page of the docs site links a file outside docs/ with a full URL that holds /blob/main/<path>.
                if "/blob/main/" not in target:
                    continue
                resolved = root / target.split("/blob/main/", 1)[1]
            elif target.startswith("mailto:"):
                continue
            else:
                resolved = (path.parent / target).resolve()
            if ADR_FILE.search(target) and not resolved.is_file():
                dead.append(f"{shown}: {target}")
        for target in ADR_PATH_IN_TICKS.findall(text):
            if not (root / target).exists():
                dead.append(f"{shown}: {target}")
        dead += [f"{shown}: ADR {number}" for number in CITATION.findall(text) if number not in numbers]
    return dead


@outside_the_package
def test_the_adrs_are_in_docs_adr_and_nowhere_else():
    found = sorted(str(path.relative_to(ROOT)) for path in ROOT.rglob("adr/*.md") if not NOT_SOURCES & set(path.relative_to(ROOT).parts))

    assert found, "the repository holds ADRs, so this test would check nothing"
    assert [name for name in found if not name.startswith("docs/adr/")] == []


@outside_the_package
def test_every_reference_to_an_adr_in_the_repository_names_a_file_that_exists():
    assert dead_adr_references(ROOT) == []


def tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


def test_a_link_to_an_adr_that_does_not_exist_is_reported(tmp_path):
    root = tree(tmp_path, {
        "docs/adr/0001-first.md": "# ADR 0001: First\n",
        "docs/page.md": "See [the ADR](adr/0002-missing.md).\n",
    })

    assert dead_adr_references(root) == ["docs/page.md: adr/0002-missing.md"]


def test_a_link_to_an_adr_in_its_old_directory_is_reported(tmp_path):
    root = tree(tmp_path, {
        "docs/adr/0001-first.md": "# ADR 0001: First\n",
        "skills/x/SKILL.md": "See [ADR 0001](docs/adr/0001-first.md).\n",
    })

    assert dead_adr_references(root) == ["skills/x/SKILL.md: docs/adr/0001-first.md"]


def test_a_full_url_to_an_adr_that_does_not_exist_is_reported(tmp_path):
    root = tree(tmp_path, {
        "docs/adr/0001-first.md": "# ADR 0001: First\n",
        "docs/page.md": "[a](https://example.org/r/blob/main/docs/adr/0001-first.md) and [b](https://example.org/r/blob/main/skills/adr/0001-first.md)\n",
    })

    assert dead_adr_references(root) == ["docs/page.md: https://example.org/r/blob/main/skills/adr/0001-first.md"]


def test_a_path_to_the_adr_directory_in_code_ticks_that_does_not_exist_is_reported(tmp_path):
    root = tree(tmp_path, {
        "docs/adr/0001-first.md": "# ADR 0001: First\n",
        "template.md": "The ADRs in `docs/adr/` and in `skills/x/docs/adr/`.\n",
    })

    assert dead_adr_references(root) == ["template.md: skills/x/docs/adr/"]


def test_a_citation_of_an_adr_number_that_does_not_exist_is_reported(tmp_path):
    root = tree(tmp_path, {
        "docs/adr/0001-first.md": "# ADR 0001: First\n",
        "src/code.py": "# Cited twice: ADR 0001 and ADR 0007.\n",
    })

    assert dead_adr_references(root) == ["src/code.py: ADR 0007"]


def test_a_tree_whose_references_all_resolve_has_no_dead_reference(tmp_path):
    root = tree(tmp_path, {
        "docs/adr/0001-first.md": "# ADR 0001: First\n\nSee [the code](../../src/code.py).\n",
        "docs/page.md": "See [ADR 0001](adr/0001-first.md) and `docs/adr/`.\n",
        "src/code.py": "# ADR 0001 explains this.\n",
    })

    assert dead_adr_references(root) == []
