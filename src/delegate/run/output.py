"""The parts of the terminal output: the paint roles, the glyph set, and the column measurer (#115).

The spec is `design/design_handoff_delegate_terminal_output/docs/tokens.md` and `docs/accessibility.md`.

* `paint(role, text)` gives the text in the ANSI code of a role. Colour is semantic, so it follows the theme of the
  terminal: only the 16 ANSI colours, bold and faint, and never a background colour. `dim` is faint (SGR 2), not
  bright black, because bright black can be the colour of the background in some themes. When `NO_COLOR` is set
  and not empty (no-color.org), or when standard output is not a terminal, the text has no SGR code. A line for
  standard error, as an error line, names that stream: it has no SGR code when standard error is not a terminal.
* `glyphs()` gives the one glyph set. When the locale is not UTF-8, it gives the ASCII set of tokens.md. Each ASCII
  glyph has the width of its Unicode glyph, so the columns stay in the same positions.
* `columns(rows, right, tracks)` pads each value to the widest value of its column, or to the track of its column
  when the track is wider, with 2 cells between columns. The columns of numbers align right. A painted value is
  measured by the cells that it shows.
* `styled(roles, text)` paints a text in more than one role, as `now bold`, and `cut(text, cells)` cuts a painted
  line to the width of the terminal.
* The short forms of the words of a frame: `short_run_id`, `gate_name`, and `without_paths`.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

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

# An absolute path in a line of a frame. A frame shows only its last part, the name of the file.
ABSOLUTE_PATH = re.compile(r"(?<![\w.~-])/(?:[^\s/'\"]+/)*([^\s/'\"]*)")

# The words at the start of a gate command that only start the tool that does the work. A frame names the gate by the
# first word after them that is not an option and not a variable, so `nix develop -c python3 -m pytest -q` is `pytest`.
LAUNCHERS: frozenset[str] = frozenset({"nix", "develop", "uvx", "uv", "run", "npx", "env", "exec", "python", "python3"})

# A word that sets a variable of the environment of a command, as `NAME=value`. A version pin, as `tool==1.0`, is not one.
VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=(?!=)")


def colour_on(stream: TextIO | None = None) -> bool:
    """True when the output can have SGR codes: `NO_COLOR` is not set or is empty, and the stream is a terminal.

    The stream is standard output when `stream` is None.
    """
    return not os.environ.get("NO_COLOR") and (sys.stdout if stream is None else stream).isatty()


def paint(role: str, text: str, stream: TextIO | None = None) -> str:
    """The text in the ANSI code of `role`, one of `fg dim bold ok now warn bad plan`, or the plain text.

    The text is plain when `colour_on(stream)` is false, and for `fg`. `stream` is the stream that gets the text, and
    standard output when it is None. Raise ValueError for a role that tokens.md does not have.
    """
    if role not in ROLES:
        raise ValueError(f"no paint role {role!r}; the roles are {', '.join(ROLES)}")
    code = ROLES[role]
    if code is None or not colour_on(stream):
        return text
    return f"\x1b[{code}m{text}{RESET}"


def styled(roles: str, text: str, stream: TextIO | None = None) -> str:
    """The text in each role of `roles`, a list of roles with a space between them, as `now bold`. `paint` gives the codes.

    An empty text and an empty list of roles give the text with no code. `stream` is as for `paint`.
    """
    if not text:
        return text
    for role in reversed(roles.split()):
        text = paint(role, text, stream)
    return text


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


def columns(rows: Sequence[Sequence[str]], right: Collection[int] = (), tracks: Sequence[int] = ()) -> list[str]:
    """The lines of a table: each value padded to the widest value of its column, with 2 cells between columns.

    `right` holds the indexes of the columns of numbers: their values, and their header, align right. `tracks` gives
    the design width of each column, in order: a column is as wide as its track when the track is wider than its widest
    value. A column with no track, past the end of `tracks`, is as wide as its widest value. The last column gets no
    padding when it aligns left, so a line has no space at its end.
    """
    if not rows:
        return []
    widths = [
        max(max(width(row[index]) for row in rows), tracks[index] if index < len(tracks) else 0) for index in range(len(rows[0]))
    ]
    last = len(widths) - 1
    lines = []
    for row in rows:
        cells = []
        for index, value in enumerate(row):
            pad = " " * (widths[index] - width(value))
            cells.append(pad + value if index in right else value if index == last else value + pad)
        lines.append(GAP.join(cells))
    return lines


def cut(text: str, cells: int) -> str:
    """The first `cells` cells of a painted text. A cut text ends with the code that turns each attribute off."""
    if width(text) <= cells:
        return text
    kept, shown, painted = [], 0, False
    position = 0
    for match in SGR.finditer(text):
        plain = text[position:match.start()]
        take = plain[: max(0, cells - shown)]
        kept.append(take)
        shown += len(take)
        if shown >= cells:
            break
        kept.append(match.group())
        painted = True
        position = match.end()
    else:
        kept.append(text[position:][: max(0, cells - shown)])
    return "".join(kept) + (RESET if painted else "")


def short_run_id(run_id: str) -> str:
    """The short form of a run id: the random part after the last `-`, as `ab12` of `20261016T030000Z-ab12`.

    A run id with no `-` is its own short form.
    """
    return run_id.rsplit("-", 1)[-1] or run_id


def gate_name(command: str) -> str:
    """The short name of a gate command: the name of the first word that is not a launcher, with no version.

    For example `nix develop -c python3 -m pytest -q` is `pytest`, and `uvx "zensical==0.0.2" build` is `zensical`.
    """
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    for word in words:
        if word in LAUNCHERS or word.startswith("-") or VARIABLE.match(word):
            continue
        return re.split(r"[=<>@]", Path(word).name)[0] or word
    return command


def without_paths(text: str) -> str:
    """The text with each absolute path cut to its last part."""
    return ABSOLUTE_PATH.sub(lambda match: match.group(1), text)
