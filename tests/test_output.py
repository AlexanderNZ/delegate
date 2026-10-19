"""The terminal output parts of #115: the paint roles, the glyph set with its ASCII fallback, and the column measurer.

The spec is `design/design_handoff_delegate_terminal_output/docs/tokens.md` and `docs/accessibility.md`. Colour is an
ANSI role, never a background and never an RGB value. With `NO_COLOR` set, or with a standard output that is not a
terminal, the text has no SGR code. With a locale that is not UTF-8, the glyphs are the ASCII set of tokens.md, with
the same width in cells, so the columns stay in the same positions.

The system boundaries are faked: the terminal (a standard output whose `isatty` is true) and the environment.
"""

from __future__ import annotations

import io
import re
import sys
from dataclasses import astuple

import pytest

from delegate.run.output import columns, glyphs, paint

from .support import Terminal

# Each role of tokens.md that has an SGR code, with its code. `fg` is the default foreground: it has no code.
CODES = {"dim": "2", "bold": "1", "ok": "32", "now": "36", "warn": "33", "bad": "31", "plan": "34"}

SGR = re.compile(r"\x1b\[[0-9;]*m")

UNICODE = ("✓", "✗", "▶", "·", "●", "○", "–", "█", "░", "│", "─", "⚠")
ASCII = ("+", "x", ">", ".", "*", "o", "-", "#", ".", "|", "-", "!")


def on_a_terminal(monkeypatch) -> None:
    """Make standard output a terminal, with no `NO_COLOR` in the environment.

    A case calls it in its body: pytest sets its own standard output at the start of each phase of a test.
    """
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys, "stdout", Terminal())


@pytest.mark.parametrize("role", sorted(CODES))
def test_on_a_terminal_each_role_wraps_the_text_in_its_sgr_code_and_a_reset(monkeypatch, role):
    on_a_terminal(monkeypatch)
    assert paint(role, "gates green") == f"\x1b[{CODES[role]}mgates green\x1b[0m"


def test_dim_is_faint_and_not_bright_black(monkeypatch):
    on_a_terminal(monkeypatch)
    painted = paint("dim", "12m38s")

    assert painted.startswith("\x1b[2m")
    assert "90" not in SGR.findall(painted)[0]


def test_fg_is_the_default_foreground_so_it_writes_no_code(monkeypatch):
    on_a_terminal(monkeypatch)
    assert paint("fg", "run-branch") == "run-branch"


@pytest.mark.parametrize("role", sorted(CODES) + ["fg"])
def test_no_role_sets_a_background_colour(monkeypatch, role):
    on_a_terminal(monkeypatch)
    params = [p for code in SGR.findall(paint(role, "x")) for p in code[2:-1].split(";") if p]

    assert not [p for p in params if 40 <= int(p) <= 49 or 100 <= int(p) <= 107]


@pytest.mark.parametrize("role", sorted(CODES) + ["fg"])
def test_with_no_color_set_each_role_gives_the_plain_text_on_a_terminal(monkeypatch, role):
    on_a_terminal(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")

    assert paint(role, "gates red") == "gates red"


@pytest.mark.parametrize("role", sorted(CODES) + ["fg"])
def test_when_standard_output_is_not_a_terminal_each_role_gives_the_plain_text(monkeypatch, role):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert paint(role, "run built") == "run built"


def test_an_empty_no_color_does_not_turn_the_colour_off(monkeypatch):
    on_a_terminal(monkeypatch)
    # no-color.org: NO_COLOR turns colour off when it is present and not an empty string.
    monkeypatch.setenv("NO_COLOR", "")

    assert paint("ok", "built") == "\x1b[32mbuilt\x1b[0m"


def test_an_unknown_role_is_an_error_that_names_the_roles(monkeypatch):
    on_a_terminal(monkeypatch)
    with pytest.raises(ValueError, match="green.*fg, dim, bold, ok, now, warn, bad, plan"):
        paint("green", "x")


def locale(monkeypatch, **values: str) -> None:
    """Set the locale variables of the environment. A variable that is not given is not set."""
    for name in ("LC_ALL", "LC_CTYPE", "LANG"):
        if name in values:
            monkeypatch.setenv(name, values[name])
        else:
            monkeypatch.delenv(name, raising=False)


def test_a_utf8_locale_gives_the_unicode_glyphs(monkeypatch):
    locale(monkeypatch, LANG="en_NZ.UTF-8")

    assert astuple(glyphs()) == UNICODE


def test_lang_c_gives_the_ascii_set_of_tokens_md(monkeypatch):
    locale(monkeypatch, LANG="C")

    assert astuple(glyphs()) == ASCII


def test_no_locale_variable_is_the_c_locale_so_the_glyphs_are_ascii(monkeypatch):
    locale(monkeypatch)

    assert astuple(glyphs()) == ASCII


def test_lc_all_wins_over_lang(monkeypatch):
    locale(monkeypatch, LC_ALL="C", LANG="en_NZ.UTF-8")
    assert astuple(glyphs()) == ASCII

    locale(monkeypatch, LC_ALL="en_NZ.utf8", LANG="C")
    assert astuple(glyphs()) == UNICODE


def test_each_ascii_glyph_has_the_width_of_its_unicode_glyph_so_the_columns_keep_their_positions(monkeypatch):
    locale(monkeypatch, LANG="C")
    ascii_set = astuple(glyphs())
    locale(monkeypatch, LANG="en_NZ.UTF-8")
    unicode_set = astuple(glyphs())

    assert [len(g) for g in ascii_set] == [len(g) for g in unicode_set]
    assert glyphs().rail == "│"


def test_columns_pad_each_value_to_the_widest_value_of_its_column_with_two_cells_between_columns():
    rows = [["#", "plan", "state"], ["1", "a", "built"], ["2", "long-id", "running"]]

    assert columns(rows, right={0}) == [
        "#  plan     state",
        "1  a        built",
        "2  long-id  running",
    ]


def test_numbers_align_right_and_so_does_the_header_of_their_column():
    rows = [["#", "plan", "used", "new"], ["1", "a", "12m38s", "149k"], ["10", "b", "5s", "10.7M"]]

    assert columns(rows, right={0, 2, 3}) == [
        " #  plan    used    new",
        " 1  a     12m38s   149k",
        "10  b         5s  10.7M",
    ]


def test_a_painted_value_is_measured_by_the_cells_that_it_shows(monkeypatch):
    on_a_terminal(monkeypatch)
    plain = [["#", "state", "used"], ["1", "running", "3m"], ["2", "pending", "12m38s"]]
    painted = [["#", "state", "used"], ["1", paint("now", "running"), paint("dim", "3m")], ["2", "pending", "12m38s"]]

    lines = columns(painted, right={0, 2})

    assert [SGR.sub("", line) for line in lines] == columns(plain, right={0, 2})
    assert "\x1b[36mrunning\x1b[0m" in lines[1]
