"""Helpers for the tests of the SVG figures in the docs.

A figure is two checked-in SVG files, one for the light scheme and one for the
dark scheme. The tests of each figure share three checks: the file is an SVG
image with a title and a description, and the two files differ only in their
colours. These helpers hold those checks once, so a page with a figure adds no
copy of them.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

SVG = "{http://www.w3.org/2000/svg}"


def parse(path: Path) -> ET.Element:
    """The root element of the SVG file."""
    return ET.parse(path).getroot()


def accessibility_problems(paths: Iterable[Path]) -> list[str]:
    """What stops a screen reader from announcing each file as an image: a root that is not an `svg`
    with `role="img"`, and a missing or empty `<title>` or `<desc>`. An empty list means no problem."""
    problems = []
    for path in paths:
        root = parse(path)
        if root.tag != f"{SVG}svg":
            problems.append(f"{path.name} is not an svg element")
        if root.get("role") != "img":
            problems.append(f'{path.name} has no role="img"')
        for part in ("title", "desc"):
            if not (root.findtext(f"{SVG}{part}") or "").strip():
                problems.append(f"{path.name} has no <{part}>")
    return problems


def without_colours(path: Path) -> str:
    """The text of the file without its `<style>` element, which holds all of its colours."""
    return re.sub(r"<style>.*?</style>", "", path.read_text(), flags=re.DOTALL)
