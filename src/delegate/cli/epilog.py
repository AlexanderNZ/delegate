"""The end of the help of each command: the address of the docs site, and the reference page of the command (#248).

A coordinator that reads no page reads the help and the dry run. So the help of each command that a parser of
`delegate.cli` owns ends with the address of the docs site and the title and the address of its reference page.

The address is the `site_url` of `zensical.toml`. It is a constant here, and the command does not read that file,
because an installed package has no `zensical.toml`. A test compares the help with the file.
"""

from __future__ import annotations

import argparse
import textwrap

# The `site_url` of zensical.toml.
SITE_URL: str = "https://alexandernz.github.io/delegate/"


class HelpFormatter(argparse.HelpFormatter):
    """The help formatter of argparse, with two changes to the description and the epilog.

    Each line of the text is a paragraph of its own, so the epilog can put an address on its own line. And the
    formatter breaks no word, so an address stays whole on a narrow terminal.
    """

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        return "\n".join(
            textwrap.fill(
                " ".join(line.split()), width, initial_indent=indent, subsequent_indent=indent,
                break_long_words=False, break_on_hyphens=False,
            )
            for line in text.splitlines()
        )


def epilog(*pages: tuple[str, str], before: str = "") -> str:
    """The epilog of a command: the text `before`, then the docs site, then one line for each (page, title) of `pages`.

    A page is the path of a page under `docs/`, for example `reference/run.md`.
    """
    lines = [before, ""] if before else []
    lines.append(f"docs: {SITE_URL}")
    lines += [f'reference "{title}": {SITE_URL}{page.removesuffix(".md")}/' for page, title in pages]
    return "\n".join(lines)


# The reference pages that the epilogs name, as (the path under docs/, the title in the navigation of the site).
COMMANDS_PAGE: tuple[str, str] = ("reference/commands.md", "The commands")
RUN_PAGE: tuple[str, str] = ("reference/run.md", "delegate run")
WORKFLOW_PAGE: tuple[str, str] = ("reference/workflow.md", "The workflow file")
STATUS_PAGE: tuple[str, str] = ("reference/status-and-watch.md", "delegate status and delegate watch")
DOCS_PAGE: tuple[str, str] = ("reference/docs.md", "delegate docs")
