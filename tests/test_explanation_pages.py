"""The explanation pages: the reasons for the design, in the maintainer's voice.

A reader of an explanation page does not follow steps. The reader learns why
the kit works as it does. So these tests check that each page exists, that its
links work, that it speaks in the first person, and that it gives the reasons
its ticket names. The pages are outside the package source, so a Nix build that
copies only the package skips these cases with a reason.
"""

import datetime
import re
import tomllib

import pytest

from .pages import ROOT, outside_the_package

EXPLANATION = ROOT / "docs" / "explanation"

PAGES = [
    "why-the-verifier-is-blind.md",
    "why-each-specialist-has-its-own-verifier.md",
    "the-mode-trade-off.md",
    "prior-art.md",
    "the-enforcement-model-and-its-limits.md",
    "decision-records.md",
    "why-the-model-should-not-matter.md",
    "why-the-code-has-this-shape.md",
    "the-concepts-behind-the-words.md",
]

# The reasons that each page must give, as a pattern for each reason. The reasons come from the ticket and the spec.
REASONS: dict[str, list[tuple[str, str]]] = {
    "why-the-verifier-is-blind.md": [
        ("a verifier that reads the report anchors on its framing", r"anchor"),
        ("the verifier gets the task and the diff, never the report", r"never (gets? )?the (specialist's )?report"),
        ("the claim that the tests pass is not evidence", r"claim"),
        ("the engine runs the gates outside the specialist", r"engine runs (the|your) gates"),
        ("the verifier runs the gates in a copy it may break", r"temporary copy"),
    ],
    "why-each-specialist-has-its-own-verifier.md": [
        ("the verifier holds the same skills as the specialist", r"same skills"),
        ("a verifier without the skills reads the code and misses the discipline", r"misses the discipline"),
        ("the verifier has a read-only tool set", r"read-only"),
        ("one declaration renders both agents, so the pair cannot drift", r"one declaration"),
        ("a generic verifier is right only for docs and configuration", r"generic verifier"),
        ("a monorepo has one pair for each stack", r"each stack"),
    ],
    "the-mode-trade-off.md": [
        ("assure verifies each branch at once and gives up tokens and time", r"`assure`.*at once"),
        ("economy verifies at the end of the chain and gives up early discovery", r"`economy`.*early"),
        ("a defect of the first ticket in economy shows late, after later tickets built on it", r"built on it"),
        ("economy is for the fewest tokens", r"fewest tokens"),
        ("a cheaper mode never means a weaker verifier", r"weaker verifier"),
        ("the trust rules are the same in both modes", r"invariants"),
        ("the page says how to choose", r"choose"),
    ],
    "the-enforcement-model-and-its-limits.md": [
        ("the engine installs a pre-push hook in each worktree", r"pre-push"),
        ("the engine checks the diff against the hotspot paths", r"hotspot"),
        ("the engine checks that the real worktree is unchanged after a verifier", r"unchanged"),
        ("the guards use git, so they hold in every harness", r"every harness"),
        ("the harness hooks in the agent files are a second guard", r"second guard"),
        ("the verifier guard is a contract, not a sandbox", r"not a sandbox"),
        ("the push hook does not stop a push with --no-verify", r"--no-verify"),
    ],
    "decision-records.md": [
        ("the page says what an ADR is for", r"decision"),
    ],
    "prior-art.md": [
        ("the page says when to choose this kit and when to choose another tool", r"choose"),
        ("the page says the kit takes the planning output of mattpocock/skills as input", r"to-tickets"),
        ("the page says the kit takes that output as its input and would not exist in this form without it", r"would not exist in this form without them"),
    ],
    "why-the-model-should-not-matter.md": [
        ("the kit began as a model router", r"model router"),
        ("the lineage names the maintainer's projects only in general terms", r"own web apps and projects"),
        ("the rules for each model were prose", r"rules for each model"),
        ("one declaration renders the specialist and its verifier", r"one \W*declaration\W.*its verifier"),
        ("a validator turns the rules into build failures", r"build failures?"),
        ("the engine runs the gates itself", r"engine runs the gates itself"),
        ("constraints beat instructions", r"constraints beat instructions"),
        ("the inputs of a run are files", r"inputs of a run are files"),
        ("the coordinator role is not yet fixed", r"not yet fixed"),
        ("the verifier recommendation is not enforced", r"not enforced"),
    ],
    "why-the-code-has-this-shape.md": [
        ("there are two contexts, run and definitions", r"two contexts"),
        ("the contexts know nothing of each other", r"nothing of each other"),
        ("the tier table is the one thing that they share", r"tier table"),
        ("a port is an interface that the domain drives", r"ports?\b.*\bdrives?"),
        ("the three ports are the harness, version control and the gates", r"(?s)harness port.*version-control port.*gate port"),
        ("the claude-code and opencode adapters implement the harness port", r"(?s)claude-code.*opencode"),
        ("the git backend implements the version-control port", r"git backend"),
        ("the shell gate runner implements the gate port", r"shell gate runner"),
        ("only the cli package parses command-line arguments", r"(?s)only .*cli.* parses? command-line arguments"),
        ("the docs tooling reads the code of the other layers", r"docs tooling"),
        ("dependencies point inward", r"point inward"),
        ("a test holds the rule", r"tests/test_architecture\.py"),
        ("the run context starts no process", r"starts no process"),
        ("the page sends the reader to the layout reference for the detail", r"layout"),
    ],
    "the-concepts-behind-the-words.md": [
        ("a harness is the program that runs a coding agent, and an adapter holds what is particular to one", r"(?s)program that runs a coding agent.*adapter holds"),
        ("a skill is a directory with a SKILL.md file", r"(?s)directory with a `skill\.md` file"),
        ("a session lets the engine go back into the same conversation for a continuation", r"(?s)session id.*continuation"),
        ("a gateway serves models under its own names, so a tier file changes no workflow", r"(?s)own names.*changes no workflow"),
        ("a spec is the plan from which the tickets come", r"plan of a piece of work"),
        ("a step is the work of the engine for one ticket and the unit of failure", r"unit of failure"),
        ("a three-dot diff shows the changes since the merge base", r"merge base"),
        ("an event stream is line-delimited json that the adapter reads for the end state", r"(?s)line-delimited json.*end state"),
        ("the neutrality check reads a private denylist and an allowlist names the exceptions", r"(?s)private.*allowlist"),
        ("ASD-STE100 is controlled english for the text that agents write", r"(?s)controlled english.*text that agents write"),
    ],
}


def prose(page: str) -> str:
    """The text of the page outside its fenced blocks."""
    text = (EXPLANATION / page).read_text()
    return re.sub(r"^(`{3,}).*?^\1$", "", text, flags=re.DOTALL | re.MULTILINE)


def anchors(markdown: str) -> set[str]:
    """The anchors of the headings of a page, as GitHub builds them."""
    found = set()
    for heading in re.findall(r"^#{1,6} (.+)$", re.sub(r"^(`{3,}).*?^\1$", "", markdown, flags=re.DOTALL | re.MULTILINE), flags=re.MULTILINE):
        found.add(re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-"))
    return found


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_every_relative_link_reaches_a_file_and_a_heading(page):
    broken = []
    for target in re.findall(r"\]\(([^)\s]+)\)", (EXPLANATION / page).read_text()):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        file = (EXPLANATION / path).resolve() if path else EXPLANATION / page
        if not file.is_file():
            broken.append(target)
        # Only a Markdown page has headings. The fragment of an image (`#gh-dark-mode-only`) selects a colour scheme.
        elif anchor and file.suffix == ".md" and anchor not in anchors(file.read_text()):
            broken.append(target)
    assert broken == []


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_a_page_speaks_in_the_first_person_of_the_maintainer(page):
    assert re.search(r"\bI\b", prose(page)), "an explanation page is in the maintainer's own voice"


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_a_page_gives_the_reasons_of_its_ticket(page):
    # A glossary link reads as its text: "[**run**](../glossary.md#run)" is the word "run".
    text = re.sub(r"\[\*\*([^*\]]+)\*\*\]\([^)]*\)", r"\1", prose(page)).lower()
    missing = [reason for reason, pattern in REASONS[page] if not re.search(pattern, text)]
    assert missing == []


# The prior art that the ticket names: the heading of its entry, and the primary source that the entry must link.
PRIOR_ART = {
    "superpowers": "https://github.com/obra/superpowers",
    "implement-spec": "https://github.com/mattpocock/skills",
    "Sandcastle": "https://github.com/mattpocock/sandcastle",
    "wshobson/agents": "https://github.com/wshobson/agents",
}


def entries(page: str) -> dict[str, str]:
    """The sections of the page, keyed by the text of their level-two heading."""
    parts = re.split(r"^## (.+)$", (EXPLANATION / page).read_text(), flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


def subentries(page: str, part: str) -> dict[str, str]:
    """The entries of one part of the page, keyed by the text of their level-three heading."""
    parts = re.split(r"^### (.+)$", entries(page)[part], flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


# The two parts of the prior art page, in order: the work that the kit is built on, then the tools beside it.
PARTS = ["Built on", "Alongside"]

# The two bodies of work that the kit is built on: the heading of the entry, and the primary source that it must link.
BUILT_ON = {
    "Bassim Eledath": "https://www.bassimeledath.com/blog/levels-of-agentic-engineering",
    "Matt Pocock": "https://github.com/mattpocock/skills",
}


@outside_the_package
def test_the_prior_art_page_has_a_built_on_part_before_an_alongside_part_and_no_other_part():
    assert list(entries("prior-art.md")) == PARTS


@outside_the_package
def test_each_built_on_entry_links_its_primary_source_and_gives_the_date_it_was_read():
    sections = subentries("prior-art.md", "Built on")
    assert sorted(next((name for name in BUILT_ON if name in heading), heading) for heading in sections) == sorted(BUILT_ON)
    problems = []
    for name, source in BUILT_ON.items():
        body = next(text for heading, text in sections.items() if name in heading)
        if not re.search(r"\]\(" + re.escape(source) + r"[/)#]", body):
            problems.append(f"{name}: no link to {source}")
        read = re.search(r"\bRead (\d{4}-\d{2}-\d{2})\b", body)
        if not read:
            problems.append(f"{name}: no read date")
        elif datetime.date.fromisoformat(read.group(1)) > datetime.date.today():
            problems.append(f"{name}: the read date {read.group(1)} is in the future")
    assert problems == []


# What each body of work gave the kit, as a pattern for the bullet of each gift. The ticket names them.
GIFTS = {
    "Bassim Eledath": [
        ("the map", r"^- the map\."),
        ("the order of the climb", r"^- the order of the climb\."),
        ("the codify loop", r"^- the codify loop\."),
        ("backpressure", r"^- backpressure\."),
    ],
    "Matt Pocock": [
        ("grilling settles the decisions", r"^- `grilling` settles the decisions"),
        ("wayfinder maps work bigger than one session", r"^- `wayfinder` maps work that is bigger than one session"),
        ("to-spec writes the spec", r"^- `to-spec` writes the spec"),
        ("to-tickets cuts vertical slices sized to one context window", r"^- `to-tickets` cuts .*context window"),
    ],
}


def plain(markdown: str) -> str:
    """The text of Markdown without link targets and without emphasis marks."""
    return re.sub(r"\*+", "", re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", markdown)).lower()


@outside_the_package
@pytest.mark.parametrize("name", list(GIFTS))
def test_each_built_on_entry_says_what_it_gave_the_kit(name):
    body = plain(next(text for heading, text in subentries("prior-art.md", "Built on").items() if name in heading))
    assert [gift for gift, pattern in GIFTS[name] if not re.search(pattern, body, flags=re.MULTILINE)] == []


@outside_the_package
def test_the_built_on_part_says_plainly_that_the_kit_takes_their_output_as_its_input():
    text = plain(entries("prior-art.md")["Built on"])
    assert "takes the output of these skills as its input" in text
    assert "would not exist in this form without them" in text


@outside_the_package
def test_each_prior_art_entry_links_its_primary_source_and_gives_the_date_it_was_read():
    sections = subentries("prior-art.md", "Alongside")
    problems = []
    for name, source in PRIOR_ART.items():
        body = next((text for heading, text in sections.items() if name in heading), None)
        if body is None:
            problems.append(f"{name}: no entry")
            continue
        if not re.search(r"\]\(" + re.escape(source) + r"[/)#]", body):
            problems.append(f"{name}: no link to {source}")
        read = re.search(r"\bRead (\d{4}-\d{2}-\d{2})\b", body)
        if not read:
            problems.append(f"{name}: no read date")
        elif datetime.date.fromisoformat(read.group(1)) > datetime.date.today():
            problems.append(f"{name}: the read date {read.group(1)} is in the future")
    assert problems == []


@outside_the_package
def test_the_prior_art_page_holds_one_entry_for_each_tool_of_the_ticket_and_no_other():
    named = [heading for heading in subentries("prior-art.md", "Alongside") if heading.strip() != "How to choose"]
    assert sorted(next(name for name in PRIOR_ART if name in heading) for heading in named) == sorted(PRIOR_ART)


@outside_the_package
def test_the_retitled_page_uses_the_glossary_term_and_its_title_is_the_title_of_its_navigation_entry():
    page = "why-each-specialist-has-its-own-verifier.md"
    title = (EXPLANATION / page).read_text().splitlines()[0].removeprefix("# ")
    config = tomllib.loads((ROOT / "zensical.toml").read_text())["project"]
    labels = [label for item in config["nav"] for value in item.values() if isinstance(value, list) for entry in value for label, target in entry.items() if target == f"explanation/{page}"]

    assert title == "Why each specialist has its own verifier"
    assert not re.search(r"\btwin\b", title, flags=re.IGNORECASE), "the glossary says: say verifier, not twin"
    assert labels == [title]


SKILL = ROOT / "skills" / "agent-definitions" / "SKILL.md"

# One phrase for each limit of the Limits section of the agent-definitions skill, in the order of the skill.
# The phrase is in the bullet of the skill and in the same item of the enforcement page.
LIMIT_PHRASES = [
    "composes agent files",
    "attached by path",
    "hook input",
    "not a sandbox",
    "without respect for quoting",
    "bash string operators",
    "command substitution",
    "destination-flag",
    "`..`",
    "does not examine it",
    "no `cp` rule",
    "`getonlycommands`",
    "no `nix develop` rule",
    "does not divide a compound command",
    "workspace trust",
    "`*git pu" "sh*`",
    "read order",
    "turn cap",
]


def limits_of_the_skill() -> list[str]:
    """The bullets of the Limits section of the skill, lower case."""
    section = SKILL.read_text().split("\n## Limits\n", 1)[1]
    return [bullet.lower() for bullet in re.findall(r"^- (.+(?:\n  .+)*)", section, flags=re.MULTILINE)]


@outside_the_package
def test_the_phrases_of_this_test_name_each_limit_of_the_skill_once():
    bullets = limits_of_the_skill()
    assert len(bullets) == len(LIMIT_PHRASES), "the skill has a limit that this test does not name, or the reverse"
    for number, phrase in enumerate(LIMIT_PHRASES):
        assert phrase in bullets[number], f"limit {number + 1} of the skill does not hold {phrase!r}"
        assert [b for b in bullets if phrase in b] == [bullets[number]], f"{phrase!r} names more than one limit"


@outside_the_package
def test_the_enforcement_page_lists_every_limit_of_the_skill_in_the_order_of_the_skill():
    section = entries("the-enforcement-model-and-its-limits.md")["The limits of the guards"]
    items = [item.lower() for item in re.findall(r"^\d+\. (.+(?:\n   .+)*)", section, flags=re.MULTILINE)]
    assert len(items) == len(LIMIT_PHRASES)
    assert [phrase for phrase, item in zip(LIMIT_PHRASES, items) if phrase not in item] == []


@outside_the_package
def test_the_enforcement_page_gives_the_pain_then_the_idea_then_the_mechanism_then_the_limits():
    # The pain and the idea: a harness guard is only as strong as its hook, so the first guard is git.
    # Then the mechanism: what the engine enforces. Then the limits: what it does not stop, and how I read them.
    assert list(entries("the-enforcement-model-and-its-limits.md")) == [
        "Why git, and not the harness",
        "What the engine enforces",
        "What the engine does not stop",
        "The limits of the guards",
        "How I read the limits",
    ]


@outside_the_package
def test_the_enforcement_page_links_the_limits_of_the_skill_for_the_full_text():
    text = (EXPLANATION / "the-enforcement-model-and-its-limits.md").read_text()
    assert "/blob/main/skills/agent-definitions/SKILL.md#limits)" in text


def adr_files() -> list:
    """Every ADR of the repository: a Markdown file in a directory named `adr`."""
    return sorted(path for path in ROOT.rglob("adr/*.md") if ".git" not in path.relative_to(ROOT).parts)


@outside_the_package
def test_the_adr_index_lists_every_adr_of_the_repository_with_its_title_and_no_other():
    adrs = adr_files()
    assert adrs, "the repository holds ADRs, so this test would check nothing"
    text = (EXPLANATION / "decision-records.md").read_text()
    # A page of the docs site links a file outside docs/ with a full URL that holds /blob/main/<path>.
    listed = {(ROOT / target.split("/blob/main/")[-1]).resolve(): label for label, target in re.findall(r"\[([^\]]+)\]\(([^)\s#]+/adr/[^)\s]+\.md)\)", text)}
    assert sorted(listed) == [path.resolve() for path in adrs]
    wrong_title = [
        path.name
        for path in adrs
        if listed[path.resolve()] != path.read_text().splitlines()[0].removeprefix("# ")
    ]
    assert wrong_title == []


@outside_the_package
def test_the_explanation_directory_holds_the_pages_of_the_tickets_and_no_other():
    assert sorted(path.name for path in EXPLANATION.glob("*.md")) == sorted(PAGES)
    assert len(PAGES) == 9


SHAPE = "why-the-code-has-this-shape.md"


@outside_the_package
def test_the_shape_page_has_one_mermaid_diagram_and_no_other_drawing():
    fences = re.findall(r"^`{3,}(\w*)", (EXPLANATION / SHAPE).read_text(), flags=re.MULTILINE)
    assert fences == ["mermaid", ""], "one opening fence of a Mermaid block, and its closing fence"


@outside_the_package
def test_the_shape_page_never_addresses_the_reader():
    assert re.findall(r"\byou(r|rs|rself)?\b", prose(SHAPE), flags=re.IGNORECASE) == []


@outside_the_package
def test_the_shape_page_is_in_the_explanation_section_of_the_navigation():
    nav = tomllib.loads((ROOT / "zensical.toml").read_text())["project"]["nav"]
    explanation = next(value for item in nav for label, value in item.items() if label == "Explanation")
    assert [label for entry in explanation for label, target in entry.items() if target == f"explanation/{SHAPE}"] == ["Why the code has this shape"]


@outside_the_package
def test_contributing_links_the_shape_page_in_its_code_map():
    text = (ROOT / "CONTRIBUTING.md").read_text()
    assert f"docs/explanation/{SHAPE}" in text


@outside_the_package
def test_the_glossary_holds_the_terms_of_the_shape_page():
    glossary = (ROOT / "docs" / "glossary.md").read_text()
    headings = re.findall(r"^## (.+)$", glossary, flags=re.MULTILINE)
    assert {"Port", "Context", "Git backend"} <= set(headings)
    assert "## Adapter" in glossary, "the glossary keeps adapter for the harness adapter that a user chooses"
