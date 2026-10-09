"""The lineage of the kit on the front door: the ladder visual, and the credit to the work that the kit builds on.

The overview shows the eight levels of agentic engineering of Bassim Eledath as
rows, and delegate as one vertical slice through them. The visual is two
checked-in SVG files, one for the light scheme and one for the dark scheme. The
image fragments `#gh-light-mode-only` and `#gh-dark-mode-only` show the right
one on the site (the palette toggle) and on GitHub. A Markdown table under the
visual is its text alternative, and it holds the same information.

The overview and the README credit Eledath and Matt Pocock, with a link to the
primary source of each, and name the four planning skills.

The pages are outside the package source, so a Nix build that copies only the
package skips these cases with a reason.
"""

import re
import xml.etree.ElementTree as ET

from .pages import ROOT, outside_the_package

DOCS = ROOT / "docs"
OVERVIEW = DOCS / "overview.md"
README = ROOT / "README.md"

# The eight level names, word for word from the headings of the article (read 2026-10-09).
# The article puts levels 1 and 2 under one heading, "Levels 1 & 2: Tab Complete and Agent IDE".
LEVELS = [
    "Tab Complete",
    "Agent IDE",
    "Context Engineering",
    "Compounding Engineering",
    "MCP and Skills",
    "Harness Engineering & Automated Feedback Loops",
    "Background Agents",
    "Autonomous Agent Teams",
]

ELEDATH = "https://www.bassimeledath.com/blog/levels-of-agentic-engineering"
POCOCK = "https://github.com/mattpocock/skills"
PLANNING_SKILLS = ["grilling", "wayfinder", "to-spec", "to-tickets"]

# Each version of the visual, by the image fragment that shows it in one colour scheme.
VERSIONS = {"assets/ladder-light.svg": "#gh-light-mode-only", "assets/ladder-dark.svg": "#gh-dark-mode-only"}

SVG = "{http://www.w3.org/2000/svg}"
FENCED = re.compile(r"^(`{3,}).*?^\1$", flags=re.DOTALL | re.MULTILINE)
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
LINK_TEXT = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def section(title: str) -> str:
    """The text of one level-two section of the overview, outside its fenced blocks."""
    parts = re.split(r"^## (.+)$", FENCED.sub("", OVERVIEW.read_text()), flags=re.MULTILINE)
    sections = dict(zip(parts[1::2], parts[2::2]))
    assert title in sections, f"the overview has no section '{title}'"
    return sections[title]


def plain(cell: str) -> str:
    """The text of a table cell, without links, bold or code marks."""
    return " ".join(LINK_TEXT.sub(r"\1", cell).replace("**", "").replace("`", "").split())


def text_alternative() -> list[tuple[str, str, str]]:
    """The rows of the table in the ladder section: the level number, the level name, and what delegate puts there."""
    rows = []
    for line in section("The ladder").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and len(cells) == 3 and cells[0].isdigit():
            rows.append((cells[0], plain(cells[1]), plain(cells[2])))
    return rows


def svg_texts(path: str) -> list[str]:
    """The text of each <text> element of one version of the visual, in document order, with its spaces normalised."""
    root = ET.parse(DOCS / path).getroot()
    return [" ".join("".join(element.itertext()).split()) for element in root.iter(f"{SVG}text")]


@outside_the_package
def test_the_ladder_section_shows_a_light_and_a_dark_version_of_the_visual_each_with_alt_text():
    images = {target: alt for alt, target in IMAGE.findall(section("The ladder"))}

    assert sorted(images) == sorted(path + fragment for path, fragment in VERSIONS.items())
    assert [target for target, alt in images.items() if not alt.strip()] == []
    assert [path for path in VERSIONS if not (DOCS / path).is_file()] == []


@outside_the_package
def test_each_version_of_the_visual_is_an_svg_image_with_a_title_and_a_description():
    for path in VERSIONS:
        root = ET.parse(DOCS / path).getroot()
        assert root.tag == f"{SVG}svg", path
        assert root.get("role") == "img", path
        assert (root.findtext(f"{SVG}title") or "").strip(), f"{path} has no <title>"
        assert (root.findtext(f"{SVG}desc") or "").strip(), f"{path} has no <desc>"


@outside_the_package
def test_the_two_versions_of_the_visual_differ_only_in_their_colours():
    # The colours are in the <style> element. All the rest, so all the information, is the same in both files.
    light, dark = (re.sub(r"<style>.*?</style>", "", (DOCS / path).read_text(), flags=re.DOTALL) for path in VERSIONS)
    assert light == dark


@outside_the_package
def test_the_visual_names_the_eight_levels_in_order():
    for path in VERSIONS:
        assert [text for text in svg_texts(path) if text in LEVELS] == LEVELS, path


@outside_the_package
def test_the_text_alternative_names_the_eight_levels_in_order():
    rows = text_alternative()

    assert [number for number, _, _ in rows] == [str(level) for level in range(1, 9)]
    assert [name for _, name, _ in rows] == LEVELS


@outside_the_package
def test_the_text_alternative_says_what_delegate_puts_at_each_level_and_that_level_8_is_not_attempted():
    rows = text_alternative()

    assert [name for _, name, put in rows if not put] == []
    assert rows[-1][2].casefold().startswith("not attempted")
    assert any(text.casefold().startswith("not attempted") for text in svg_texts("assets/ladder-light.svg"))


@outside_the_package
def test_the_overview_and_the_readme_credit_eledath_and_pocock_with_a_link_and_name_the_four_planning_skills():
    for page in (OVERVIEW, README):
        text = page.read_text()
        assert "Bassim Eledath" in text and f"]({ELEDATH})" in text, page.name
        assert "Matt Pocock" in text and f"]({POCOCK})" in text, page.name
        assert [skill for skill in PLANNING_SKILLS if f"`{skill}`" not in text] == [], page.name
