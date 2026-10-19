"""`delegate docs` writes the terminal frames of `watch` and `status` as SVG figures, and checks that they are current (#138).

A figure is a frame that the code renders from a journal in the package source, with a fixed clock, a fixed width and
a fixed height, on the path of `delegate watch` and `delegate status` with the SGR codes on. The command writes two
files for each figure under `docs/assets/terminal/`, one for the light scheme and one for the dark scheme of the docs
theme, as the other figures of the docs (`tests/svg.py`). `delegate docs --check` names each figure file that differs
from the code.

The cases call `delegate.main(["docs", ...])` on a copy of the docs, or on the repository for the drift test. The case
of the text compares each figure with the frame that the code renders (`delegate.docs.figures.frame_lines`), so a figure
holds no cell that the code does not print. The frame with `NO_COLOR` set is the text under the figure on a page. It has
the same cells, except the liveness of an agent (#147): with colour, the live view shows a frame of the spinner or
`quiet <time>`, and with no colour the text of a snapshot, `active <time> ago`.
"""

from __future__ import annotations

import os
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from delegate.cli import delegate
from delegate.docs import figures

from .pages import repository_root
from .svg import SVG, accessibility_problems, parse, without_colours

ROOT = repository_root(Path(__file__))
ASSETS = Path("docs") / "assets" / "terminal"

# The figures of the ticket and of its plan change: frames 3a, 2b, 1i, 1d, the paused frame, and 3b.
NAMES = ["watch-live", "watch-picker", "status-table", "watch-fixup", "watch-paused", "watch-fit"]
FILES = [f"{name}-{scheme}.svg" for name in NAMES for scheme in ("light", "dark")]

outside_the_package = pytest.mark.skipif(
    not (ROOT / "README.md").is_file(), reason="the docs are outside the package source, as in a Nix build"
)


@pytest.fixture
def pages(tmp_path):
    """A copy of the docs and of the skill page, with no figure file: the files that `delegate docs` reads and writes."""
    shutil.copytree(ROOT / "docs", tmp_path / "docs")
    shutil.rmtree(tmp_path / ASSETS, ignore_errors=True)
    skill = tmp_path / "skills" / "agent-definitions" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    shutil.copy(ROOT / "skills" / "agent-definitions" / "SKILL.md", skill)
    return tmp_path


def docs(capsys, *argv: str) -> tuple[int, str, str]:
    code = delegate.main(["docs", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def contents(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


# The liveness cell at the end of a line of a frame (#147): with colour, a frame of the spinner or `quiet <time>`, and
# with no colour, `active <time> ago`.
LIVE = re.compile(r"(?:[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]|quiet (?P<time>\S+))$")
SNAPSHOT = re.compile(r"active (?P<time>\S+) ago$")


def same_story(painted: list[str], plain: list[str]) -> None:
    """Assert that the lines of a frame with colour, with no SGR code, are the lines with no colour, except that a
    liveness cell of the live view is `active <time> ago` with no colour. A `quiet <time>` keeps its time."""
    shown = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in painted]
    assert len(shown) == len(plain)
    for live, text in zip(shown, plain):
        if live == text:
            continue
        cell, snapshot = LIVE.search(live), SNAPSHOT.search(text)
        assert cell is not None and snapshot is not None, (live, text)
        assert live[:cell.start()] == text[:snapshot.start()], (live, text)
        assert cell["time"] in (None, snapshot["time"]), (live, text)


def rows_of(path: Path) -> list[str]:
    """The text of each `<text>` row of a figure, with the text of its `<tspan>` runs in order."""
    return ["".join(row.itertext()) for row in parse(path).iter(f"{SVG}text")]


def test_the_figures_of_the_ticket_are_the_figures_of_the_code():
    assert [figure.name for figure in figures.FIGURES] == NAMES


@outside_the_package
def test_the_command_writes_a_light_and_a_dark_file_for_each_figure_and_a_second_run_changes_no_byte(pages, capsys):
    code, out, err = docs(capsys, "--root", str(pages))

    assert (code, err) == (0, "")
    assert sorted(path.name for path in (pages / ASSETS).iterdir()) == sorted(FILES)
    assert all(f"updated {ASSETS / name}" in out.splitlines() for name in FILES), out
    first = contents(pages / ASSETS)

    code, out, err = docs(capsys, "--root", str(pages))

    assert (code, out, err) == (0, "", "")
    assert contents(pages / ASSETS) == first


@outside_the_package
def test_the_check_names_each_missing_figure_file_and_tells_the_reader_to_run_the_command(pages, capsys):
    code, out, err = docs(capsys, "--root", str(pages), "--check")

    assert code == 1
    for name in FILES:
        assert f"{ASSETS / name}: " in err and "run `delegate docs`" in err, err
    assert not (pages / ASSETS).exists()  # the check writes nothing


@outside_the_package
def test_the_check_names_a_figure_file_with_a_changed_glyph_and_only_that_file(pages, capsys):
    assert docs(capsys, "--root", str(pages))[0] == 0
    changed = pages / ASSETS / "watch-live-dark.svg"
    text = changed.read_text()
    assert "✓" in text
    changed.write_text(text.replace("✓", "+", 1))

    code, out, err = docs(capsys, "--root", str(pages), "--check")

    assert code == 1
    assert err == f"delegate docs: {ASSETS / 'watch-live-dark.svg'}: the figure is not current with the code; run `delegate docs` and commit the result\n"


@outside_the_package
@pytest.mark.parametrize("name", FILES)
def test_each_committed_figure_file_matches_the_code(name, capsys):
    """The drift test of the figures. A change of a frame (a glyph, a column, a word) with no `delegate docs` fails it."""
    code, out, err = docs(capsys, "--root", str(ROOT), "--check")

    assert str(ASSETS / name) not in err, "the figure differs from the code: run `delegate docs` and commit the result"


@outside_the_package
@pytest.mark.parametrize("name", NAMES)
def test_each_pair_of_figure_files_is_accessible_and_differs_only_in_its_colours(name):
    light, dark = ROOT / ASSETS / f"{name}-light.svg", ROOT / ASSETS / f"{name}-dark.svg"

    assert accessibility_problems([light, dark]) == []
    assert without_colours(light) == without_colours(dark)
    assert light.read_text() != dark.read_text()


@outside_the_package
@pytest.mark.parametrize("name", NAMES)
def test_the_rows_of_each_figure_are_the_lines_of_the_frame_that_the_code_prints(name):
    figure = next(f for f in figures.FIGURES if f.name == name)

    shown = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in figures.frame_lines(figure, colour=True)]

    assert not any("\x1b" in line for line in shown)
    assert len(shown) > 3
    for scheme in ("light", "dark"):
        assert rows_of(ROOT / ASSETS / f"{name}-{scheme}.svg") == shown


def test_the_frame_with_colour_is_the_frame_with_no_colour_and_the_sgr_codes_of_the_roles():
    for figure in figures.FIGURES:
        painted = figures.frame_lines(figure, colour=True)

        assert any("\x1b[" in line for line in painted), figure.name
        same_story(painted, figures.frame_lines(figure, colour=False))


def test_the_live_view_figures_show_the_spinner_and_quiet_and_their_text_reads_active_ago():
    # #147: frame 3a has an agent quiet for 38s, and frame 1d an agent active 25s ago.
    by_name = {figure.name: figure for figure in figures.FIGURES}
    for name, live, text in (("watch-live", "  quiet 38s", "  active 38s ago"), ("watch-fixup", "  ⠋", "  active 25s ago")):
        painted = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in figures.frame_lines(by_name[name], colour=True)]
        plain = figures.frame_lines(by_name[name], colour=False)
        assert sum(line.endswith(live) for line in painted) == 1, painted
        assert sum(line.endswith(text) for line in plain) == 1, plain


def test_a_frame_does_not_depend_on_the_locale_the_time_zone_or_the_colour_settings_of_the_process(monkeypatch):
    before = {figure.name: figures.frame_lines(figure, colour=True) for figure in figures.FIGURES}
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TZ", "NZT-13")

    after = {figure.name: figures.frame_lines(figure, colour=True) for figure in figures.FIGURES}

    assert after == before
    assert os.environ["TZ"] == "NZT-13" and os.environ["LC_ALL"] == "C"  # the render sets the environment back


@outside_the_package
def test_a_figure_has_a_title_that_names_the_frame_a_monospace_font_and_no_script_or_external_reference():
    for name in FILES:
        text = (ROOT / ASSETS / name).read_text()
        root = ET.fromstring(text)
        assert root.findtext(f"{SVG}title").startswith(("delegate watch", "delegate status")), name
        assert "Noto Sans Mono" in text and "ui-monospace, monospace" in text, name
        assert "<script" not in text and "href" not in text and "url(" not in text, name


@outside_the_package
def test_the_colours_of_a_figure_are_the_docs_theme_written_as_hex_values():
    light, dark = (ROOT / ASSETS / f"watch-live-{scheme}.svg" for scheme in ("light", "dark"))

    for path in (light, dark):
        style = re.search(r"<style>(.*?)</style>", path.read_text(), flags=re.DOTALL).group(1)
        assert "oklch" not in style
        assert all(re.fullmatch(r"#[0-9a-f]{6}", colour) for colour in re.findall(r"(?:fill|stroke):\s*([^;}\s]+)", style)), style
    # The background is the `code-bg` of each scheme: oklch(0.965 0.004 80) and oklch(0.25 0.006 60).
    assert ".bg { fill: #f5f3f0; }" in light.read_text()
    assert ".bg { fill: #24211f; }" in dark.read_text()


def denylist_terms() -> list[str]:
    path = os.environ.get("AGENT_DEFINITIONS_DENYLIST")
    if not path:
        pytest.skip("AGENT_DEFINITIONS_DENYLIST is not set, so the figures cannot be checked for private terms")
    lines = Path(path).read_text().splitlines()
    return [line.strip().lower() for line in lines if line.strip() and not line.strip().startswith("#")]


def test_no_term_of_the_private_denylist_is_in_a_journal_of_a_figure_or_in_a_figure():
    terms = denylist_terms()
    files = [path for path in figures.SOURCE.rglob("*") if path.is_file()]
    files += sorted((ROOT / ASSETS).glob("*.svg")) if (ROOT / ASSETS).is_dir() else []
    assert files

    found = [f"{path.name}: line {number}" for path in files for number, line in enumerate(path.read_text().splitlines(), 1)
             if any(term in line.lower() for term in terms)]

    assert found == []  # the message names the file and the line, not the term
