"""A reference page does not hold its own copy of the options of `delegate run`.

The generator writes those options into `docs/reference/run.md`. A second table
in another page drifts: the workflow page once lacked `--resume` and
`--break-lock`. The two pages below link the generated section instead.

The pages are outside the package source, as the README is, so a Nix build
that copies only the package skips these cases with a reason.
"""

import re

import pytest

from delegate.cli import run_command

from .pages import ROOT, outside_the_package

REFERENCE = ROOT / "docs" / "reference"
PAGES = ["workflow.md", "opencode-adapter.md"]


def outside_generated_sections(text: str) -> str:
    """The page without the text between generated-section markers."""
    return re.sub(r"<!-- generated:begin (\S+) -->.*?<!-- generated:end \1 -->", "", text, flags=re.DOTALL)


def flags_of_run() -> set[str]:
    """Every long option string of the parser of `delegate run`, as the parser lists it."""
    return {option for action in run_command.build_parser()._actions for option in action.option_strings if option.startswith("--")}  # noqa: SLF001


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_the_page_holds_no_table_row_for_an_option_of_delegate_run(page):
    flags = flags_of_run()
    rows = [line for line in outside_generated_sections((REFERENCE / page).read_text()).splitlines() if line.startswith("|")]

    copies = [row for row in rows if flags & set(re.findall(r"`(--[a-z-]+)", row.split("|")[1]))]

    assert copies == [], f"{page} holds its own copy of an option of `delegate run`; link run.md#options instead"


@outside_the_package
@pytest.mark.parametrize("page", PAGES)
def test_the_page_links_the_generated_options_section_of_the_run_page(page):
    run_page = (REFERENCE / "run.md").read_text()

    assert "(run.md#options)" in (REFERENCE / page).read_text()
    assert "## Options\n\n<!-- generated:begin run-options -->" in run_page
