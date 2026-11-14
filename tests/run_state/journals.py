"""The helpers of the tests of the run state: a journal as a list of events, with `seq` and `time` as the engine writes them."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

START = datetime(2026, 11, 14, 9, 0, 0, tzinfo=timezone.utc)

Item = tuple[str, dict[str, object]]


def ev(name: str, /, **fields: object) -> Item:
    """One event of a journal, by name and fields. `journal` gives it the `seq` and the `time`."""
    return name, fields


def journal(*items: Item, tickets: tuple[str, ...] = ("a", "b", "c"), mode: str = "assure", versioned: bool = True) -> list[dict[str, object]]:
    """The events of a run: `run-start` first, then `items`. One second passes between two events.

    `versioned` is False for a journal of an earlier version of the kit: its `run-start` has no `schema`.
    """
    start: dict[str, object] = {
        "run_id": "20261114T090000Z-test", "workflow": "/w/workflow.toml", "mode": mode, "adapter": "scripted",
        "base_branch": "main", "run_branch": "run/demo", "tickets": list(tickets),
    }
    if versioned:
        start["schema"] = 2
    events: list[dict[str, object]] = []
    for seq, (name, fields) in enumerate([("run-start", start), *items], start=1):
        events.append({"seq": seq, "time": (START + timedelta(seconds=seq)).isoformat(), "event": name, **fields})
    return events


def started(ticket: str) -> Item:
    return ev("step-start", ticket=ticket, stack="python")


def built(ticket: str) -> Item:
    return ev("step-end", ticket=ticket, state="built", reason=None)


def failed(ticket: str, reason: str | None = "the gates are red") -> Item:
    return ev("step-end", ticket=ticket, state="failed", reason=reason)


def skipped(ticket: str, reason: str = "blocked by a, which failed", blockers: list[str] | None = None) -> Item:
    return ev("skip", ticket=ticket, reason=reason, blockers=["a"] if blockers is None else blockers)


def plan_change(order: list[str], removed: list[str] | None = None, changed: list[str] | None = None, added: list[str] | None = None) -> Item:
    return ev(
        "plan-change", added=added or [], changed=changed or [], fields={}, removed=removed or [], order=order, reviewed=[], plan=[],
    )
