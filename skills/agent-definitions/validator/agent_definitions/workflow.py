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


class _Reader:
    """Reads fields out of parsed TOML and collects a message for each defect.

    Each reader method returns None after it records a problem, so the caller
    can go on and report every defect of the file in one run.
    """

    def __init__(self) -> None:
        self.problems: list[str] = []

    def table(self, value: object, field: str) -> dict[str, object] | None:
        if not isinstance(value, dict):
            self.problems.append(f"{field}: must be a table")
            return None
        return value

    def refuse_unknown(self, table: dict[str, object], allowed: tuple[str, ...], field: str) -> None:
        for key in table:
            if key not in allowed:
                prefix = f"{field}." if field else ""
                self.problems.append(f"{prefix}{key}: unknown field; known fields: {', '.join(allowed)}")

    def string(self, table: dict[str, object], key: str, field: str) -> str | None:
        if key not in table:
            self.problems.append(f"{field}: field is missing")
            return None
        value = table[key]
        if not isinstance(value, str):
            self.problems.append(f"{field}: must be a string, not {value!r}")
            return None
        if not value.strip():
            self.problems.append(f"{field}: must not be empty")
            return None
        return value

    def string_list(self, table: dict[str, object], key: str, field: str, *, at_least_one: bool) -> list[str] | None:
        if key not in table:
            self.problems.append(f"{field}: field is missing")
            return None
        value = table[key]
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            self.problems.append(f"{field}: must be a list of strings, not {value!r}")
            return None
        if at_least_one and not value:
            self.problems.append(f"{field}: needs at least one entry")
            return None
        return list(value)


TOP_LEVEL_FIELDS: tuple[str, ...] = ("base-branch", "run-branch", "mode", "adapter", "tier-overrides", "stacks", "tickets")
STACK_FIELDS: tuple[str, ...] = ("specialist", "verifier", "gates", "hotspots")
TICKET_FIELDS: tuple[str, ...] = ("id", "text", "text-file", "stack", "blocked-by")


def _read_stacks(reader: _Reader, value: object) -> dict[str, Stack]:
    stacks: dict[str, Stack] = {}
    table = reader.table(value, "stacks") if value is not None else None
    if value is None:
        reader.problems.append("stacks: field is missing")
    for name, raw in (table or {}).items():
        field = f"stacks.{name}"
        stack = reader.table(raw, field)
        if stack is None:
            continue
        reader.refuse_unknown(stack, STACK_FIELDS, field)
        specialist = reader.string(stack, "specialist", f"{field}.specialist")
        verifier = reader.string(stack, "verifier", f"{field}.verifier")
        gates = reader.string_list(stack, "gates", f"{field}.gates", at_least_one=True)
        hotspots = reader.string_list(stack, "hotspots", f"{field}.hotspots", at_least_one=False)
        if None not in (specialist, verifier, gates, hotspots):
            stacks[name] = Stack(name, specialist, verifier, gates, hotspots)  # type: ignore[arg-type]
    return stacks


def _read_tickets(reader: _Reader, value: object, root: Path) -> list[Ticket]:
    if not isinstance(value, list) or not value or not all(isinstance(item, dict) for item in value):
        reader.problems.append("tickets: needs at least one [[tickets]] table")
        return []
    tickets: list[Ticket] = []
    seen: set[str] = set()
    for position, raw in enumerate(value):
        ticket_id = reader.string(raw, "id", f"tickets[{position}].id")
        label = f"ticket {ticket_id!r}" if ticket_id is not None else f"tickets[{position}]"
        reader.refuse_unknown(raw, TICKET_FIELDS, label)
        if ticket_id is not None and ticket_id in seen:
            reader.problems.append(f"{label}: id appears twice")
        if ticket_id is not None:
            seen.add(ticket_id)
        text = _read_ticket_text(reader, raw, label, root)
        stack = reader.string(raw, "stack", f"{label}: stack")
        blocked_by = reader.string_list(raw, "blocked-by", f"{label}: blocked-by", at_least_one=False)
        if None not in (ticket_id, text, stack, blocked_by):
            tickets.append(Ticket(ticket_id, text, stack, blocked_by))  # type: ignore[arg-type]
    return tickets


def _read_ticket_text(reader: _Reader, raw: dict[str, object], label: str, root: Path) -> str | None:
    """The ticket text: the inline `text`, or the content of the file `text-file`."""
    if ("text" in raw) == ("text-file" in raw):
        reader.problems.append(f"{label}: needs exactly one of text and text-file")
        return None
    if "text" in raw:
        return reader.string(raw, "text", f"{label}: text")
    name = reader.string(raw, "text-file", f"{label}: text-file")
    if name is None:
        return None
    try:
        return (root / name).read_text()
    except FileNotFoundError:
        reader.problems.append(f"{label}: text-file {name!r} not found (looked in {root})")
    except OSError as error:
        reader.problems.append(f"{label}: text-file {name!r} cannot be read: {error}")
    return None


def load_workflow(path: Path, tiers: Tiers) -> Workflow:
    """Read and check a workflow file. Raise WorkflowError with every defect found.

    `text-file` paths are relative to the directory of the workflow file.
    """
    try:
        data = tomllib.loads(Path(path).read_text())
    except FileNotFoundError:
        raise WorkflowError(["workflow file not found"]) from None
    except OSError as error:
        raise WorkflowError([f"workflow file cannot be read: {error}"]) from None
    except tomllib.TOMLDecodeError as error:
        raise WorkflowError([f"not valid TOML: {error}"]) from None
    reader = _Reader()
    reader.refuse_unknown(data, TOP_LEVEL_FIELDS, "")
    base_branch = reader.string(data, "base-branch", "base-branch")
    run_branch = reader.string(data, "run-branch", "run-branch")
    mode = reader.string(data, "mode", "mode")
    adapter = reader.string(data, "adapter", "adapter")
    if base_branch is not None and base_branch == run_branch:
        reader.problems.append(f"run-branch: {run_branch!r} is the base-branch; the run needs its own branch")
    if mode is not None and mode not in MODES:
        reader.problems.append(f"mode: {mode!r} is not a mode; known modes: {', '.join(MODES)}")
    if adapter is not None and adapter not in ADAPTERS:
        reader.problems.append(f"adapter: {adapter!r} is not an adapter; known adapters: {', '.join(ADAPTERS)}")

    overrides_table = reader.table(data.get("tier-overrides", {}), "tier-overrides") or {}
    overrides = {role: value for role, value in overrides_table.items() if isinstance(value, str)}
    for role in overrides_table.keys() - overrides.keys():
        reader.problems.append(f"tier-overrides.{role}: must be a string, not {overrides_table[role]!r}")
    reader.problems.extend(_tier_override_problems(overrides, tiers))

    stacks = _read_stacks(reader, data.get("stacks"))
    tickets = _read_tickets(reader, data.get("tickets"), Path(path).parent)
    known_ids = {t.id for t in tickets}
    for ticket in tickets:
        if ticket.stack not in stacks:
            known = ", ".join(sorted(stacks))
            reader.problems.append(f"ticket {ticket.id!r}: unknown stack {ticket.stack!r}; known stacks: {known}")
        for blocker in ticket.blocked_by:
            if blocker not in known_ids:
                reader.problems.append(f"ticket {ticket.id!r}: unknown blocker {blocker!r}")
    for cycle in _cycles(tickets):
        reader.problems.append("dependency cycle among tickets " + ", ".join(repr(i) for i in cycle))
    if reader.problems:
        raise WorkflowError(reader.problems)
    return Workflow(base_branch, run_branch, mode, adapter, stacks, tickets, overrides)  # type: ignore[arg-type]


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
