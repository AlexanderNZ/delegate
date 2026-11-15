"""`delegate docs` writes the terminal frames of `watch`, `status` and `runs` as SVG figures, and checks that they are current (#138, #372).

A figure is a frame that the code renders from a journal in the package source, with a fixed clock, a fixed width and
a fixed height, on the path of `delegate watch`, `delegate status` and `delegate runs` with the SGR codes on. The command writes two
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

# The figures of the ticket and of its plan change: frames 3a, 2b, 1i, 1d, the paused frame, and 3b, then the frames W8, W9 and E15 of
# `delegate runs` (#372).
NAMES = ["watch-live", "watch-picker", "status-table", "watch-fixup", "watch-paused", "watch-fit", "runs-table", "runs-timeline", "runs-fit"]
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

    assert (ROOT / ASSETS / name).is_file()
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


def frame_of(name: str) -> list[str]:
    """The plain lines of the figure `name`, as the code prints them with `NO_COLOR` set."""
    return figures.frame_lines(next(f for f in figures.FIGURES if f.name == name), colour=False)


def test_the_table_figure_of_runs_shows_the_fleet_lines_the_hosts_the_ended_group_and_the_legend():
    # W8: `delegate runs` at 120 columns, with the clock at 14:20.
    frame = frame_of("runs-table")
    host = next(i for i, line in enumerate(frame) if line.startswith("ci-runner"))
    ended = frame.index("ended")

    assert frame[1].startswith("runs  3 of 4 reachable  ") and "11/23 ended" in frame[1]
    assert frame[2].startswith("09:20–14:20  ⚠ 1 in trouble  trend: tickets ended an hour, last 6h · all")
    assert any(line.startswith("host") and line.split()[-2:] == ["alert", "do"] for line in frame)
    assert any(line.startswith("laptop ") and "11/23" in line for line in frame)  # the totals row of this machine
    assert any("⚠ idle 6m40s" in line and line.endswith("watch") for line in frame)  # a run in trouble, with its alert and its do word
    assert any("fix-up 1/2" in line and line.endswith("watch") for line in frame)
    assert "unreachable" in frame[host] and "heard 47m ago" in frame[host] and frame[host].endswith("check host")
    assert re.fullmatch(r"│ +ssh cannot reach ci-runner: .+", frame[host + 1])  # the reason, under the row of the host
    assert [line.split()[0] for line in frame[ended + 1:ended + 3]] == ["laptop", "laptop"]  # the host is in the column host
    assert [line.split()[-1] for line in frame[ended + 1:ended + 3]] == ["merge", "retry"]
    assert frame[-5:] == [
        "watch       delegate watch <run>",
        "resume      delegate run --resume <run>",
        "retry       delegate run --resume <run> --retry <ticket>",
        "merge       git merge --ff-only <run branch>",
        "check host  ssh <host>; on another host, put ssh <host> in front of each command",
    ]


def test_the_timeline_figure_of_runs_shows_the_lane_all_a_lane_for_each_run_the_column_of_now_and_the_footer():
    # W9: `delegate runs --timeline` at 120 columns.
    frame = frame_of("runs-timeline")
    first = next(i for i, line in enumerate(frame) if line.startswith("all "))
    lanes = [line for line in frame[first + 1:-2] if line.strip()]

    assert frame[first - 2].startswith("last 6h, to the latest ETA") and frame[first - 2].split()[-4:] == ["ended", "ETA", "state", "alert"]
    assert sum(line.startswith("all ") for line in frame) == 1
    assert len(lanes) == 6  # the five runs of this machine, and the last-known run of the other host
    assert [state in line.split() for state, line in zip(["paused", "running", "running", "built", "failed", "unreachable"], lanes)] == [True] * 6
    assert all("┊" in line or "▶" in line for line in frame[first:first + 6])  # the column of now in the lane all and in each lane of this machine
    assert "one cell is 10m" in frame[2]
    assert frame[-1] == "detail  delegate watch <run>; on another host, ssh <host> delegate watch <run>"


def test_the_figure_of_runs_at_80_columns_has_dropped_the_bar_the_legend_and_the_columns_new_cache_trend_and_slip():
    # E15: `delegate runs` at 80 columns.
    frame = frame_of("runs-fit")
    header = next(line for line in frame if line.startswith("host"))

    assert max(len(line) for line in frame) <= 80
    assert header.split() == ["host", "repo", "run", "state", "ended", "ETA", "alert", "do"]
    assert not any("█" in line or "░" in line for line in frame)
    assert not any(word in header.split() for word in ("slip", "new", "cache", "trend"))
    assert not any("delegate watch <run>" in line for line in frame)  # no legend under 100 columns


def test_the_figures_of_runs_use_neutral_names_and_start_no_process(monkeypatch):
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: pytest.fail("a figure of runs started a process"))

    frames = {name: "\n".join(frame_of(name)) for name in ("runs-table", "runs-timeline", "runs-fit")}

    assert {path.name for path in (figures.SOURCE / "runs").iterdir()} == {"api", "web", "docs", "last-answers"}
    assert all("ci-runner" in frame for frame in frames.values())  # the host that ssh cannot reach, read from the store of last answers
    assert "unreachable" in frames["runs-table"] and "unreachable" in frames["runs-timeline"]


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
        assert root.findtext(f"{SVG}title").startswith(("delegate watch", "delegate status", "delegate runs")), name
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
