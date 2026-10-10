"""The docs theme: the files that zensical.toml names, and the man-page data of the reference pages.

design/README.md is the spec of the theme. A reference page carries `man`,
`man_name` and `synopsis` in its front matter, and the theme renders them as the
man header line and the NAME and SYNOPSIS block. The synopsis must not drift from
the parser of the command, so each option in it must be an option of the command.
These files are outside the package source, so a Nix build that copies only the
package skips these cases with a reason.
"""

import re
import tomllib

import pytest
import yaml

from delegate.cli import delegate

from .pages import ROOT, outside_the_package

CONFIG = ROOT / "zensical.toml"
DOCS = ROOT / "docs"
REFERENCE = DOCS / "reference"

# The reference pages of a command, and the subcommands that each synopsis covers.
COMMAND_PAGES: dict[str, list[str]] = {
    "run.md": ["run"],
    "status-and-watch.md": ["status", "watch"],
    "docs.md": ["docs"],
    "commands.md": ["render", "validate", "bootstrap", "brief"],
}


def front_matter(path) -> dict:
    text = path.read_text()
    if not text.startswith("---\n"):
        return {}
    return yaml.safe_load(text.split("---\n", 2)[1]) or {}


def synopsis_lines(meta: dict) -> list[str]:
    value = meta.get("synopsis", [])
    return [value] if isinstance(value, str) else list(value)


def help_text(subcommand: str, capsys) -> str:
    with pytest.raises(SystemExit):
        delegate.main([subcommand, "--help"])
    return capsys.readouterr().out


@outside_the_package
def test_each_stylesheet_and_script_that_the_config_names_exists_under_docs():
    project = tomllib.loads(CONFIG.read_text())["project"]
    named = project.get("extra_css", []) + project.get("extra_javascript", [])

    assert named, "the theme needs its stylesheet and script in the config"
    assert [path for path in named if not (DOCS / path).is_file()] == []


@outside_the_package
def test_the_override_directory_holds_the_page_template_and_the_partials_it_includes():
    theme = tomllib.loads(CONFIG.read_text())["project"]["theme"]
    overrides = ROOT / theme["custom_dir"]
    main = (overrides / "main.html").read_text()

    included = re.findall(r'include "partials/(dlg-[\w-]+\.html)"', main)
    assert included, "main.html includes the theme partials"
    assert [name for name in included if not (overrides / "partials" / name).is_file()] == []


@outside_the_package
def test_each_reference_page_has_a_man_header_and_a_name_line():
    for page in sorted(REFERENCE.glob("*.md")):
        meta = front_matter(page)
        assert re.fullmatch(r"[A-Z][A-Z-]*\(\d\)", str(meta.get("man", ""))), f"{page.name}: man must look like DELEGATE-RUN(1)"
        assert " — " in str(meta.get("man_name", "")), f"{page.name}: man_name is 'name — what it does'"


@outside_the_package
def test_only_reference_pages_carry_man_data():
    guide_pages = [path for path in DOCS.rglob("*.md") if REFERENCE not in path.parents and "agents" not in path.relative_to(DOCS).parts]

    assert [path.name for path in guide_pages if "man" in front_matter(path)] == []


@outside_the_package
def test_the_tutorial_turns_off_the_section_numbers_because_its_headings_are_numbered():
    assert front_matter(DOCS / "tutorial.md").get("heading_numbers") is False


@outside_the_package
@pytest.mark.parametrize("page", sorted(COMMAND_PAGES))
def test_each_synopsis_line_names_its_command_and_only_options_that_the_command_has(page, capsys):
    lines = synopsis_lines(front_matter(REFERENCE / page))
    subcommands = COMMAND_PAGES[page]

    assert lines, f"{page} needs a synopsis"
    for line in lines:
        subcommand = next((name for name in subcommands if line.startswith(f"delegate {name} ")), None)
        assert subcommand, f"{page}: '{line}' does not start with one of {subcommands}"
        options = set(re.findall(r"--[a-z][a-z-]*", help_text(subcommand, capsys)))
        assert set(re.findall(r"--[a-z][a-z-]*", line)) <= options, f"{page}: an option in '{line}' is not an option of delegate {subcommand}"


@outside_the_package
def test_the_design_folder_holds_the_spec_and_the_prototype():
    design = ROOT / "design"

    assert (design / "README.md").is_file()
    assert (design / "delegate-docs-theme-2c.dc.html").is_file()
