"""The explanation page "Why the model should not matter": the lineage of the kit, the thesis, five lessons, and which model verifies.

The page goes from the pain to the idea to the mechanism: first the lineage
(a model router, then rules for each model in prose, then contracts for each
role, then the engine), then the thesis as a table, then five lessons, then the
stance on which model verifies. The stance is a recommendation, and the page
must name the same tier floor as the workflow reference, because that floor is
the only rule that the engine enforces.

Each quotation of Bassim Eledath must be verbatim. The test holds the
quotations that were checked word for word against the article, and a quotation
on the page must be one of them, or a part of one.

The lineage has a visual: two checked-in SVG files, one for each colour scheme,
which differ only in their colours. A Markdown table under the visual holds the
same information, and the test checks that the marks of the visual and the
cells of the table agree.

The pages are outside the package source, so a Nix build that copies only the
package skips these cases with a reason.
"""

import re
import xml.etree.ElementTree as ET

from .pages import ROOT, outside_the_package

DOCS = ROOT / "docs"
PAGE = DOCS / "explanation" / "why-the-model-should-not-matter.md"
GLOSSARY = DOCS / "glossary.md"
WORKFLOW_REFERENCE = DOCS / "reference" / "workflow.md"
ELEDATH = "https://www.bassimeledath.com/blog/levels-of-agentic-engineering"

# The level-two sections of the page, in the order of the ticket.
SECTIONS = [
    "How the kit got its shape",
    "What varies between models, and what fixes it",
    "Five lessons",
    "Which model verifies: a recommendation, not a rule",
]

# The four steps of the lineage, in order. Each is the start of a level-three heading of the first section.
STEPS = ["First, a model router", "Then, rules for each model", "Then, contracts for each role", "Then, the engine"]

# The things that vary between models: the first column of the thesis table, in order.
VARIES = ["Context", "Planning and scope", "Tool use", 'The claim of "done"', "The code"]

# Each quotation of Eledath that the page may use, word for word from the article (read 2026-10-09).
QUOTES = [
    "Constraints > instructions.",
    "defining boundaries works better than giving checklists, because agents fixate on the list and ignore anything not on it.",
    "if the same model instance implements and evaluates its own work, it's biased.",
    "Have a different model (or a different instance with a review-specific prompt) do the review pass.",
    "Don't let the same model grade its own exam — separate the implementer from the reviewer",
    "use different models for different jobs.",
    "the cumulative output is stronger than any single model working alone.",
]

# The two quotations that the ticket names.
NAMED_QUOTES = ["Constraints > instructions.", "Don't let the same model grade its own exam"]

# Each version of the visual, by the image fragment that shows it in one colour scheme.
VERSIONS = {"assets/lineage-light.svg": "#gh-light-mode-only", "assets/lineage-dark.svg": "#gh-dark-mode-only"}
SVG = "{http://www.w3.org/2000/svg}"

FENCED = re.compile(r"^(`{3,}).*?^\1$", flags=re.DOTALL | re.MULTILINE)
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
LINK_TEXT = re.compile(r"\[([^\]]+)\]\([^)]+\)")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
BOLD_GLOSSARY_LINK = re.compile(r"\[\*\*([^*]+)\*\*\]\(\.\./glossary\.md#([^)\s]+)\)")


def prose() -> str:
    """The text of the page outside its fenced blocks."""
    return FENCED.sub("", PAGE.read_text())


def sections() -> dict[str, str]:
    parts = re.split(r"^## (.+)$", prose(), flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


def plain(cell: str) -> str:
    """The text of a table cell, without links, bold or code marks."""
    return " ".join(LINK_TEXT.sub(r"\1", cell).replace("**", "").replace("`", "").split())


def table_rows(text: str) -> list[list[str]]:
    """The body rows of the Markdown tables in a text, as plain cells. The header and the rule rows are left out."""
    rows = []
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    for number, line in enumerate(lines):
        cells = [plain(cell) for cell in line.strip("|").split("|")]
        is_rule = set("".join(cells)) <= set("-: ")
        is_header = number + 1 < len(lines) and set(lines[number + 1].replace("|", "")) <= set("-: ")
        if not is_rule and not is_header:
            rows.append(cells)
    return rows


def slug(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def glossary_anchors() -> dict[str, str]:
    """Each term of the glossary, without backticks or case, mapped to the anchor of its entry."""
    headings = re.findall(r"^## (.+)$", FENCED.sub("", GLOSSARY.read_text()), flags=re.MULTILINE)
    return {heading.replace("`", "").casefold(): slug(heading) for heading in headings}


@outside_the_package
def test_the_page_gives_its_sections_in_the_order_of_the_ticket():
    assert re.findall(r"^## (.+)$", prose(), flags=re.MULTILINE) == SECTIONS


@outside_the_package
def test_the_lineage_tells_the_four_steps_in_order():
    headings = re.findall(r"^### (.+)$", sections()[SECTIONS[0]], flags=re.MULTILINE)
    started = [next((heading for heading in headings if heading.startswith(step)), None) for step in STEPS]

    assert None not in started, dict(zip(STEPS, started))
    assert [headings.index(heading) for heading in started] == sorted(headings.index(heading) for heading in started)


@outside_the_package
def test_the_thesis_table_names_what_varies_and_leaves_the_code_free_on_purpose():
    rows = table_rows(sections()[SECTIONS[1]])

    assert [row[0] for row in rows] == VARIES
    assert "on purpose" in rows[-1][1]
    planning = rows[VARIES.index("Planning and scope")][1]
    assert "before the kit sees the work" in planning and "grilling" in planning


@outside_the_package
def test_the_lessons_section_holds_five_lessons():
    assert len(re.findall(r"^### ", sections()[SECTIONS[2]], flags=re.MULTILINE)) == 5


@outside_the_package
def test_every_quotation_in_a_paragraph_that_names_eledath_is_verbatim_from_the_article():
    paragraphs = [paragraph for paragraph in re.split(r"\n\s*\n", prose()) if "Eledath" in paragraph]
    quoted = [quote for paragraph in paragraphs for quote in re.findall(r'"([^"]+)"', paragraph)]

    assert [quote for quote in quoted if not any(quote in verified for verified in QUOTES)] == []
    assert [quote for quote in NAMED_QUOTES if not any(quote in found for found in quoted)] == []
    assert f"]({ELEDATH})" in PAGE.read_text()


@outside_the_package
def test_the_verifier_section_recommends_one_tier_above_and_says_that_it_is_not_enforced():
    text = " ".join(sections()[SECTIONS[3]].split())

    assert "one tier above the specialist" in text
    assert "not enforced" in text
    assert "different model family is not required" in text


@outside_the_package
def test_the_verifier_section_names_the_tier_floor_of_the_workflow_reference():
    reference = " ".join(WORKFLOW_REFERENCE.read_text().split())
    allowed = re.search(r"must name a tier that is as strong as the tier `verifier`: ([^.]+)\.", reference)
    weaker = re.search(r"A weaker tier \(([^)]+)\) is an error", reference)
    assert allowed and weaker, "the workflow reference no longer states the tier floor in the form that this test reads"
    text = " ".join(sections()[SECTIONS[3]].split())

    tiers = re.findall(r"`(\w+)`", allowed.group(1)) + re.findall(r"`(\w+)`", weaker.group(1))
    assert len(tiers) == 4
    assert [tier for tier in tiers if f"`{tier}`" not in text] == []
    assert "`tier-overrides.verifier`" in text
    assert "../reference/workflow.md#tier-overrides" in text


@outside_the_package
def test_every_term_in_bold_links_its_glossary_entry_once():
    text = prose()
    bold = BOLD.findall(text)
    linked = BOLD_GLOSSARY_LINK.findall(text)
    anchors = glossary_anchors()

    assert len(bold) >= 10, "the page links the glossary terms at their first use"
    assert [term for term in bold if term not in {name for name, _ in linked}] == []
    assert [(name, anchor) for name, anchor in linked if anchors.get(name.replace("`", "").casefold()) != anchor] == []
    terms = [term.replace("`", "").casefold() for term in bold]
    assert sorted({term for term in terms if terms.count(term) > 1}) == []


def svg_marks(path: str) -> dict[str, list[str]]:
    """The marks of one version of the visual: for each rule, the class of the mark at each of the four stages, or "none"."""
    root = ET.parse(DOCS / path).getroot()
    marks = {}
    for group in root.iter(f"{SVG}g"):
        rule = group.get("data-rule")
        if rule is None:
            continue
        stages = ["none"] * 4
        for circle in group.iter(f"{SVG}circle"):
            stages[int(circle.get("data-stage")) - 1] = circle.get("class")
        marks[rule] = stages
    return marks


def lineage_images() -> dict[str, str]:
    return {target.removeprefix("../"): alt for alt, target in IMAGE.findall(sections()[SECTIONS[0]])}


@outside_the_package
def test_the_lineage_shows_a_light_and_a_dark_version_of_the_visual_each_with_alt_text():
    images = lineage_images()

    assert sorted(images) == sorted(path + fragment for path, fragment in VERSIONS.items())
    assert [target for target, alt in images.items() if not alt.strip()] == []


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
    light, dark = (re.sub(r"<style>.*?</style>", "", (DOCS / path).read_text(), flags=re.DOTALL) for path in VERSIONS)
    assert light == dark


@outside_the_package
def test_the_table_under_the_visual_holds_the_same_marks_as_the_visual():
    # The table follows the images in the lineage section. Its columns: the rule, then one cell for each stage.
    lineage = sections()[SECTIONS[0]]
    after = lineage.split("lineage-dark.svg", 1)[1]
    table = {row[0]: [cell.casefold() if cell in ("Prose", "Structure") else "none" for cell in row[1:]] for row in table_rows(after)}
    marks = svg_marks("assets/lineage-light.svg")

    assert len(table) == 6
    assert table == marks
    assert all(stages[-1] == "structure" for rule, stages in table.items() if "coordinator" not in rule)
    assert [stages for rule, stages in table.items() if "coordinator" in rule] == [["prose"] * 4]
