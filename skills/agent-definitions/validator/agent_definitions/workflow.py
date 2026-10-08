"""The workflow file: a declarative TOML description of one delegation run.

`load_workflow` reads and checks the file. `plan_order` gives the tickets in
dependency order. Nothing here touches git or the harness.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Stack:
    name: str
    specialist: str
    verifier: str
    gates: list[str]
    hotspots: list[str]


@dataclass(frozen=True)
class Ticket:
    id: str
    text: str
    stack: str
    blocked_by: list[str]


@dataclass(frozen=True)
class Workflow:
    base_branch: str
    run_branch: str
    mode: str
    adapter: str
    stacks: dict[str, Stack]
    tickets: list[Ticket]
    tier_overrides: dict[str, str]


def load_workflow(path: Path) -> Workflow:
    data = tomllib.loads(Path(path).read_text())
    stacks = {
        name: Stack(name, s["specialist"], s["verifier"], list(s["gates"]), list(s["hotspots"]))
        for name, s in data["stacks"].items()
    }
    root = Path(path).parent
    tickets = [
        Ticket(
            t["id"],
            t["text"] if "text" in t else (root / t["text-file"]).read_text(),
            t["stack"],
            list(t["blocked-by"]),
        )
        for t in data["tickets"]
    ]
    return Workflow(
        data["base-branch"],
        data["run-branch"],
        data["mode"],
        data["adapter"],
        stacks,
        tickets,
        dict(data.get("tier-overrides", {})),
    )


def plan_order(workflow: Workflow) -> list[Ticket]:
    """The tickets so that each one follows every ticket that blocks it.

    Among tickets that are ready at the same time, the order of the file wins.
    """
    done: list[Ticket] = []
    done_ids: set[str] = set()
    pending = list(workflow.tickets)
    while pending:
        ready = next(t for t in pending if set(t.blocked_by) <= done_ids)
        pending.remove(ready)
        done.append(ready)
        done_ids.add(ready.id)
    return done
