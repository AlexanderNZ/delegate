"""The docs site: the Zensical configuration, the one pinned version, and the commands that build and serve it.

The site is built from `docs/` by `uvx zensical==<pin>`. The pin is in one file,
`.zensical-version`. CONTRIBUTING and the CI workflow read that file, so no
second copy of the version can drift. These files are outside the package
source, so a Nix build that copies only the package skips these cases with a
reason. The build itself (`build --strict`) is a gate of the ticket, not a unit
test: it needs the network.
"""

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from .pages import ROOT, outside_the_package
from .test_contributing import bash_commands

CONFIG = ROOT / "zensical.toml"
PIN = ROOT / ".zensical-version"
DOCS = ROOT / "docs"
WORKFLOW = ROOT / ".github" / "workflows" / "docs.yml"

# The order of the sections in the navigation. The overview is first, and the glossary is last.
SECTIONS: list[str] = ["Start here", "Tutorial", "How-to guides", "Explanation", "Reference", "Glossary"]


def config() -> dict:
    return tomllib.loads(CONFIG.read_text())["project"]


def nav_targets(items: list) -> list[str]:
    """Every page that the navigation names, in order, at any depth."""
    found: list[str] = []
    for item in items:
        for value in item.values():
            found += nav_targets(value) if isinstance(value, list) else [value]
    return found


def nav_titles(items: list) -> list[str]:
    return [title for item in items for title in item]


def pages() -> set[str]:
    """Every page under docs/ that is user documentation: all of them except docs/agents/."""
    return {str(path.relative_to(DOCS)) for path in DOCS.rglob("*.md") if path.relative_to(DOCS).parts[0] != "agents"}


@outside_the_package
def test_the_pin_is_one_exact_version_and_nothing_else():
    # A range or a tool name in the file would make `zensical==$(cat ...)` build a different command.
    assert re.fullmatch(r"\d+\.\d+\.\d+\n?", PIN.read_text()), "the file must hold one exact version such as 1.2.3"


@outside_the_package
def test_the_build_command_and_the_serve_command_of_contributing_run_the_pinned_version_through_uvx():
    commands = bash_commands("Build and preview the docs")
    pin = PIN.read_text().strip()

    build, serve = [command.replace('$(cat .zensical-version)', pin) for command in commands]

    assert len(commands) == 2
    assert build == f'uvx "zensical=={pin}" build --strict'
    assert serve == f'uvx "zensical=={pin}" serve'
    assert all("$(cat .zensical-version)" in command for command in commands)


@outside_the_package
def test_no_file_but_the_pin_file_holds_the_version_of_zensical():
    # One place for the pin: a literal `zensical==1.2.3` in the page or in the workflow would be a second copy.
    texts = [(ROOT / "CONTRIBUTING.md").read_text(), CONFIG.read_text()]
    if WORKFLOW.is_file():
        texts.append(WORKFLOW.read_text())
    assert [text for text in texts if re.search(r"zensical==\d", text)] == []


@outside_the_package
def test_the_ci_workflow_for_the_docs_reads_the_pin_file_when_it_exists():
    if not WORKFLOW.is_file():
        pytest.skip("the docs workflow is written by the coordinator, after this change")
    text = WORKFLOW.read_text()
    assert ".zensical-version" in text
    assert "--strict" in text


@outside_the_package
def test_the_navigation_lists_the_sections_in_the_order_of_the_ticket():
    nav = config()["nav"]

    assert nav_titles(nav) == SECTIONS
    assert nav_targets(nav[:1]) == ["overview.md"]
    assert nav_targets(nav[-1:]) == ["glossary.md"]


@outside_the_package
def test_the_site_root_opens_the_overview():
    assert config()["plugins"]["redirects"]["redirect_maps"] == {"index.md": "overview.md"}


@outside_the_package
def test_no_relative_link_of_a_page_leaves_docs():
    # The strict build cannot follow a path out of docs/. CONTRIBUTING asks for a full URL.
    link = re.compile(r"\]\(([^)\s#]+)")
    leaving = []
    for page in sorted(DOCS.rglob("*.md")):
        if page.relative_to(DOCS).parts[0] == "agents":
            continue
        for target in link.findall(page.read_text()):
            if not target.startswith(("http://", "https://", "mailto:")) and not (page.parent / target).resolve().is_relative_to(DOCS):
                leaving.append(f"{page.relative_to(ROOT)}: {target}")
    assert leaving == []


@outside_the_package
def test_every_page_of_the_navigation_exists_and_every_user_page_is_in_the_navigation():
    targets = nav_targets(config()["nav"])

    assert [target for target in targets if not (DOCS / target).is_file()] == []
    assert sorted(pages() - set(targets)) == [], "a page of docs/ is missing from the navigation of zensical.toml"
    assert len(targets) == len(set(targets)), "a page is in the navigation twice"


@outside_the_package
def test_docs_agents_is_excluded_from_the_site_and_the_site_directory_is_the_one_that_gitignore_names():
    project = config()

    assert project["docs_dir"] == "docs"
    assert project["site_dir"] == "site"
    assert "agents/**" in project["plugins"]["exclude"]["glob"]
    assert "site/" in (ROOT / ".gitignore").read_text().splitlines()


@outside_the_package
def test_a_dead_link_and_a_dead_anchor_fail_the_build():
    assert config()["validation"] == {"invalid_links": True, "invalid_link_anchors": True}


@outside_the_package
def test_the_site_has_search_a_colour_toggle_code_copy_buttons_and_tooltips():
    theme = config()["theme"]

    assert {"content.code.copy", "content.tooltips", "search.highlight"} <= set(theme["features"])
    assert {palette["scheme"] for palette in theme["palette"] if "scheme" in palette} == {"default", "slate"}
    assert all("toggle" in palette for palette in theme["palette"])


@outside_the_package
def test_the_abbreviations_file_is_appended_to_every_page_from_a_directory_outside_docs():
    extensions = config()["markdown_extensions"]
    appended = extensions["pymdownx"]["snippets"]["auto_append"]

    assert {"abbr", "attr_list"} <= set(extensions)
    assert appended == ["includes/abbreviations.md"]
    assert (ROOT / appended[0]).is_file(), "the generated file must be committed"


OPENING_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
ATTRIBUTE = r'(?:\{[^}]*\}|[\w.-]+="[^"]*"|[\w.-]+=\S+)'
ATTRIBUTES = re.compile(rf"{ATTRIBUTE}(?:\s+{ATTRIBUTE})*")


def bare_words_after_the_language(page: Path) -> list[str]:
    """The opening fences of a page that put a bare word after the language, as `file:line: fence`.

    Zensical (pymdownx.superfences) renders a fence such as ```toml title="workflow.toml".
    A fence such as ```toml workflow.toml is not a code block in the built HTML: it becomes a paragraph.
    After the language, only attributes (`name="value"`) and a brace group are valid.
    """
    found: list[str] = []
    closing = ""
    for number, line in enumerate(page.read_text().splitlines(), start=1):
        if closing:
            text = line.strip()
            if text and set(text) == {closing[0]} and len(text) >= len(closing):
                closing = ""
            continue
        opened = OPENING_FENCE.match(line)
        if opened is None:
            continue
        closing = opened.group(1)
        words = opened.group(2).strip().split(None, 1)
        if len(words) > 1 and not ATTRIBUTES.fullmatch(words[1]):
            found.append(f"{page}:{number}: {line.strip()}")
    return found


def test_the_fence_check_flags_a_bare_word_and_accepts_the_title_form(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(
        '```toml workflow.toml\na = 1\n```\n\n'
        '```toml title="workflow.toml"\na = 1\n```\n\n'
        '````markdown docs/agents/delegation.md\n```toml x\n```\n````\n\n'
        '````markdown title="docs/agents/delegation.md"\n```bash\nls\n```\n````\n\n'
        '```bash\nls\n```\n'
    )

    flagged = bare_words_after_the_language(page)

    assert [line.split(": ", 1)[1] for line in flagged] == ["```toml workflow.toml", "````markdown docs/agents/delegation.md"]


@outside_the_package
def test_no_fence_in_docs_puts_a_bare_word_after_the_language():
    # A fence such as ```toml workflow.toml does not render on the site; the title form does.
    found = [problem for page in sorted(DOCS.rglob("*.md")) for problem in bare_words_after_the_language(page)]

    assert found == []


@outside_the_package
def test_the_site_directory_is_ignored_by_git():
    ignored = subprocess.run(["git", "check-ignore", "-q", "site/index.html"], cwd=ROOT)
    assert ignored.returncode == 0
