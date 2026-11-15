"""The `delegate runs` command: parse the arguments, find the state root of this machine, and print the table of its runs (#307).

The use case lives in `delegate.run.fleet`, which never imports an adapter. This module is the composition point of the
command: it gives the use case the name of the machine, the time of the clock, and the stream reader of the harness
adapter that each run names (`delegate.cli.status.stream_reader_of`, as `delegate status` and `delegate watch` use it).

With `--watch` (#313), the command redraws the view in place on the terminal, as `delegate watch` redraws its frame (`delegate.cli.screen`):
once for each `WATCH_POLL_SECONDS`, no alternate screen, no spinner, and the size of the terminal read again at each redraw. Each redraw reads each
journal only from its last position (`JournalReads`), opens no journal that did not change (#350), lists the run index again only when its directory changed, and keeps the ETA of 2 hours ago of
each run for a minute (`EtaMemory`). The command ends with Ctrl-C, SIGTERM or SIGHUP, as the live view of `delegate watch`
does: the cursor shows again, and `delegate` prints `delegate: interrupted` and exits with 130. A journal that cannot be read goes on stderr
once, before the first frame when the first read finds it, and else after the last frame, so no line of stderr breaks a frame.

The run index is always on (#323): `DELEGATE_INDEX`, else the key `index` of the configuration file of the user, else `index/` in the state root
(`delegate.cli.machine_settings`). Each form of the command writes the record of each listed run of this machine that has none, or an older one.
A configuration file that cannot be used stops the command with exit 2 and one line.

The view also has a group for each other host (#317): each host of the records of the index and each host of the key `hosts` of the configuration
file. `--local` limits the view to this machine and reads no other host, and `--json` describes this machine only and reads no other host.

The command reads each other host over ssh (#318, `delegate.adapters.ssh_hosts`): it gives the use case a `HostReads` with the ssh backend and
the store of last answers (`delegate.run.last_answers`) in the state root. `--watch` keeps one `HostReads` for all its redraws, so it reads the other
hosts at most each 30 seconds. Its reads go on in the background (#328): a redraw does not wait for a slow host, and shows its last answer until the
new one comes. Ctrl-C ends each `ssh` of a read at once, with and without `--watch`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys

from ..adapters.ssh_hosts import SshHostReader
from ..run import clock, status
from ..run.fleet import EtaMemory, fleet_of
from ..run.fleet_hosts import HostReads
from ..run.fleet_model import ViewSettings
from ..run.fleet_view import fleet_lines
from ..run.journal import JournalReads
from ..run.host import host_name
from ..run.last_answers import LAST_ANSWERS_NAME, LastAnswers
from ..run.runs import RunsError
from ..run.user_config import UserConfigError
from .machine_settings import MachineSettings, machine_settings
from .epilog import RUNS_PAGE, HelpFormatter, epilog
from .screen import Screen
from .status import stream_reader_of, utc_now
from .stop_signals import stop_signals_as_ctrl_c

# The exit status of a state root or a configuration file that cannot be used, or of a lock file that cannot be read.
EXIT_ERROR: int = 2

# The seconds between two redraws of `--watch`: the poll rate of `delegate watch`.
WATCH_POLL_SECONDS: float = 1.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delegate runs",
        description=(
            "List the runs of this machine in one table, across each repository: the runs that have not ended, and the runs that ended in the last 6 hours. "
            "List the runs of the other hosts of the run index and of the key hosts of the configuration file too."
        ),
        epilog=epilog(RUNS_PAGE),
        formatter_class=HelpFormatter,
    )
    form = parser.add_mutually_exclusive_group()
    form.add_argument(
        "--json", action="store_true",
        help="print one object as JSON: the format, the host, the time, the window, and one object for each listed run; cannot be used with --watch",
    )
    form.add_argument(
        "--watch", action="store_true",
        help="redraw the table in place once a second, until Ctrl-C; it needs a terminal, and it cannot be used with --json",
    )
    parser.add_argument(
        "--local", action="store_true",
        help="show only the runs of this machine and read no other host; it still writes the records of the runs of this machine that have none",
    )
    parser.add_argument(
        "--timeline", action="store_true",
        help="draw one lane for each run over the last 6 hours, to the latest ETA, in place of the table; it cannot be used with --json",
    )
    return parser


def _settings() -> MachineSettings | None:
    """The settings of this machine, or None after one line on stderr when the configuration file or the state root cannot be used."""
    try:
        return machine_settings()
    except (UserConfigError, RunsError) as error:
        print(f"delegate runs: {error}", file=sys.stderr)
        return None


def _view(settings: MachineSettings, local: bool) -> ViewSettings:
    """What the header line names, and the hosts of the key `hosts` to read; `--local` reads none of them."""
    return ViewSettings(
        config=settings.config.path, shared_index=settings.index.directory if settings.index.shared else None,
        hosts=() if local else settings.config.hosts,
    )


def _host_reads(settings: MachineSettings, background: bool = False) -> HostReads:
    """The reads of the other hosts over ssh, with the store of last answers in the state root. With `background`, a redraw does not wait for them (#328)."""
    return HostReads(SshHostReader(), store=LastAnswers(settings.root / LAST_ANSWERS_NAME), background=background)


def _watch(settings: MachineSettings, timeline: bool, local: bool) -> int:
    """Redraw the table in place until Ctrl-C. Return 2 with no terminal, or when the state root cannot be used."""
    if not sys.stdout.isatty():
        print("delegate runs: --watch needs a terminal", file=sys.stderr)
        return EXIT_ERROR
    the_clock = clock.current()
    screen, reads, hosts = Screen(sys.stdout), JournalReads(), _host_reads(settings, background=True)
    etas = EtaMemory()
    # One reader of each adapter for all the redraws, so a stream that grew is read from its last place (#352).
    reader_of = status.caching(stream_reader_of)
    reported: set[str] = set()  # the lines of the journals that cannot be read, so each goes on stderr once
    late: list[str] = []
    first = True
    try:
        try:
            with stop_signals_as_ctrl_c():
                while True:
                    found = fleet_of(
                        settings.root, host_name(), utc_now(), reader_of, reads, settings.index.index, not local, hosts, _view(settings, local), etas,
                    )
                    new = [line for line in found.unreadable_lines("delegate runs") if line not in reported]
                    reported.update(new)
                    if first:
                        for line in new:
                            print(line, file=sys.stderr)  # before the first frame, as without --watch
                    else:
                        late += new
                    width, height = screen.size()
                    screen.draw(fleet_lines(found, width, height or None, timeline))
                    first = False
                    the_clock.sleep(WATCH_POLL_SECONDS)
        finally:
            screen.restore()  # on each end, before the last lines
            hosts.close()  # Ctrl-C ends each read that runs at once (#328)
            for line in late:
                print(line, file=sys.stderr)
    except RunsError as error:
        print(f"delegate runs: {error}", file=sys.stderr)
        return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeline and args.json:
        parser.error("--timeline cannot be used with --json")
    settings = _settings()
    if settings is None:
        return EXIT_ERROR
    if args.watch:
        return _watch(settings, args.timeline, args.local)
    local = args.local or args.json
    try:
        with _host_reads(settings) as hosts:  # Ctrl-C during a read ends the reads at once (#328)
            found = fleet_of(
                settings.root, host_name(), utc_now(), stream_reader_of, index=settings.index.index, read_index=not local,
                hosts=hosts, view=_view(settings, local),
            )
    except RunsError as error:
        print(f"delegate runs: {error}", file=sys.stderr)
        return EXIT_ERROR
    for line in found.unreadable_lines("delegate runs"):
        print(line, file=sys.stderr)
    if args.json:
        print(json.dumps(found.to_json(), indent=2))
        return 0
    size = shutil.get_terminal_size(fallback=(80, 0))  # a terminal that gives no height: the lines are 0, and no row folds
    print("\n".join(fleet_lines(found, size.columns, size.lines or None, args.timeline)))
    return 0
