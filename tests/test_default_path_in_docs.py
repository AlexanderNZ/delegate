"""Each example of the docs shows the default path of the state root (#206).

#200 moved the default state root outside the git directory. The example of `delegate status` and its two figures
still showed the journal under `.git/delegate`, so a reader who followed the page looked in the wrong directory. A page
can still name `.git/delegate` as the place of a run that an older version started.
"""

from __future__ import annotations

import re

from delegate.docs import figures

from .pages import ROOT, outside_the_package

DEFAULT_ROOT = "~/.local/state/delegate/"
JOURNAL_UNDER_GIT = re.compile(r"\.git/delegate/runs/")


def status_frame() -> list[str]:
    (figure,) = [figure for figure in figures.FIGURES if figure.name == "status-table"]
    return figures.frame_lines(figure, colour=False)


def test_the_status_figure_shows_the_journal_under_the_default_state_root():
    (journal,) = [line for line in status_frame() if line.startswith("journal ")]

    assert journal.startswith(f"journal {DEFAULT_ROOT}")
    assert ".git" not in journal


@outside_the_package
def test_no_page_or_figure_under_docs_shows_a_journal_under_the_git_directory():
    shown = []
    for path in sorted((ROOT / "docs").rglob("*")):
        if path.suffix in (".md", ".svg"):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if JOURNAL_UNDER_GIT.search(line):
                    shown.append(f"{path.relative_to(ROOT)}:{number}")

    assert shown == []
