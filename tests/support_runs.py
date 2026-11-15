"""The helpers of the tests of `delegate runs`: the index of hosts, the records of a run, and the call of the command (#354)."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from delegate.adapters.run_index import DirectoryRunIndex
from delegate.cli import delegate
from delegate.ports.index import RunRecord

from .runs.journals import NOW, built, clock_time, run_end, working, write_run
from .support import Terminal


def fixup_started(ticket: str, round_number: int, at):
    return at, "fixup-start", {
        "ticket": ticket, "round": round_number, "rejected_commit": "3", "findings_file": "/f", "agent": "s", "tier": "strong", "model": "m",
    }


def failed_end(at):
    return at, "run-end", {"result": "failed", "built": [], "failed": ["1"], "skipped": []}


def every_kind(root) -> None:
    """Six runs: an idle agent, a fix-up round, paused, failed, built, and a run that works with nothing wrong."""
    write_run(root, "alpha", "iiii", clock_time(13, 0), [working("1", clock_time(14, 0)), agent_idle("1", clock_time(14, 10))],
              adapter="claude-code", lock=True)
    write_run(root, "beta", "ffff", clock_time(13, 1), [working("1", clock_time(14, 0)), fixup_started("1", 1, clock_time(14, 15))],
              adapter="claude-code", lock=True)
    write_run(root, "gamma", "pppp", clock_time(13, 2), [(clock_time(13, 40), "paused", {"ticket": "1", "before": "step"})])
    started = clock_time(13, 3)
    write_run(root, "delta", "llll", started, [working("1", started), failed_end(clock_time(13, 30))], tickets=("1",))
    write_run(root, "epsilon", "bbbb", clock_time(13, 4), [*built("1", clock_time(13, 4), clock_time(13, 30)), run_end(clock_time(13, 30))], tickets=("1",))
    write_run(root, "zeta", "rrrr", clock_time(13, 5), [working("1", clock_time(14, 18))], lock=True)


def agent_idle(ticket: str, at):
    return at, "agent-start", {"ticket": ticket, "role": "specialist", "round": 0, "event_stream": f"/s/{ticket}.stream.jsonl"}


def failed_start(root) -> None:
    """A run that failed at start: it has a log and no journal. The log last changed at 13:30."""
    log = root / "theta-0123456789ab" / "runs" / "20261114T133000Z-ssss" / "run.log"
    log.parent.mkdir(parents=True)
    log.write_text("delegate run: the workflow file is missing\n")
    stamp = clock_time(13, 30).timestamp()
    os.utime(log, (stamp, stamp))


def config_file(config_home: Path, text: str) -> Path:
    """Write the configuration file of the user with `text`, and return its path."""
    path = config_home / "delegate" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def index(tmp_path, monkeypatch) -> DirectoryRunIndex:
    """The run index of the case: the directory that `DELEGATE_INDEX` names."""
    directory = tmp_path / "shared-index"
    monkeypatch.setenv("DELEGATE_INDEX", str(directory))
    return DirectoryRunIndex(directory)


def record(
    index: DirectoryRunIndex, host: str, repo: str, short: str, state: str, ended: int, total: int, heard_minutes_ago: int,
    eta: datetime | None = None, started: datetime | None = None,
) -> RunRecord:
    """Keep the record of a run of `host` that the host wrote `heard_minutes_ago` minutes before `NOW`."""
    started = started or clock_time(12, 0)
    kept = RunRecord(
        run_id=f"{started.strftime('%Y%m%dT%H%M%SZ')}-{short}", host=host, repo=repo, started=started.isoformat(), state=state,
        tickets_ended=ended, tickets_total=total, eta=eta.isoformat() if eta else None,
        heard=(NOW - timedelta(minutes=heard_minutes_ago)).isoformat(),
    )
    index.record(kept)
    return kept


def this_machine(root) -> None:
    """One run of `laptop`, in its journals: 1 of 2 tickets ended, a ticket runs now. It started at 13:40."""
    started = clock_time(13, 40)
    write_run(
        root, "alpha", "aaaa", started,
        [*built("1", started, clock_time(14, 0), tokens=(12_000, 340_000)), working("2", clock_time(14, 0))],
        adapter="claude-code", lock=True,
    )


def two_other_hosts(index: DirectoryRunIndex) -> None:
    """`build-host` has a run with 3 of 6 tickets, last heard 47 minutes ago. `ci-runner` has a run with 3 of 10, heard 3 hours 5 minutes ago."""
    record(index, "build-host", "docs-site", "a41e", "running", 3, 6, 47, eta=clock_time(16, 5))
    record(index, "ci-runner", "api-service", "5d02", "running", 3, 10, 185, started=clock_time(9, 0))


def document(host: str, *found: dict[str, object], format: int | None = 1) -> dict[str, object]:
    """The document of `delegate runs --local --json` of `host`: its fields, and the runs `found`. With `format` None, the document has no `format`."""
    fields = {"format": format, "host": host, "time": NOW.isoformat(), "window_seconds": 21600, "runs": list(found)}
    return {key: value for key, value in fields.items() if key != "format" or format is not None}


def reason_of(lines: list[str], host: str) -> str:
    """The text of the row under the row of `host`, after its rail: the reason that the command could not read the host (#323)."""
    row = next(number for number, line in enumerate(lines) if line.startswith(f"{host} "))
    return lines[row + 1].removeprefix("│").strip()


def run_of(
    host: str, repo: str, short: str, state: str, ended: int, total: int, eta: str | None = "16:05", tokens: tuple[int, int] = (20_000, 500_000),
    alert: str | None = None, do: str | None = None, trend: tuple[int, ...] = (0, 0, 1, 1, 1, 0), idle_seconds: int | None = None,
    fixup: tuple[int, int] | None = None, failed_start: bool = False,
) -> dict[str, object]:
    """One run of the document of a host. It started at 12:00, and it has not ended. `alert` is the text for a person; the reader uses `idle_seconds`, `fixup` and `failed_start`."""
    started = clock_time(12, 0)
    return {
        "run_id": f"{started.strftime('%Y%m%dT%H%M%SZ')}-{short}", "repo": repo, "host": host, "state": state, "started": started.isoformat(), "ended": None,
        "tickets_ended": ended, "tickets_total": total, "eta": clock_time(*map(int, eta.split(":"))).isoformat() if eta else None,
        "tokens_new": tokens[0], "tokens_cache": tokens[1], "alert": alert, "do": do, "trend": list(trend), "slip_seconds": None,
        "idle_seconds": idle_seconds, "fixup_round": fixup[0] if fixup else None, "fixup_limit": fixup[1] if fixup else None, "failed_start": failed_start,
    }


def ctrl_c() -> None:
    raise KeyboardInterrupt


def watching(monkeypatch, capsys, *argv: str, columns: int = 80, lines: int = 1000, out: Terminal | None = None) -> tuple[int, str, str]:
    """Run `delegate runs --watch` on a terminal. Return the exit code, what the terminal got, and stderr."""
    terminal = Terminal() if out is None else out
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(lines))
    capsys.readouterr()
    try:
        code = delegate.main(["runs", "--watch", *argv])
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C left `delegate runs --watch` as a KeyboardInterrupt, which prints a traceback")
    return code, terminal.getvalue(), capsys.readouterr().err
