"""The reads of the other hosts for `delegate runs` (#334): the answers of the host reader, the document of a host, and its last-known runs.

With a host reader (#318, `delegate.ports.hosts`), `fleet_of` reads each other host, at the same time, with a time limit (`HOST_READ_SECONDS`). For
`delegate runs --watch`, the reads go on in the background (#328, `HostReads`): a redraw does not wait for a slow host, and shows its last answer until the new one comes. A host that
gives the document of `delegate runs --local --json` with a `format` that this version knows shows its runs as live runs: they are in the sums, with the alert and the
`do` word that the host worked out, and the store of last answers keeps the document. A host whose read fails, times out, or gives another `format` is unreachable.
`HostReads` keeps the answers, so `delegate runs --watch` reads the other hosts at most each `HOST_READ_INTERVAL_SECONDS`.

A host that is not read, or whose read fails (#318), is `unreachable` (C25): its runs are the last-known values, dim, with no ETA, no tokens and no trend: the runs of its last answer in the
store of last answers (`delegate.run.last_answers`), or of its records in the run index (#317, #323), the newer of the two. A run that ended before the window is not listed.
`other_hosts` gives the rows of all the other hosts, in the order of the table, and each host that it could not read.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import datetime

from ..ports.hosts import OLDER_VERSION, HostReader, Unreachable
from ..ports.index import RunRecord
from . import clock, fleet_rules
from .fleet_model import FORMAT, LostHost, RunRow
from .fleet_rules import in_window
from .last_answers import LastAnswers
from .status_model import HALTED, PAUSED, PAUSING, RUNNING, STOPPED

# The seconds that the read of one other host may take (#318), and the seconds between two reads of the same hosts in `delegate runs --watch`.
HOST_READ_SECONDS: float = 10.0
HOST_READ_INTERVAL_SECONDS: float = 30.0

# The seconds that a redraw of `delegate runs --watch` waits for the reads that it starts (#328). A host that answers in this time is in the frame;
# for a slower host, the frame shows its last answer, and a later redraw shows the new one.
HOST_WAIT_SECONDS: float = 0.3

# The states of a run that has no `run-end`. The state of a record of the index that is not one of them is the result of a `run-end`.
OPEN_STATES: frozenset[str] = frozenset({RUNNING, PAUSING, PAUSED, HALTED, STOPPED})

# The first version of `delegate` that has `delegate runs --local --json` (#317, #318). A host with an earlier version is unreachable.
RUNS_VERSION: str = "1.22.0"


def _remote(record: RunRecord, now: datetime) -> RunRow | None:
    """The row of the run that a record of the index describes, or None when the record is not listed (#317).

    The run is unreachable: its values are the last known. A run whose state is the result of a `run-end` ended when the record was written
    (`heard`), and it is not listed when that time is out of the window. A record with a time that cannot be read is not listed.
    """
    started, heard = record.started_time(), record.heard_time()
    if started is None or heard is None:
        return None
    ended = record.state not in OPEN_STATES
    if ended and not in_window(heard, now):
        return None
    return RunRow(
        record.run_id, record.repo, record.host, record.state, started, heard if ended else None, record.tickets_ended, record.tickets_total,
        None, None, None, last_event=heard, unreachable=True, heard=heard,
    )


class HostReads:
    """The answers of the host reader (`delegate.ports.hosts`) for the other hosts of the index (#318).

    `read` asks the reader for the hosts at the same time, each in a thread of its own, with the time limit `HOST_READ_SECONDS`. It keeps the answers: a call
    less than `interval` seconds after the last read of all hosts asks only for a host it has no answer for. `delegate runs --watch` uses one `HostReads` for
    all its redraws, so it reads the other hosts at most each `interval` seconds. The time is the monotonic time of the clock of the process. A host is never
    read twice at the same time.

    With `background` False, `read` waits for each read that it needs. With `background` True (#328, `delegate runs --watch`), `read` waits at most
    `HOST_WAIT_SECONDS` for the reads that it starts, and the reads go on after it returns: a host whose read has not ended gives its answer of the read
    before, and a host with no answer yet gives `Unreachable` with the reason `no answer from <host> yet`, so the view shows its last-known runs with their age.
    A later call collects the answers that came.

    `close` ends each read that runs at once: the reader ends the processes that it started (Ctrl-C), and `close` returns when the reads have ended.
    Use `HostReads` as a context manager, or call `close` when the command ends.
    """

    def __init__(
        self, reader: HostReader, interval: float | None = None, store: LastAnswers | None = None, background: bool = False,
    ) -> None:
        self._reader = reader
        self._interval = HOST_READ_INTERVAL_SECONDS if interval is None else interval
        self._answers: dict[str, dict[str, object] | Unreachable] = {}
        self._read_at: float | None = None
        self._store = store
        self._fresh: set[str] = set()  # the hosts of the newest read whose answer the store has not kept yet
        self._background = background
        self._running: dict[str, Future[dict[str, object] | Unreachable]] = {}
        self._stop = threading.Event()

    def __enter__(self) -> HostReads:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _ask(self, host: str) -> dict[str, object] | Unreachable:
        try:
            return self._reader.read_runs(host, HOST_READ_SECONDS, self._stop)
        except Exception as error:  # noqa: BLE001 - a host that cannot be read is unreachable, whatever the reason
            return Unreachable(str(error) or type(error).__name__)

    def read(self, hosts: Sequence[str]) -> dict[str, dict[str, object] | Unreachable]:
        """The answer for each host of `hosts`."""
        now = clock.current().monotonic()
        stale = self._read_at is None or now - self._read_at >= self._interval
        wanted = [host for host in hosts if (stale or host not in self._answers) and host not in self._running]
        if wanted:
            pool = ThreadPoolExecutor(max_workers=len(wanted))
            self._running.update((host, pool.submit(self._ask, host)) for host in wanted)
            pool.shutdown(wait=False)  # the threads end when their reads end
            if stale:
                self._read_at = now
        running = [self._running[host] for host in hosts if host in self._running]
        if running:
            wait(running, timeout=(HOST_WAIT_SECONDS if wanted else 0) if self._background else None)
        for host, future in list(self._running.items()):
            if future.done():
                del self._running[host]
                self._answers[host] = future.result()
                self._fresh.add(host)
        return {host: self._answers.get(host, Unreachable(f"no answer from {host} yet")) for host in hosts}

    def close(self) -> None:
        """End each read that runs, and wait until the reads have ended."""
        self._stop.set()
        wait(list(self._running.values()))
        self._running.clear()

    def keep(self, host: str, document: dict[str, object], heard: datetime) -> None:
        """Keep the document of `host` in the store of last answers (#323), once for each read of the host. With no store, do nothing."""
        if self._store is not None and host in self._fresh:
            self._store.keep(host, heard, document)
        self._fresh.discard(host)

    def last(self, host: str) -> tuple[datetime, dict[str, object]] | None:
        """The time and the document of the last answer of `host` in the store, or None."""
        found = None if self._store is None else self._store.last(host)
        return None if found is None else (found.heard, found.document)


def _count(value: object) -> int:
    """A count of a document: a whole number, and not a boolean. Raise ValueError for anything else."""
    if type(value) is not int:
        raise ValueError(f"{value!r} is not a whole number")
    return value


def _moment(value: object) -> datetime:
    """A time of a document: ISO 8601 text with a zone. Raise ValueError for anything else."""
    moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    if moment is None or moment.tzinfo is None:
        raise ValueError(f"{value!r} is not a time with a zone")
    return moment


def _optional_count(value: object) -> int | None:
    """A count of a document that can be `null`: a whole number, or None. Raise ValueError for anything else."""
    return None if value is None else _count(value)


def _flag(value: object) -> bool:
    """A flag of a document: true or false. Raise ValueError for anything else."""
    if type(value) is not bool:
        raise ValueError(f"{value!r} is not true or false")
    return value


def _fixup_of(round_of: object, limit_of: object) -> tuple[int, int] | None:
    """The fix-up round and its limit of a document, as `RunRow.fixup` holds them: both numbers, or both `null`. Raise ValueError for one number."""
    found = (_optional_count(round_of), _optional_count(limit_of))
    if (found[0] is None) != (found[1] is None):
        raise ValueError("fixup_round and fixup_limit are not both set or both null")
    return None if found[0] is None or found[1] is None else (found[0], found[1])


def _live_run(item: object, host: str, taken: datetime) -> RunRow:
    """The row of one run of the document of a host. Raise ValueError, KeyError or TypeError for a run that this version cannot read.

    The values the document holds are the values of the host at `taken`. The alert comes from the fields `idle_seconds`, `fixup_round`, `fixup_limit`
    and `failed_start` (#329): the reader parses no text. The fields `tokens_new`, `tokens_cache`, `trend` and `slip_seconds` can be missing. The rows are of the group `host`, the name that the index has for it.
    """
    if not isinstance(item, dict):
        raise TypeError("a run is not an object")
    state = str(item["state"])
    total = _count(item["tickets_total"])
    ended = None if item["ended"] is None else _moment(item["ended"])
    eta = None if item.get("eta") is None else _moment(item["eta"])
    trend = tuple(_count(cell) for cell in item.get("trend", (0,) * fleet_rules.TREND_HOURS))
    if len(trend) != fleet_rules.TREND_HOURS:
        raise ValueError("trend has the wrong number of cells")
    slip = item.get("slip_seconds")
    idle = _optional_count(item["idle_seconds"])
    fixup = _fixup_of(item["fixup_round"], item["fixup_limit"])
    tokens = [item.get(name) for name in ("tokens_new", "tokens_cache")]
    return RunRow(
        str(item["run_id"]), str(item["repo"]), host, state, _moment(item["started"]), ended, _count(item["tickets_ended"]), total, eta,
        *(None if count is None else _count(count) for count in tokens),
        failed_start=_flag(item["failed_start"]), idle_seconds=idle, fixup=fixup, trend=trend,
        slip_seconds=None if slip is None else _count(slip), last_event=taken,
    )


def older_reason(host: str) -> str:
    """The reason of a host whose `delegate` has no `delegate runs`, or gives a `format` before `FORMAT` (#323)."""
    return f"delegate on {host} is older than {RUNS_VERSION}"


def _live(answer: dict[str, object] | Unreachable, host: str) -> list[RunRow] | Unreachable:
    """The live runs that the document of `host` holds (#318), or `Unreachable` with the reason (#323).

    The host is unreachable when the answer is `Unreachable`, when the `format` of the document is not `FORMAT`, and when the document is not made as
    `Fleet.to_json` makes it. A document with no `format`, or an earlier one, is of a `delegate` older than `RUNS_VERSION`.
    """
    if isinstance(answer, Unreachable):
        return Unreachable(older_reason(host)) if answer.kind == OLDER_VERSION else answer
    found = answer.get("format")
    if type(found) is not int or found < FORMAT:
        return Unreachable(older_reason(host))
    if found > FORMAT:
        return Unreachable(f"delegate on {host} is newer than this one (format {found}); update delegate on this machine")
    try:
        taken = _moment(answer["time"])
        items = answer["runs"]
        if not isinstance(items, list):
            raise TypeError("runs is not a list")
        return [_live_run(item, host, taken) for item in items]
    except (KeyError, TypeError, ValueError):
        return Unreachable(f"{host} gave a document that this version of delegate cannot read")


def _last_known(name: str, records: Sequence[RunRecord], hosts: HostReads | None, now: datetime) -> tuple[list[RunRow], datetime | None]:
    """The last-known runs of the host `name` that the command could not read, and the time it was last heard from, or None.

    They are the runs of its last answer in the store of last answers (#323), or the runs of its records in the index (#317): the newer of the two.
    Each run is unreachable: dim, with no ETA, no tokens, no trend, no alert and no command. A run that ended before the window is not listed.
    """
    own = [record for record in records if record.host == name]
    recorded = max((heard for heard in (record.heard_time() for record in own) if heard is not None), default=None)
    last = None if hosts is None else hosts.last(name)
    if last is not None and (recorded is None or last[0] >= recorded):
        heard, document = last
        answered = _live(document, name)
        if not isinstance(answered, Unreachable):
            rows = [
                replace(
                    row, eta=None, tokens_new=None, tokens_cache=None, idle_seconds=None, fixup=None, trend=(0,) * fleet_rules.TREND_HOURS,
                    slip_seconds=None, built=(), last_event=heard, unreachable=True, heard=heard,
                )
                for row in answered if row.ended is None or in_window(row.ended, now)
            ]
            return rows, heard
    return [row for row in (_remote(record, now) for record in own) if row is not None], recorded


def other_hosts(
    records: Sequence[RunRecord], host: str, now: datetime, hosts: HostReads | None, named: Sequence[str],
) -> tuple[list[RunRow], list[str], list[LostHost]]:
    """The rows of the runs of the other hosts, the name of each other host, and each of them that could not be read.

    The other hosts are the hosts of the records of the index and the hosts of the key `hosts` (#323), each one time; `host`, this machine, is not
    one of them. A host that `hosts` reads gives the runs of its document (#318), and the store of last answers keeps the document. Any other host
    is unreachable, with the reason: its runs are its last-known runs (`_last_known`); with no `hosts`, no host is read. The rows are in the order of
    the table within each host: the runs with no `run-end`, newest start first, then the runs that ended, newest end first. The hosts are in the order of their names.
    """
    names = sorted({record.host for record in records if record.host != host} | {name for name in named if name != host})
    answers = {} if hosts is None else hosts.read(names)
    ordered: list[RunRow] = []
    lost: list[LostHost] = []
    for name in names:
        answer = answers.get(name, Unreachable("not read"))
        live = _live(answer, name)
        if isinstance(live, Unreachable):
            rows, heard = _last_known(name, records, hosts, now)
            lost.append(LostHost(name, live.reason, heard))
        else:
            rows = live
            if hosts is not None and isinstance(answer, dict):
                hosts.keep(name, answer, now)
        ordered += sorted((row for row in rows if row.ended is None), key=lambda row: row.started, reverse=True)
        ordered += sorted((row for row in rows if row.ended is not None), key=lambda row: row.ended or row.started, reverse=True)
    return ordered, names, lost
