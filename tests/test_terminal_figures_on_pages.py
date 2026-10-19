"""The pages show the terminal figures of `delegate docs`, each with its plain-text alternative under it (#139).

A page shows a figure with two image lines, one after the other: the light file with the fragment
`#gh-light-mode-only`, then the dark file with the fragment `#gh-dark-mode-only`, as the lineage figure does
(`tests/test_lineage.py`). The site and GitHub show the file of the scheme of the reader. The first fenced block under
the two lines is the text alternative of the figure: the plain text of the frame, as the code prints it with `NO_COLOR`
set (`delegate.docs.figures.frame_lines`). So a figure and its text cannot tell different stories, and a page names
no figure file that `delegate docs` does not write.

The image lines are prose that a writer puts on a page: they are never in a generated section. The figure files are
never edited by hand: `delegate docs` writes them, and `tests/test_terminal_figures.py` holds their drift test.

The pages are outside the package source, so a Nix build that copies only the package skips these cases with a reason.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from delegate.docs import figures

from .pages import ROOT, outside_the_package

DOCS = ROOT / "docs"

# Each page that shows a figure: the figures in the order of the page, each with the heading of the section that holds
# it. The README and the overview show the live view near the top, before their first section.
PLACES: dict[str, list[tuple[str, str]]] = {
    "README.md": [("watch-live", "# delegate")],
    "docs/overview.md": [("watch-live", "# Start here")],
    "docs/tutorial.md": [("watch-live", "## 7. Run the ticket")],
    "docs/reference/status-and-watch.md": [
        ("status-table", "## `delegate status`"),
        ("watch-picker", "### The run picker"),
        ("watch-live", "### The live view"),
        ("watch-fixup", "### The live view"),
        ("watch-paused", "### The live view"),
        ("watch-fit", "### The live view"),
    ],
}

FRAGMENTS = {"light": "#gh-light-mode-only", "dark": "#gh-dark-mode-only"}

IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)|<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']")
OPENING_FENCE = re.compile(r"^(`{3,})(\S*)\s*$")
GENERATED = re.compile(r"<!-- generated:begin .*?<!-- generated:end [^>]*-->", flags=re.DOTALL)


@dataclass(frozen=True)
class Shown:
    """One figure on a page: its name, the heading of its section, the targets and the alt texts of its two image
    lines, the text of the first fenced block under them (or None), and the lines between."""

    name: str
    heading: str
    targets: tuple[str, ...]
    alts: tuple[str, ...]
    block: list[str] | None
    between: list[str]


def terminal_images(text: str) -> list[tuple[int, str, str]]:
    """Each image of a page whose target is under `assets/terminal/`: its line index, its alt text and its target."""
    found = []
    for index, line in enumerate(text.splitlines()):
        for match in IMAGE.finditer(line):
            alt, target = (match.group(1) or "", match.group(2)) if match.group(2) else ("", match.group(3))
            if "assets/terminal/" in target:
                found.append((index, alt, target))
    return found


def figure_name(target: str) -> str:
    """The figure of an image target: `../assets/terminal/watch-live-dark.svg#x` gives `watch-live`."""
    return re.sub(r"-(light|dark)$", "", Path(target.split("#")[0]).stem)


def outside_fences(lines: list[str]) -> list[str]:
    """The lines of a page, with each line of a fenced block, and each fence, blank."""
    result, closing = [], ""
    for line in lines:
        opened = OPENING_FENCE.match(line)
        if closing:
            if line.strip() == closing:
                closing = ""
            result.append("")
        elif opened:
            closing = opened.group(1)
            result.append("")
        else:
            result.append(line)
    return result


def shown(page: Path) -> list[Shown]:
    """The figures of a page, in order. Two image lines of one figure, one after the other, are one figure."""
    lines = page.read_text().splitlines()
    prose = outside_fences(lines)
    images = terminal_images("\n".join(prose))
    headings = [line if line.startswith("#") else "" for line in prose]
    result = []
    position = 0
    while position < len(images):
        group = [images[position]]
        while position + len(group) < len(images):
            following = images[position + len(group)]
            if following[0] != group[-1][0] + 1 or figure_name(following[2]) != figure_name(group[0][2]):
                break
            group.append(following)
        position += len(group)
        last = group[-1][0]
        heading = next(line for line in reversed(headings[: group[0][0]]) if line)
        block, between = None, []
        for index in range(last + 1, len(lines)):
            opened = OPENING_FENCE.match(lines[index])
            if opened:
                close = next(i for i in range(index + 1, len(lines)) if lines[i].strip() == opened.group(1))
                block = lines[index + 1:close]
                break
            between.append(lines[index])
        result.append(Shown(
            figure_name(group[0][2]), heading, tuple(target for _, _, target in group), tuple(alt for _, alt, _ in group),
            block, between,
        ))
    return result


def occurrence(page: str, index: int) -> Shown:
    """The figure at `index` of the figures of a page."""
    found = shown(ROOT / page)
    assert len(found) > index, f"{page} shows {len(found)} terminal figures, not {len(PLACES[page])}"
    return found[index]


def figure(name: str) -> figures.Figure:
    return next(f for f in figures.FIGURES if f.name == name)


def markdown_files() -> list[str]:
    """Each Markdown file of the repository that git tracks or that is new and not ignored."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.md"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(path for path in listed if (ROOT / path).is_file())


OCCURRENCES = [(page, index) for page, places in PLACES.items() for index in range(len(places))]


@outside_the_package
@pytest.mark.parametrize("page", sorted(PLACES))
def test_each_page_shows_its_figures_in_order_each_at_the_section_that_describes_its_frame(page):
    assert [(item.name, item.heading) for item in shown(ROOT / page)] == PLACES[page]


@outside_the_package
@pytest.mark.parametrize(("page", "index"), OCCURRENCES)
def test_each_figure_on_a_page_names_its_light_and_its_dark_file_and_both_exist(page, index):
    name, _ = PLACES[page][index]
    item = occurrence(page, index)
    expected = tuple(
        Path(os.path.relpath(ROOT / figure(name).path(scheme), (ROOT / page).parent)).as_posix() + fragment
        for scheme, fragment in FRAGMENTS.items()
    )

    assert item.targets == expected
    assert [alt for alt in item.alts if not alt.strip()] == []
    assert len(set(item.alts)) == 1  # the two lines are one figure, with one description
    assert [scheme for scheme in FRAGMENTS if not (ROOT / figure(name).path(scheme)).is_file()] == []


@outside_the_package
@pytest.mark.parametrize(("page", "index"), OCCURRENCES)
def test_the_text_alternative_under_each_figure_is_the_plain_text_of_its_frame(page, index):
    name, _ = PLACES[page][index]
    item = occurrence(page, index)
    frame = figures.frame_lines(figure(name), colour=False)

    assert item.block is not None, f"{page}: the figure {name} has no text block under it"
    assert [line for line in item.between if line.strip()] == [], f"{page}: only blank lines go between the figure {name} and its text"
    # A space at the end of a line does not show, and an editor removes it. Each other character is the frame.
    assert item.block == [line.rstrip() for line in frame], (
        f"{page}: the text under the figure {name} is not the plain text of its frame. Put this text under it:\n" + "\n".join(frame)
    )


@outside_the_package
def test_no_page_names_an_image_under_the_terminal_assets_that_delegate_docs_does_not_write():
    written = {(ROOT / figure.path(scheme)).resolve() for figure in figures.FIGURES for scheme in figures.SCHEMES}
    found = []
    for page in markdown_files():
        for index, _, target in terminal_images((ROOT / page).read_text()):
            if (ROOT / page).parent.joinpath(target.split("#")[0]).resolve() not in written:
                found.append(f"{page}:{index + 1}: {target}")

    assert found == []


@outside_the_package
def test_no_figure_of_terminal_output_is_in_a_generated_section():
    found = [
        f"{page}: {target}" for page in markdown_files()
        for section in GENERATED.findall((ROOT / page).read_text())
        for _, _, target in terminal_images(section)
    ]

    assert found == []


def test_the_page_parser_joins_the_two_lines_of_a_figure_and_finds_the_text_under_it(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(
        "# Page\n\n## Part\n\n"
        "![The frame.](assets/terminal/watch-live-light.svg#gh-light-mode-only)\n"
        "![The frame.](assets/terminal/watch-live-dark.svg#gh-dark-mode-only)\n\n"
        "```text\nrun 7c1e\n```\n\n"
        "## Other part\n\n"
        "```text\n# not a heading\n![In a block.](assets/terminal/watch-fit-light.svg)\n```\n\n"
        "![A ladder.](assets/ladder-light.svg)\n\n"
        '<img src="assets/terminal/watch-mock.png" alt="mock">\n'
    )

    first, second = shown(page)

    assert (first.name, first.heading, first.block, first.between) == ("watch-live", "## Part", ["run 7c1e"], [""])
    assert first.targets == (
        "assets/terminal/watch-live-light.svg#gh-light-mode-only", "assets/terminal/watch-live-dark.svg#gh-dark-mode-only",
    )
    assert (second.name, second.heading, second.targets, second.block) == (
        "watch-mock", "## Other part", ("assets/terminal/watch-mock.png",), None,
    )
