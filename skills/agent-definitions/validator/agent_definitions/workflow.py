"""The workflow file: a declarative TOML description of one delegation run.

`load_workflow` reads and checks the file. `plan_order` gives the tickets in
dependency order. Nothing here touches git or the harness.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .tiers import Tiers


# The two modes of a run.
MODES: tuple[str, ...] = ("assure", "economy")

# The harness adapters that a workflow can name. A later change replaces this
# list with the adapter registry.
ADAPTERS: tuple[str, ...] = ("claude-code", "cursor", "opencode")

# The roles whose tier a workflow can override.
ROLES: tuple[str, ...] = ("specialist", "verifier")


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


class WorkflowError(Exception):
    """A workflow file is invalid. `problems` holds one message for each defect."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


def _cycles(tickets: list[Ticket]) -> list[list[str]]:
    """Each group of tickets that wait on each other, as a list of ticket ids.

    A group is a strongly connected set of tickets (Tarjan), so one group names
    every ticket that takes part in a cycle. A blocker id that no ticket has is
    skipped here; `load_workflow` reports it.
    """
    known = {t.id for t in tickets}
    blockers = {t.id: [b for b in t.blocked_by if b in known] for t in tickets}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []

    def visit(ticket_id: str) -> None:
        index[ticket_id] = low[ticket_id] = len(index)
        stack.append(ticket_id)
        on_stack.add(ticket_id)
        for blocker in blockers[ticket_id]:
            if blocker not in index:
                visit(blocker)
                low[ticket_id] = min(low[ticket_id], low[blocker])
            elif blocker in on_stack:
                low[ticket_id] = min(low[ticket_id], index[blocker])
        if low[ticket_id] == index[ticket_id]:
            group = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                group.append(member)
                if member == ticket_id:
                    break
            if len(group) > 1 or ticket_id in blockers[ticket_id]:
                found.append(sorted(group))

    for ticket in tickets:
        if ticket.id not in index:
            visit(ticket.id)
    return found


def _tier_override_problems(overrides: dict[str, str], tiers: Tiers) -> list[str]:
    """One message for each override whose role or value is not valid.

    A value must be a tier name of the table. A model identifier is a different
    thing, and the message says so: the workflow names strengths, never models.
    """
    models = {model for columns in tiers.tiers.values() for model in columns.values()}
    models.update(model for allowed in tiers.allowed_models.values() for model in allowed)
    problems: list[str] = []
    for role, value in overrides.items():
        field = f"tier-overrides.{role}"
        if role not in ROLES:
            problems.append(f"{field}: unknown role {role!r}; known roles: {', '.join(ROLES)}")
        elif value not in tiers.tiers:
            kind = "a model identifier, not a tier name" if value in models or "/" in value else "not a tier name"
            problems.append(f"{field}: {value!r} is {kind}; known tiers: {', '.join(sorted(tiers.tiers))}")
    return problems


def load_workflow(path: Path, tiers: Tiers) -> Workflow:
    """Read and check a workflow file. Raise WorkflowError with every defect found."""
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
    problems: list[str] = []
    known_ids = {t.id for t in tickets}
    for ticket in tickets:
        if ticket.stack not in stacks:
            problems.append(f"ticket {ticket.id!r}: unknown stack {ticket.stack!r}; known stacks: {', '.join(sorted(stacks))}")
        for blocker in ticket.blocked_by:
            if blocker not in known_ids:
                problems.append(f"ticket {ticket.id!r}: unknown blocker {blocker!r}")
    if data["mode"] not in MODES:
        problems.append(f"mode: {data['mode']!r} is not a mode; known modes: {', '.join(MODES)}")
    if data["adapter"] not in ADAPTERS:
        problems.append(f"adapter: {data['adapter']!r} is not an adapter; known adapters: {', '.join(ADAPTERS)}")
    problems.extend(_tier_override_problems(dict(data.get("tier-overrides", {})), tiers))
    for cycle in _cycles(tickets):
        problems.append("dependency cycle among tickets " + ", ".join(repr(i) for i in cycle))
    if problems:
        raise WorkflowError(problems)
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
    Raise ValueError on a cycle; `load_workflow` already refuses one.
    """
    done: list[Ticket] = []
    done_ids: set[str] = set()
    pending = list(workflow.tickets)
    while pending:
        ready = next((t for t in pending if set(t.blocked_by) <= done_ids), None)
        if ready is None:
            raise ValueError(f"tickets {[t.id for t in pending]} wait on each other")
        pending.remove(ready)
        done.append(ready)
        done_ids.add(ready.id)
    return done
