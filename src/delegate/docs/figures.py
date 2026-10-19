"""The terminal figures of the docs: frames of `delegate watch` and `delegate status` as SVG files (#138).

`delegate docs` (in `delegate.docs.reference`) writes two files for each figure of `FIGURES` under `ASSETS`: one
for the light scheme and one for the dark scheme of the docs theme. The two files differ only in the colours of their
`<style>` element. `delegate docs --check` names each file that differs from the code.

A figure is the frame that the code renders from the journals in `SOURCE/<figure>/`, with a fixed clock, a fixed
width and a fixed height. The render copies the journals into the state directory of a temporary repository, and
replaces `$RUN` in them with the directory of the run. A journal names its event streams in `$RUN/reports/`: the render
copies them there from `SOURCE/streams/`. It writes each gate log that a journal names as an empty file, and gives each
file of a run the time of the newest event of its journal. It then calls the code of the command, with the SGR codes
on, a UTF-8 locale and the UTC time zone:

* `watch`: `delegate.run.watch.follow` with the live view of `delegate.run.live`, and the stream reader of the
  harness adapter of the run, as `delegate watch` on a terminal. The figure is the first frame that the view draws.
* `status`: the lines of `delegate status` (`delegate.run.status.render`), with the same reader.
* `picker`: the run picker of `delegate watch` with more than one active run (`delegate.run.status.picker_lines`),
  cut to the width, and the prompt.

The path of the temporary repository shows as `REPOSITORY` in a frame. The SVG writer uses the standard library only.
It reads the SGR codes of each line, and writes one `<text>` row for each line, with one `<tspan>` for each run of
one role. Each character is one monospace cell, and each `<tspan>` starts at the cell of its first character. Bold
is `font-weight` 600, and faint is the `dim` colour. The colours are the tokens of the docs theme
(`design/README.md`, Design tokens), written as hex values.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from ..cli.status import stream_reader_of
from ..cli.watch import PROMPT
from ..run import live, output, status, watch
from ..run.journal import ACTIVITY_FIELDS, read_new_events

# The journals and the event streams of the figures, in the package source, so a Nix build can read them.
SOURCE: Path = Path(__file__).parent / "figures"
SOURCE_IN_REPOSITORY: str = "src/delegate/docs/figures"

# The directory of the figure files, relative to the repository root.
ASSETS: str = "docs/assets/terminal"

# The text that a frame shows for the path of the temporary repository of the render.
REPOSITORY: str = "~/project"

# The environment of the terminal of a figure: a UTF-8 locale and the UTC time zone.
LOCALE: str = "C.UTF-8"
ZONE: str = "UTC"


@dataclass(frozen=True)
class Figure:
    """One figure: its name, the frame of the design that it shows, its title and its description, the command that
    renders it (`watch`, `status` or `picker`), the columns and the rows of the terminal, and the time of the clock."""

    name: str
    frame: str
    title: str
    description: str
    command: str
    columns: int
    rows: int
    now: str

    @property
    def journals(self) -> list[Path]:
        return sorted((SOURCE / self.name).glob("*.jsonl"))

    def path(self, scheme: str) -> str:
        return f"{ASSETS}/{self.name}-{scheme}.svg"


FIGURES: tuple[Figure, ...] = (
    Figure(
        "watch-live", "3a", "delegate watch: the live view of a run",
        "The run line, the ticket table and the feed of a run of 11 tickets at 120 columns. Tickets 101 and 102 are "
        "built. Ticket 103 is in its specialist, with its other phases on the rows under it. The rail joins a row to "
        "the row above it, and the after column names the blockers of 106 and 107. The feed shows the lines of 103.",
        "watch", 120, 24, "2026-03-02T09:24:30+00:00",
    ),
    Figure(
        "watch-picker", "2b", "delegate watch: the run picker",
        "Three runs are active, so delegate watch with no run id shows a table of the runs, newest first, and asks "
        "which run to follow. Two runs are running and one run is paused.",
        "picker", 120, 24, "2026-03-02T09:06:00+00:00",
    ),
    Figure(
        "status-table", "1i", "delegate status: the ticket table of a run",
        "The header lines of a run and its ticket table. Tickets 101, 102 and 105 are built, 103 failed with its "
        "reason, 104 was skipped because 103 failed, 106 is in its verifier, and 107 and 108 are pending. The table "
        "gives the verdict, the branch and the last event of each ticket.",
        "status", 120, 24, "2026-03-02T09:52:50+00:00",
    ),
    Figure(
        "watch-fixup", "1d", "delegate watch: a REJECT and a fix-up round",
        "The verifier of ticket 103 gave REJECT with 2 findings, and the fix-up round 1 of 103 runs. The phase "
        "column shows the fix-up round, and the feed shows the verdict and the start of the fix-up.",
        "watch", 120, 24, "2026-03-02T09:31:45+00:00",
    ),
    Figure(
        "watch-paused", "1f", "delegate watch: a paused run",
        "A pause request stopped the run before the step of ticket 104. Tickets 101 to 103 are built. The row of 104 "
        "says that the run paused before this step, and the feed gives the command that goes on with the run.",
        "watch", 120, 24, "2026-03-02T09:31:00+00:00",
    ),
    Figure(
        "watch-fit", "3b", "delegate watch: a plan of 40 tickets fitted to 24 rows",
        "A plan of 40 tickets with two branches on a terminal of 120 columns and 24 rows. Ticket 124 runs its second "
        "gate of 3. The built rows in the middle fold into one row with their totals, the pending rows at the end fold "
        "into one row, and the running ticket and the pending rows nearest to it stay.",
        "watch", 120, 24, "2026-03-02T13:06:46+00:00",
    ),
)


# The colours of the docs theme for each scheme, as oklch (lightness, chroma, hue). `bg` is `code-bg`, `fg` is `ink`,
# `dim` is `muted` and `plan` is `link`. `ok`, `now`, `warn` and `bad` have the lightness and the chroma of `link`,
# with the hues of green, cyan, yellow and red. A colour outside the sRGB gamut keeps its lightness and hue, and gets
# the largest chroma in the gamut.
HUES: dict[str, float] = {"ok": 145, "now": 210, "warn": 85, "bad": 25}
THEME: dict[str, dict[str, tuple[float, float, float]]] = {
    "light": {"bg": (0.965, 0.004, 80), "fg": (0.27, 0.01, 60), "dim": (0.52, 0.01, 60), "plan": (0.48, 0.13, 255)},
    "dark": {"bg": (0.25, 0.006, 60), "fg": (0.9, 0.006, 80), "dim": (0.66, 0.01, 70), "plan": (0.76, 0.1, 255)},
}
SCHEMES: tuple[str, ...] = tuple(THEME)

# The font of the code of the docs theme, the size of a cell, and the space around the frame, in pixels.
FONT: str = "'Noto Sans Mono', ui-monospace, monospace"
FONT_SIZE: int = 13
CELL: float = 7.8  # the advance of a monospace character: 0.6 em
LINE: int = 18
PAD: int = 16

# The attribute of a run in the `bold` role.
BOLD: str = ' font-weight="600"'

# The SGR code of the colour of each role of `output.ROLES`, by the code.
COLOUR_OF_CODE: dict[str, str] = {code: role for role, code in output.ROLES.items() if code and role not in ("dim", "bold")}


class _Drawn(Exception):
    """The live view drew a frame: the render takes it, and stops the watch."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__("frame drawn")
        self.lines = lines


class _Terminal(io.StringIO):
    """The standard output of the render: a terminal, so the code writes the SGR codes when `NO_COLOR` is not set."""

    def isatty(self) -> bool:
        return True


@contextlib.contextmanager
def _terminal(colour: bool) -> Iterator[None]:
    """Give the code a terminal with a UTF-8 locale and the UTC time zone, and `NO_COLOR` when `colour` is false. Set the
    environment and the standard output back after."""
    names = ("LC_ALL", "NO_COLOR", "TZ")
    before = {name: os.environ.get(name) for name in names}
    stdout = sys.stdout
    os.environ.update({"LC_ALL": LOCALE, "TZ": ZONE})
    if colour:
        os.environ.pop("NO_COLOR", None)
    else:
        os.environ["NO_COLOR"] = "1"
    _tzset()
    sys.stdout = _Terminal()
    try:
        yield
    finally:
        sys.stdout = stdout
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        _tzset()


def _tzset() -> None:
    if hasattr(time, "tzset"):  # POSIX only; on another system the local zone of the process stays
        time.tzset()


def _stage(figure: Figure, repo: Path) -> list[Path]:
    """Copy the journals of a figure into the state directory of `repo`, with the files that they name. Return the
    journals, in the order of their names."""
    journals = []
    for source in figure.journals:
        run = repo / ".git" / "delegate" / "runs" / source.stem
        run.mkdir(parents=True)
        journal = run / "journal.jsonl"
        journal.write_text(source.read_text().replace("$RUN", json.dumps(str(run))[1:-1]))
        events = [json.loads(line) for line in journal.read_text().splitlines()]
        files = {Path(str(event[name])) for event in events for name in ACTIVITY_FIELDS if name in event}
        for path in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = SOURCE / "streams" / path.name
            if path.parent.name == "reports" and stream.is_file():
                shutil.copyfile(stream, path)
            else:
                path.write_text("")  # a gate log: the code reads only its time
        newest = datetime.fromisoformat(str(events[-1]["time"])).timestamp()
        for path in [journal, *files]:
            os.utime(path, (newest, newest))
        journals.append(journal)
    return journals


def _watch(figure: Figure, journal: Path, now: datetime) -> list[str]:
    def draw(lines: list[str]) -> None:
        raise _Drawn(lines)

    view = live.LiveView(journal, stream_reader_of, lambda: now, lambda: (figure.columns, figure.rows), draw)
    try:
        watch.follow(journal, watch.WatchOptions(), view)
    except _Drawn as drawn:
        return drawn.lines
    raise RuntimeError(f"the live view drew no frame of the figure {figure.name}")


def _status(journal: Path, now: datetime) -> list[str]:
    events, _ = read_new_events(journal, 0, 1)
    return status.render(events, journal, status.snapshot_of(events, journal, stream_reader_of, now))


def _picker(figure: Figure, journals: list[Path], now: datetime) -> list[str]:
    lines = status.picker_lines(status.active_runs(journals, now), now)
    return [*(output.cut(line, figure.columns) for line in lines), PROMPT]


def frame_lines(figure: Figure, colour: bool = True) -> list[str]:
    """The lines of the frame of a figure as the command prints them: with the SGR codes of the roles, or with
    `NO_COLOR` set when `colour` is false. The path of the temporary repository shows as `REPOSITORY`."""
    now = datetime.fromisoformat(figure.now)
    with tempfile.TemporaryDirectory() as temporary, _terminal(colour):
        repo = Path(temporary).resolve() / "project"
        journals = _stage(figure, repo)
        if figure.command == "watch":
            lines = _watch(figure, journals[0], now)
        elif figure.command == "status":
            lines = _status(journals[0], now)
        else:
            lines = _picker(figure, journals, now)
        return [line.replace(str(repo), REPOSITORY) for line in lines]


# The SVG writer.


def _runs(line: str) -> list[tuple[int, str, bool, str]]:
    """The runs of one role of a painted line: the cell where each starts, its colour role, whether it is bold, and its
    text. The SGR state follows the terminal: code 0 turns each attribute off."""
    runs: list[tuple[int, str, bool, str]] = []
    colour, bold, faint = "fg", False, False
    cell, position = 0, 0
    for match in [*output.SGR.finditer(line), None]:
        text = line[position:match.start() if match else len(line)]
        if text:
            role = "dim" if faint else colour
            if runs and runs[-1][1:3] == (role, bold):
                start, _, _, before = runs[-1]
                runs[-1] = (start, role, bold, before + text)
            else:
                runs.append((cell, role, bold, text))
            cell += len(text)
        if match is None:
            break
        position = match.end()
        for code in (match.group()[2:-1] or "0").split(";"):
            if code in ("", "0"):
                colour, bold, faint = "fg", False, False
            elif code == "1":
                bold = True
            elif code == "2":
                faint = True
            elif code in COLOUR_OF_CODE:
                colour = COLOUR_OF_CODE[code]
    return runs


def _hex(lightness: float, chroma: float, hue: float) -> str:
    """The sRGB hex value of an oklch colour. Outside the gamut, the chroma goes down until the colour is in it."""

    def channels(c: float) -> list[float]:
        a, b = c * math.cos(math.radians(hue)), c * math.sin(math.radians(hue))
        l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
        m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
        s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
        return [
            4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
            -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
            -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
        ]

    low, high = 0.0, chroma
    if any(not -1e-9 <= value <= 1 + 1e-9 for value in channels(high)):
        for _ in range(40):
            middle = (low + high) / 2
            low, high = (middle, high) if all(0 <= value <= 1 for value in channels(middle)) else (low, middle)
        high = low
    encoded = []
    for value in channels(high):
        value = min(1.0, max(0.0, value))
        value = 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
        encoded.append(round(value * 255))
    return "#" + "".join(f"{value:02x}" for value in encoded)


def palette(scheme: str) -> dict[str, str]:
    """The hex value of each colour of a scheme: `bg`, `fg`, `dim`, `plan`, `ok`, `now`, `warn` and `bad`."""
    tokens = dict(THEME[scheme])
    lightness, chroma, _ = tokens["plan"]
    tokens.update({role: (lightness, chroma, hue) for role, hue in HUES.items()})
    return {role: _hex(*value) for role, value in tokens.items()}


def svg(figure: Figure, scheme: str, lines: list[str]) -> str:
    """The SVG file of a figure in a scheme, from the painted lines of its frame."""
    colours = palette(scheme)
    # A frame of `watch` is as wide as the terminal. The table of `status` is not cut, so its widest line can be wider.
    width = 2 * PAD + max([figure.columns, *(output.width(line) for line in lines)]) * CELL
    height = 2 * PAD + len(lines) * LINE
    style = "\n".join([f"    .bg {{ fill: {colours['bg']}; }}", *(f"    .{role} {{ fill: {value}; }}" for role, value in colours.items() if role != "bg")])
    rows = []
    for index, line in enumerate(lines):
        spans = "".join(
            f'<tspan x="{_number(PAD + cell * CELL)}" class="{role}"{BOLD if bold else ""}>{escape(text)}</tspan>'
            for cell, role, bold, text in _runs(line)
        )
        rows.append(f'  <text y="{PAD + index * LINE + 13}">{spans}</text>')
    title, description = f"{figure.name}-title", f"{figure.name}-desc"
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_number(width)} {height}" width="{_number(width)}" height="{height}" '
        f'role="img" aria-labelledby="{title} {description}" font-family="{FONT}" font-size="{FONT_SIZE}" xml:space="preserve">',
        f'  <title id="{title}">{escape(figure.title)} (frame {figure.frame})</title>',
        f'  <desc id="{description}">{escape(figure.description)}</desc>',
        "  <style>",
        style,
        "  </style>",
        f'  <rect class="bg" x="0" y="0" width="{_number(width)}" height="{height}" rx="4"/>',
        *rows,
        "</svg>",
        "",
    ])


def _number(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def files() -> dict[str, str]:
    """The text of each figure file, by its path relative to the repository root."""
    found = {}
    for figure in FIGURES:
        lines = frame_lines(figure)
        for scheme in SCHEMES:
            found[figure.path(scheme)] = svg(figure, scheme, lines)
    return found


def stale(root: Path) -> list[str]:
    """The figure files under `root` that are missing or differ from the code."""
    return [path for path, text in files().items() if not (root / path).is_file() or (root / path).read_text() != text]


def write(root: Path) -> list[str]:
    """Write each figure file under `root` that is missing or differs from the code. Return the files that changed."""
    changed = []
    for path, text in files().items():
        target = root / path
        if target.is_file() and target.read_text() == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        changed.append(path)
    return changed
