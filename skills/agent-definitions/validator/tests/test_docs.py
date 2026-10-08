"""`delegate docs` writes the generated sections of the reference pages, and checks that the committed pages are current.

The pages are outside the package source, as the README is, so a Nix build
that copies only the package skips the cases that read them, with a reason.
"""

import shutil
from pathlib import Path

import pytest

from agent_definitions import delegate

ROOT = Path(__file__).resolve().parents[4]

outside_the_package = pytest.mark.skipif(
    not (ROOT / "README.md").is_file(), reason="the docs are outside the package source, as in a Nix build"
)


@pytest.fixture
def pages(tmp_path):
    """A copy of the reference pages and of the skill, so a test can change a page and the repository stays as it is."""
    shutil.copytree(ROOT / "docs", tmp_path / "docs")
    skill = tmp_path / "skills" / "agent-definitions" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    shutil.copy(ROOT / "skills" / "agent-definitions" / "SKILL.md", skill)
    return tmp_path


def docs(capsys, *argv):
    code = delegate.main(["docs", *argv])
    out, err = capsys.readouterr()
    return code, out, err


@outside_the_package
def test_the_command_writes_the_options_of_run_into_the_run_page_and_keeps_the_prose(pages, capsys):
    page = pages / "docs" / "reference" / "run.md"
    page.write_text(page.read_text().replace("| `--break-lock` |", "| `--old-flag` |"))

    code, out, err = docs(capsys, "--root", str(pages))

    text = page.read_text()
    assert (code, err) == (0, "")
    assert "| `--break-lock` | Remove the lock of the run branch when its process no longer exists; a lock whose process is alive stays. |" in text
    assert "--old-flag" not in text
    assert "## What a run does" in text  # the prose around the section stays
    assert "docs/reference/run.md" in out


@outside_the_package
def test_the_check_fails_and_names_the_section_when_a_page_does_not_match_the_parser(pages, capsys):
    page = pages / "docs" / "reference" / "run.md"
    before = page.read_text()
    stale = before.replace("| `--break-lock` |", "| `--old-flag` |")
    assert stale != before
    page.write_text(stale)

    code, out, err = docs(capsys, "--root", str(pages), "--check")

    assert code == 1
    assert "docs/reference/run.md" in err and "run-options" in err
    assert page.read_text() == stale  # the check writes nothing


@outside_the_package
def test_the_check_passes_after_the_command_has_written_the_pages(pages, capsys):
    assert docs(capsys, "--root", str(pages))[0] == 0

    code, out, err = docs(capsys, "--root", str(pages), "--check")

    assert (code, err) == (0, "")


@outside_the_package
def test_a_page_with_no_marker_for_its_section_is_an_error_that_names_the_page_and_the_section(pages, capsys):
    page = pages / "docs" / "reference" / "run.md"
    page.write_text("# Reference: `delegate run`\n\nNo marker here.\n")

    code, out, err = docs(capsys, "--root", str(pages))

    assert code == 2
    assert "docs/reference/run.md" in err and "run-options" in err


def test_a_root_with_no_page_is_an_error_that_names_the_page(tmp_path, capsys):
    code, out, err = docs(capsys, "--root", str(tmp_path))

    assert code == 2
    assert "docs/reference/run.md" in err
