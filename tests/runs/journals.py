"""The helpers of the tests of `delegate runs`: the journals of runs in the state directories of a state root of the case."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from delegate.cli import delegate

from ..support import hold_lock

NOW = datetime(2026, 11, 14, 14, 20, 0, tzinfo=timezone.utc)


def clock_time(hours: int, minutes: int, days_back: int = 0) -> datetime:
    return (NOW - timedelta(days=days_back)).replace(hour=hours, minute=minutes)


def write_run(
    root: Path, repo: str, short: str, started: datetime, events: list[tuple[datetime, str, dict[str, object]]],
    tickets: tuple[str, ...] = ("1", "2"), adapter: str = "scripted", lock: bool = False,
) -> Path:
    """Write the journal of the run `<started>-<short>` in the state directory `<root>/<repo>-<key>`, and return its path."""
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{short}"
    path = root / f"{repo}-0123456789ab" / "runs" / run_id / "journal.jsonl"
    path.parent.mkdir(parents=True)
    start = {
        "run_id": run_id, "workflow": "/w/workflow.toml", "mode": "assure", "adapter": adapter, "base_branch": "main",
        "run_branch": "run/demo", "tickets": list(tickets),
    }
    lines = []
    for seq, (at, name, fields) in enumerate([(started, "run-start", start), *events], start=1):
        lines.append(json.dumps({"seq": seq, "time": at.isoformat(), "event": name, **fields}))
    path.write_text("\n".join(lines) + "\n")
    if lock:
        hold_lock(path)
    return path


def built(ticket: str, start: datetime, end: datetime, tokens: tuple[int, int] | None = None) -> list[tuple[datetime, str, dict[str, object]]]:
    """The events of a ticket that ran from `start` to `end` and ended built; `tokens` is the totals of its specialist."""
    events: list[tuple[datetime, str, dict[str, object]]] = [(start, "step-start", {
        "ticket": ticket, "stack": "python", "branch": f"run/demo-{ticket}", "worktree": f"/w/{ticket}", "base_commit": "0" * 40,
        "agent": "python-specialist", "tier": "strong", "model": "m",
    })]
    if tokens is not None:
        events.append((end, "adapter-result", {
            "ticket": ticket, "event_stream": f"/s/{ticket}", "tokens_new": tokens[0], "tokens_cache": tokens[1],
        }))
    events.append((end, "step-end", {"ticket": ticket, "state": "built", "reason": None}))
    return events


def run_end(at: datetime) -> tuple[datetime, str, dict[str, object]]:
    return at, "run-end", {"result": "built", "built": ["1"], "failed": [], "skipped": []}


def runs(capsys, *argv: str) -> tuple[int, str, str]:
    code = delegate.main(["runs", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def working(ticket: str, at: datetime) -> tuple[datetime, str, dict[str, object]]:
    """The `step-start` of a ticket that runs from `at`."""
    return at, "step-start", {
        "ticket": ticket, "stack": "python", "branch": f"run/demo-{ticket}", "worktree": f"/w/{ticket}", "base_commit": "0" * 40,
        "agent": "python-specialist", "tier": "strong", "model": "m",
    }


def agent_started(ticket: str, at: datetime) -> tuple[datetime, str, dict[str, object]]:
    """The `agent-start` of the specialist of a ticket at `at`. Its stream file does not exist, so the agent is idle since `at`."""
    return at, "agent-start", {"ticket": ticket, "role": "specialist", "round": 0, "event_stream": f"/s/{ticket}.stream.jsonl"}
