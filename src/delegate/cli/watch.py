"""The `delegate watch` command: parse the arguments, build the git backend, follow the journal through the run context and show each event.

The use case lives in `delegate.run.watch`, which never imports an adapter. This module is the composition point
of the command: it makes the git backend of the version-control port, finds the journal, and hands the use case a sink.
When standard output is a terminal, the sink is the live view of `delegate.run.live`, with the stream reader of the
adapter of the run, as for `delegate status`, and this module draws its frames in place. Otherwise, and with `--raw`,
the sink prints the raw line of each event.

When standard output is a terminal, this module also sets the title of the terminal (`delegate.cli.title`): the short
run id, the percentage and the local time of the ETA while the watch goes on, and the result when it ends.

With no run id, the active runs decide (`delegate.run.runs.runs_of`), also with `--from` (#137). The one active run is
followed, also when a newer run ended. With more than one, standard input and standard output on a terminal get the run
picker of frame 2b (#118): the table of the active runs, newest first (`delegate.run.status.picker_lines`), and the prompt
`watch which? [1]`. The reader types the number of a run, and Enter takes 1. Then this module follows that run. When
standard input or standard output is not a terminal, nobody can answer the prompt: this module prints one line on stderr
that names each active run (`delegate.run.status.choices_line`), follows none, and exits with `watch.EXIT_ERROR`. Ctrl-C
at the prompt ends the command as before it finds a run: `delegate: interrupted` and 130. With no active run and with
`--from`, this module goes on in the journal of the newest run from the position. With no active run and no `--from`,
the newest run gets one line and no replay of its journal:
a run that ended (`delegate.run.watch.EndedRun`) exits with the code of its result, and a run that stopped
(`delegate.run.watch.StoppedRun`) with the code of a pause, or the code of an error on stderr. A newest run that failed
at start (`delegate.run.runs.FailedStart`) has no journal, also with `--from`: its line gives the last line of its log,
and the exit code is `watch.EXIT_FAILED`.

Ctrl-C ends the watch with `watch.EXIT_INTERRUPTED` (130), and the run goes on. The live view draws its last frame, and
this module shows the cursor and sets the title to the reason `interrupted`. It then prints one line on stderr with the
position to go on from, and no line `position <seq>`.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import TextIO

from ..adapters.git import GitVersionControl
from ..run import live, status, watch
from ..run.journal import JournalError
from ..run.output import cut, short_run_id, styled
from ..run.runs import FailedStart, RunsError, journal_of, runs_of
from .status import stream_reader_of, utc_now
from .title import SEPARATOR, Title

# The terminal control sequences of the live view: clear the screen, move the cursor to the top left, clear to the end
# of the line, and clear the screen below the cursor.
CLEAR_SCREEN: str = "\x1b[2J"
HOME: str = "\x1b[H"
CLEAR_LINE_END: str = "\x1b[K"
CLEAR_DOWN: str = "\x1b[J"

# The prompt of the run picker. The default answer, 1, is the newest active run.
PROMPT: str = "watch which? [1] "

# The terminal control sequence that shows the cursor. Ctrl-C can come while a frame is written, so the command writes
# it after the last frame.
SHOW_CURSOR: str = "\x1b[?25h"


def _positive(text: str) -> float:
    """An option value that is a number greater than 0."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0, not {text}")
    return value


def _position(text: str) -> int:
    """An option value that is a journal position: a whole number, 0 or more."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, not {text}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delegate watch", description="Follow the journal of a run, print each new event, and exit with the run's result.")
    parser.add_argument(
        "run_id", nargs="?",
        help="the run to follow; default is the one active run, or the newest run of the repository when no run is active",
    )
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository that holds the run; default is the current directory")
    parser.add_argument(
        "--until", choices=[watch.UNTIL_VERDICT],
        help="also exit when a verifier verdict is written to the journal; default is to follow the run to its end",
    )
    parser.add_argument(
        "--stall-minutes", type=_positive, metavar="N",
        help="exit when the journal, the captured event streams and the gate logs have not changed for N minutes; N is a number greater than 0",
    )
    parser.add_argument(
        "--max-minutes", type=_positive, metavar="N",
        help="exit after N minutes, before a harness time limit, and print the position to go on from; N is a number greater than 0",
    )
    parser.add_argument(
        "--from", type=_position, default=None, metavar="POSITION", dest="from_position",
        help="go on after the event with this position, which an earlier watch printed; no event is reported twice",
    )
    parser.add_argument("--poll-seconds", type=_positive, default=1.0, help="how often to read the journal; default 1")
    parser.add_argument(
        "--raw", action="store_true",
        help="print the raw line of each event, as to a standard output that is not a terminal; default on a terminal is the live view",
    )
    return parser


def _emit(line: str) -> None:
    print(line, flush=True)


class _Screen:
    """The terminal that the live view draws on. The first frame clears the screen; each frame starts at the top left."""

    def __init__(self, out: TextIO) -> None:
        self._out = out
        self._drawn = False

    def size(self) -> tuple[int, int]:
        size = shutil.get_terminal_size()
        return size.columns, size.lines

    def draw(self, lines: list[str]) -> None:
        start = HOME if self._drawn else CLEAR_SCREEN + HOME
        self._drawn = True
        # The screen below the frame is cleared before the last newline, so a line printed after the frame starts clean.
        self._out.write(start + "\n".join(f"{line}{CLEAR_LINE_END}" for line in lines) + CLEAR_DOWN + "\n")
        self._out.flush()


class _Titled:
    """A sink that sets the title of the terminal, then tells each call to the sink that it wraps.

    At each poll the title gives the percentage and the local time of the ETA of the snapshot of the events so far. The
    title is set before the sink draws, so a frame of the live view holds no title. At the end the title gives the
    result of the run, or the reason that the watch ended before the run: `problem`, `verdict`, `stall` or `time limit`.
    """

    def __init__(self, sink: watch.Sink, journal: Path, title: Title) -> None:
        self._sink, self._journal, self._title = sink, journal, title
        self._name = f"delegate watch{SEPARATOR}{short_run_id(journal.parent.name)}"
        self._events: list[dict[str, object]] = []
        self._result: str | None = None

    def event(self, event: dict[str, object], reported: bool, problem: str | None) -> None:
        self._events.append(event)
        if event["event"] == "run-end":
            self._result = str(event["result"])
        elif event["event"] == "paused":
            self._result = "paused"
        elif problem is not None:
            self._result = "problem"
        self._sink.event(event, reported, problem)

    def verdict(self, event: dict[str, object]) -> None:
        self._result = "verdict"
        self._sink.verdict(event)

    def stall(self, minutes: float, last_event: dict[str, object] | None) -> None:
        self._result = "stall"
        self._sink.stall(minutes, last_event)

    def time_limit(self, minutes: float) -> None:
        self._result = "time limit"
        self._sink.time_limit(minutes)

    def interrupted(self) -> None:
        self._result = "interrupted"
        self._sink.interrupted()

    def poll(self) -> None:
        self._title.set(self._progress())
        self._sink.poll()

    def end(self) -> None:
        self._title.set(self._progress() if self._result is None else f"{self._name}{SEPARATOR}{self._result}")
        self._sink.end()

    def _progress(self) -> str:
        """The title of a watch that goes on: the percentage and the local ETA, or only the run when the journal has no `run-start`."""
        try:
            # The title needs no tokens, so the snapshot reads no event stream.
            snap = status.snapshot_of(self._events, self._journal, lambda adapter: None, utc_now())
        except JournalError:
            return self._name
        eta = live.local_time(snap.eta) if snap.eta else "-"
        return f"{self._name}{SEPARATOR}{snap.percent}%{SEPARATOR}ETA {eta}"


def _error(text: str, raw: bool) -> None:
    """Print an error line of `watch` on stderr, in `bad` when stderr is a terminal (tokens.md).

    With `--raw` (`raw`), the line is plain also on a terminal, as before #114.
    """
    line = f"delegate watch: {text}"
    print(line if raw else styled("bad", line, sys.stderr), file=sys.stderr)


def _newest_run(newest: watch.EndedRun | watch.StoppedRun, raw: bool) -> int:
    """Print the one line of the newest run when no run is active, and return its exit code.

    The line of a run that stopped with no pause is an error, so it goes to stderr, and the title is not set. With
    `--raw` (`raw`), the line is plain also on a terminal, as before #114.
    """
    if isinstance(newest, watch.StoppedRun) and not newest.paused:
        _error(newest.line(), raw)
        return newest.code
    result = newest.result if isinstance(newest, watch.EndedRun) else "paused"
    Title(sys.stdout).set(f"delegate watch{SEPARATOR}{short_run_id(newest.run_id)}{SEPARATOR}{result}")
    print(newest.line() if raw else newest.painted())
    return newest.code


def _pick(runs: list[status.ActiveRun], raw: bool) -> status.ActiveRun | None:
    """Show the run picker on the terminal, read the answer, and return the run that the reader chose.

    Enter takes 1. An answer that is not the number of a row gets one line on stderr, and the prompt again. Return None
    at the end of the input, with one line on stderr. Ctrl-C ends the line of the prompt and goes to the caller.
    """
    width = shutil.get_terminal_size().columns
    print("\n".join(cut(line, width) for line in status.picker_lines(runs, utc_now())), flush=True)
    while True:
        sys.stdout.write(PROMPT)
        sys.stdout.flush()
        try:
            answer = sys.stdin.readline()
        except KeyboardInterrupt:
            print(flush=True)  # the line of `delegate: interrupted` starts on a line of its own
            raise
        if not answer:
            print(flush=True)
            _error("no run chosen", raw)
            return None
        text = answer.strip() or "1"
        if text.isdecimal() and 1 <= int(text) <= len(runs):
            return runs[int(text) - 1]
        _error(f"{text} is not a number from 1 to {len(runs)}", raw)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from_position = 0 if args.from_position is None else args.from_position
    options = watch.WatchOptions(args.until, args.stall_minutes, args.max_minutes, from_position, args.poll_seconds)
    try:
        if args.run_id is None:
            runs = runs_of(args.repo, GitVersionControl())
            if runs.chosen is None:
                # More than one run is active: the reader chooses one, or, with no terminal, follow none.
                active = status.active_runs(runs.active, utc_now())  # an error prints no part of the list
                if not (sys.stdin.isatty() and sys.stdout.isatty()):
                    _error(status.choices_line(active), args.raw)
                    return watch.EXIT_ERROR
                picked = _pick(active, args.raw)
                if picked is None:
                    return watch.EXIT_ERROR
                journal = picked.journal
            elif isinstance(runs.chosen, FailedStart):
                # No run is active, and the newest run failed at start: it has no journal, also to go on from a position.
                Title(sys.stdout).set(f"delegate watch{SEPARATOR}{short_run_id(runs.chosen.run_id)}{SEPARATOR}failed")
                print(runs.chosen.line())
                return watch.EXIT_FAILED
            elif not runs.active and args.from_position is None:
                # No run is active: the newest run gets one line, not a replay of its journal.
                return _newest_run(watch.newest_run(runs.chosen), args.raw)
            else:
                # The one active run, or, with `--from` and no active run, the newest run from the position.
                journal = runs.chosen
        else:
            journal = journal_of(args.repo, args.run_id, GitVersionControl())
        sink: watch.Sink = watch.RawSink(_emit)
        live_view = not args.raw and sys.stdout.isatty()
        if live_view:
            screen = _Screen(sys.stdout)
            sink = live.LiveView(journal, stream_reader_of, utc_now, screen.size, screen.draw, args.stall_minutes)
        title = Title(sys.stdout)
        if title.enabled:
            sink = _Titled(sink, journal, title)
        result = watch.follow(journal, options, sink)
    except (RunsError, JournalError) as error:
        _error(str(error), args.raw)
        return watch.EXIT_ERROR
    if result.code == watch.EXIT_INTERRUPTED:
        if live_view:
            sys.stdout.write(SHOW_CURSOR)
            sys.stdout.flush()
        run_id = journal.parent.name
        print(
            f"watch: stopped at position {result.position}; the run goes on. "
            f"Resume with delegate watch {run_id} --from {result.position}",
            file=sys.stderr,
        )
        return result.code
    print(f"position {result.position}")
    return result.code
