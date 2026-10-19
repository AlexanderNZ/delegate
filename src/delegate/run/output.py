"""The parts of the terminal output: the paint roles, the glyph set, and the column measurer (#115).

The spec is `design/design_handoff_delegate_terminal_output/docs/tokens.md` and `docs/accessibility.md`.

* `paint(role, text)` gives the text in the ANSI code of a role. Colour is semantic, so it follows the theme of the
  terminal: only the 16 ANSI colours, bold and faint, and never a background colour. `dim` is faint (SGR 2), not
  bright black, because bright black can be the colour of the background in some themes. When `NO_COLOR` is set
  and not empty (no-color.org), or when standard output is not a terminal, the text has no SGR code.
* `glyphs()` gives the one glyph set. When the locale is not UTF-8, it gives the ASCII set of tokens.md. Each ASCII
  glyph has the width of its Unicode glyph, so the columns stay in the same positions.
* `columns(rows, right)` pads each value to the widest value of its column, with 2 cells between columns. The
  columns of numbers align right. A painted value is measured by the cells that it shows.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Collection, Sequence
from dataclasses import dataclass

# The SGR code of each role of tokens.md. `fg` is the default foreground, so it has no code.
ROLES: dict[str, str | None] = {
    "fg": None, "dim": "2", "bold": "1", "ok": "32", "now": "36", "warn": "33", "bad": "31", "plan": "34",
}

# The SGR code that turns each attribute off.
RESET: str = "\x1b[0m"

# An SGR sequence. It takes no cell on the terminal.
SGR = re.compile(r"\x1b\[[0-9;]*m")

# The cells between two columns.
GAP: str = "  "

# The locale variables, in the order that decides the character set: the first one that is set and not empty wins.
LOCALE_VARIABLES: tuple[str, ...] = ("LC_ALL", "LC_CTYPE", "LANG")


def colour_on() -> bool:
    """True when the output can have SGR codes: `NO_COLOR` is not set or is empty, and standard output is a terminal."""
    return not os.environ.get("NO_COLOR") and sys.stdout.isatty()


def paint(role: str, text: str) -> str:
    """The text in the ANSI code of `role`, one of `fg dim bold ok now warn bad plan`, or the plain text.

    The text is plain when `colour_on()` is false, and for `fg`. Raise ValueError for a role that tokens.md does not have.
    """
    if role not in ROLES:
        raise ValueError(f"no paint role {role!r}; the roles are {', '.join(ROLES)}")
    code = ROLES[role]
    if code is None or not colour_on():
        return text
    return f"\x1b[{code}m{text}{RESET}"


@dataclass(frozen=True)
class Glyphs:
    """The glyph set of tokens.md. The tree glyph takes two cells for each depth."""

    built: str      # built, ACCEPT
    failed: str     # failed, a problem
    running: str
    pending: str    # pending, and the separator
    node: str       # a built node, the active phase
    open: str       # a pending phase
    skipped: str
    fill: str       # the filled part of the bar
    empty: str      # the rest of the bar
    tree: str
    rule: str       # the divider
    idle: str       # idle past the threshold


UNICODE = Glyphs("✓", "✗", "▶", "·", "●", "○", "–", "█", "░", "└─", "─", "⚠")
ASCII = Glyphs("+", "x", ">", ".", "*", "o", "-", "#", ".", "\\-", "-", "!")


def utf8_locale() -> bool:
    """True when the locale of the environment is UTF-8. With no locale variable, the locale is C, which is not UTF-8."""
    value = next((os.environ[name] for name in LOCALE_VARIABLES if os.environ.get(name)), "")
    return "utf8" in value.lower().replace("-", "")  # `en_NZ.UTF-8`, `C.utf8`, and `UTF-8` alone as macOS sets LC_CTYPE


def glyphs() -> Glyphs:
    """The glyph set of the locale: the Unicode set, or the ASCII set when the locale is not UTF-8."""
    return UNICODE if utf8_locale() else ASCII


def width(text: str) -> int:
    """The cells that a value takes on the terminal: its characters, with no SGR sequence."""
    return len(SGR.sub("", text))


def columns(rows: Sequence[Sequence[str]], right: Collection[int] = ()) -> list[str]:
    """The lines of a table: each value padded to the widest value of its column, with 2 cells between columns.

    `right` holds the indexes of the columns of numbers: their values, and their header, align right. The last column
    gets no padding when it aligns left, so a line has no space at its end.
    """
    if not rows:
        return []
    widths = [max(width(row[index]) for row in rows) for index in range(len(rows[0]))]
    last = len(widths) - 1
    lines = []
    for row in rows:
        cells = []
        for index, value in enumerate(row):
            pad = " " * (widths[index] - width(value))
            cells.append(pad + value if index in right else value if index == last else value + pad)
        lines.append(GAP.join(cells))
    return lines
