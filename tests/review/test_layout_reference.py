"""The layout reference lists each module of the package, and the glossary has the terms of the run view (#355).

The page says that it lists the modules of each layer. A module that a change adds is
missing from the page until a test compares the page with the files of `src/delegate/`.
The page is outside the package source, as the README is, so a Nix build that copies
only the package skips these cases with a reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ..pages import ROOT, outside_the_package

PACKAGE = ROOT / "src" / "delegate"
LAYOUT = ROOT / "docs" / "reference" / "layout.md"
GLOSSARY = ROOT / "docs" / "glossary.md"

# A data file that sits beside the module that loads it is part of the layout too.
SUFFIXES = {".py", ".toml"}


def modules_of(package: Path) -> list[str]:
    """The path of each module and data file under `package`, as `layer/name.py`. A package `__init__.py` is no module of its own."""
    return sorted(
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.suffix in SUFFIXES and path.name != "__init__.py" and "__pycache__" not in path.parts
    )


def listed_on(page: str) -> set[str]:
    """Each path in backticks in the table of the package: the section from `## The package` to the next heading."""
    section = page.split("\n## The package\n", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"`([a-z_]+/[a-z_/]+\.(?:py|toml))`", section))


def missing_from(page: str, package: Path) -> list[str]:
    listed = listed_on(page)
    return [module for module in modules_of(package) if module not in listed]


@outside_the_package
def test_the_layout_page_lists_each_module_of_the_package():
    assert missing_from(LAYOUT.read_text(), PACKAGE) == []


@outside_the_package
def test_the_layout_page_lists_no_module_that_is_not_in_the_package():
    existing = set(modules_of(PACKAGE)) | {f"{layer.name}/__init__.py" for layer in PACKAGE.iterdir() if layer.is_dir()}
    assert sorted(entry for entry in listed_on(LAYOUT.read_text()) if entry not in existing) == []


@outside_the_package
def test_a_module_that_the_page_does_not_list_is_reported(tmp_path):
    """The comparison fails on a module that the page misses: it does not pass because it reads nothing."""
    package = tmp_path / "delegate"
    for name in ("run/listed.py", "run/planted.py", "shared/data.toml"):
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_text("")
    (package / "run" / "__init__.py").write_text("")
    page = "# The page\n\n## The package\n\n| `run/listed.py` | x |\n| `shared/data.toml` | y |\n\n## The next section\n"

    assert missing_from(page, package) == ["run/planted.py"]


# The terms of the new run view: each has its own entry in the glossary.
RUN_VIEW_TERMS = (
    "Fleet",
    "Host reader",
    "Lane",
    "Run context",
    "Run state",
    "Slip",
    "Snapshot",
    "Store of last answers",
    "Trend",
)


def glossary_headings() -> set[str]:
    return set(re.findall(r"^## (.+)$", GLOSSARY.read_text(), flags=re.MULTILINE))


@outside_the_package
@pytest.mark.parametrize("term", RUN_VIEW_TERMS)
def test_the_glossary_has_an_entry_for_each_term_of_the_run_view(term):
    assert term in glossary_headings()
