"""The helpers of the tests that read the commands of `CONTRIBUTING.md` (#354)."""

from __future__ import annotations

import re

from .pages import Block, ROOT, commands


CONTRIBUTING = ROOT / "CONTRIBUTING.md"


def section(heading: str) -> str:
    """The text of the `## heading` section of CONTRIBUTING, up to the next `## ` heading."""
    text = CONTRIBUTING.read_text()
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, flags=re.MULTILINE | re.DOTALL)
    assert match, f"CONTRIBUTING has no section {heading!r}"
    return match.group(1)


def bash_commands(heading: str) -> list[str]:
    """Every command of every bash block in the section."""
    body = section(heading)
    found = [Block(lang, path or None, text) for _fence, lang, path, text in re.findall(r'^(`{3,})(\w+)(?: title="([^"]+)")?\n(.*?)^\1$', body, flags=re.MULTILINE | re.DOTALL)]
    return [command for block in found if block.lang == "bash" for command in commands(block)]
