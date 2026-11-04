"""The workflow file: a declarative TOML description of one delegation run.

`load_workflow` reads and checks the file. `plan_order` gives the tickets in
dependency order. Nothing here touches git or the harness.
"""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..ports.harness import McpServer
from ..shared.tiers import Tiers
from . import domain
from .domain import ASSURE, ECONOMY, MODES, START_BASE_BRANCH, START_RUN_BRANCH, TICKET_STARTS
from .output import gate_name

# The built-in harness adapters. A workflow can name these, and any adapter
# that the caller of `load_workflow` reports as registered (a test registers a scripted one).
ADAPTERS: tuple[str, ...] = ("claude-code", "opencode")

# The roles whose tier a workflow can override.
ROLES: tuple[str, ...] = ("specialist", "verifier")

# The strength order of the tiers that the bundled table names. A higher number is a stronger tier.
# `strong` and `verifier` have the same rank: both name the strongest model.
TIER_RANK: dict[str, int] = {"cheap": 0, "standard": 1, "strong": 2, "verifier": 2}

# The verifier tier never goes down, in any mode. A verifier override must rank at least this high.
VERIFIER_TIER_FLOOR: str = "verifier"


# A ticket id is a path segment of the worktree and a part of the ticket branch name (#181).
TICKET_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

_REF_FORBIDDEN_CHARS = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def ref_name_problem(name: str) -> str | None:
    """Why git would refuse `name` as a branch name, or None. The rules of `git check-ref-format --branch`.

    The check is in Python, because `run/` starts no process.
    """
    if _REF_FORBIDDEN_CHARS.search(name):
        return "holds a space, a control character, or one of ~ ^ : ? * [ \\"
    for part, reason in (("..", "holds '..'"), ("@{", "holds '@{'"), ("//", "holds '//'")):
        if part in name:
            return reason
    if name == "@":
        return "is '@'"
    if name.startswith(("-", "/")):
        return f"starts with {name[0]!r}"
    if name.endswith(("/", ".")):
        return f"ends with {name[-1]!r}"
    for component in name.split("/"):
        if component.startswith("."):
            return "has a part that starts with '.'"
        if component.endswith(".lock"):
            return "has a part that ends with '.lock'"
    return None


def _ticket_id_problem(ticket_id: str) -> str | None:
    if not TICKET_ID_PATTERN.fullmatch(ticket_id):
        return "must start with a letter or a digit and hold only letters, digits, '.', '_' and '-'"
    if ".." in ticket_id:
        return "must not hold '..'"
    return None


# The directories where a harness looks for a project agent (#188). The verifier runs in a clone of the ticket
# branch, so it can load its definition from the clone. An agent definition is never part of the work that a run
# builds: each stack has these paths as hotspots, in addition to the hotspots that the workflow names.
BUILT_IN_HOTSPOTS: tuple[str, ...] = (".claude/agents/", ".opencode/agents/")


@dataclass(frozen=True)
class Stack:
    name: str
    specialist: str
    verifier: str
    gates: list[str]
    hotspots: list[str]
    mcp_servers: tuple[McpServer, ...] = ()
    # The commands that the engine runs in each tree before an agent starts in it (#155). Empty: none.
    prepare: tuple[str, ...] = ()
    # The text that the engine adds to each brief of a role of this stack (#156). None: no notes.
    specialist_notes: str | None = None
    verifier_notes: str | None = None
    # The time limit of one gate run and of one agent session of this stack, in minutes (#179). None: no limit.
    gate_timeout: float | None = None
    agent_timeout: float | None = None
    # The name that the workflow gives to a gate, by its command (#223). A gate with no entry has no name: the views
    # show the short name that they find in its command.
    gate_names: dict[str, str] = field(default_factory=dict)

    def gate_name_clashes(self) -> list[tuple[str, list[str]]]:
        """The short names that more than one gate of this stack has (#223), each with the commands of those gates.

        The short name of a gate is its `name`, or the name of the tool in its command (see `output.gate_name`). A view
        that shows two gates by the same short name does not tell them apart. The result is in the order of the gates.
        """
        by_name: dict[str, list[str]] = {}
        for command in self.gates:
            by_name.setdefault(gate_name(command, self.gate_names.get(command)), []).append(command)
        return [(name, commands) for name, commands in by_name.items() if len(commands) > 1]

    @property
    def all_hotspots(self) -> list[str]:
        """The built-in hotspots, then the hotspots that the workflow names for this stack, each one once."""
        return [*BUILT_IN_HOTSPOTS, *(pattern for pattern in self.hotspots if pattern not in BUILT_IN_HOTSPOTS)]


@dataclass(frozen=True)
class Ticket:
    id: str
    text: str
    stack: str
    blocked_by: list[str]
    # The tier of the specialist of this ticket, of its continuations and of its fix-ups (#231). None: the tier of the role.
    tier: str | None = None


@dataclass(frozen=True)
class Workflow:
    base_branch: str
    run_branch: str
    mode: str
    adapter: str
    stacks: dict[str, Stack]
    tickets: list[Ticket]
    tier_overrides: dict[str, str]
    ticket_start: str = START_BASE_BRANCH
    keep_worktrees: bool = False


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


def _tier_name_problem(field: str, value: str, tiers: Tiers) -> str | None:
    """The message when `value` is not a tier name of the table, or None.

    A model identifier is a different thing, and the message says so: the
    workflow names strengths, never models.
    """
    if value in tiers.tiers:
        return None
    models = {model for columns in tiers.tiers.values() for model in columns.values()}
    models.update(model for allowed in tiers.allowed_models.values() for model in allowed)
    kind = "a model identifier, not a tier name" if value in models or "/" in value else "not a tier name"
    return f"{field}: {value!r} is {kind}; known tiers: {', '.join(sorted(tiers.tiers))}"


def _tier_override_problems(overrides: dict[str, str], tiers: Tiers) -> list[str]:
    """One message for each override whose role or value is not valid. A value must be a tier name of the table."""
    problems: list[str] = []
    for role, value in overrides.items():
        field = f"tier-overrides.{role}"
        name_problem = _tier_name_problem(field, value, tiers)
        if role not in ROLES:
            problems.append(f"{field}: unknown role {role!r}; known roles: {', '.join(ROLES)}")
        elif name_problem is not None:
            problems.append(name_problem)
        elif role == "verifier":
            problems.extend(_verifier_floor_problems(field, value))
    return problems


def _ticket_tier_order_problems(mode: str, overrides: dict[str, str], tickets: list[Ticket], tiers: Tiers) -> list[str]:
    """In `economy` mode, one message for each ticket tier, and for the tier of the specialist role, with no rank.

    The fix-up of the chain verification uses the strongest specialist tier of
    the tickets that it covers (see `strongest_tier`). A tier that is not in
    `TIER_RANK` has no place in the strength order, so the engine cannot pick
    the strongest tier. `assure` mode has no such fix-up, and needs no rank.
    A tier that is not a tier name of the table has its message already, so it gets none here.
    """
    if mode != ECONOMY or all(ticket.tier is None for ticket in tickets):
        return []
    allowed = ", ".join(sorted(TIER_RANK))
    reason = "has no place in the tier order, so the fix-up of the chain verification cannot pick the strongest tier"
    problems = [
        f"ticket {ticket.id!r}: tier: {ticket.tier!r} {reason}; allowed: {allowed}"
        for ticket in tickets if ticket.tier in tiers.tiers and ticket.tier not in TIER_RANK
    ]
    role = domain.role_tier(mode, "specialist", overrides)
    if role in tiers.tiers and role not in TIER_RANK:
        problems.append(f"tier-overrides.specialist: {role!r} {reason} of a ticket with a tier; allowed: {allowed}")
    return problems


def specialist_tier(workflow: Workflow, ticket: Ticket) -> str:
    """The tier of the specialist of `ticket`: the tier of the ticket, or the tier of the specialist role (#231)."""
    return ticket.tier if ticket.tier is not None else domain.role_tier(workflow.mode, "specialist", workflow.tier_overrides)


def strongest_tier(names: Iterable[str]) -> str:
    """The strongest of the tier names `names`, by `TIER_RANK`. On a tie, the first one wins.

    `load_workflow` refuses an `economy` workflow whose ticket tiers have no rank, so each name has one.
    """
    return max(names, key=lambda name: TIER_RANK[name])


def _verifier_floor_problems(field: str, tier: str) -> list[str]:
    """One message when the verifier override `tier` ranks below the verifier tier, or has no rank.

    The verifier tier never goes down, in any mode. A tier that is not in
    `TIER_RANK` has no place in the strength order, so the floor cannot be
    checked and the override is refused.
    """
    floor = TIER_RANK[VERIFIER_TIER_FLOOR]
    allowed = ", ".join(sorted(name for name, rank in TIER_RANK.items() if rank >= floor))
    if tier not in TIER_RANK:
        return [f"{field}: {tier!r} has no place in the tier order, so the verifier tier floor cannot be checked; allowed: {allowed}"]
    if TIER_RANK[tier] < floor:
        return [f"{field}: {tier!r} is below the verifier tier, and the verifier tier never goes down; allowed: {allowed}"]
    return []


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

    def minutes(self, table: dict[str, object], key: str, field: str) -> float | None:
        """An optional time limit in minutes: a finite number greater than 0, a fraction included. None when the field is absent or not valid."""
        if key not in table:
            return None
        value = table[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            self.problems.append(f"{field}: must be a number of minutes, not {value!r}")
            return None
        if value <= 0:
            self.problems.append(f"{field}: must be greater than 0 minutes, not {value!r}")
            return None
        return float(value)

    def gates(self, table: dict[str, object], key: str, field: str) -> tuple[list[str], dict[str, str]] | None:
        """The gates of a stack (#223): each is a command string, or a table `{ name = "...", command = "..." }`.

        Return the commands in order, and the name of each gate that has one by its command. None when the field is
        missing or has a defect: the defect is in `problems`.
        """
        if key not in table:
            self.problems.append(f"{field}: field is missing")
            return None
        value = table[key]
        if not isinstance(value, list):
            self.problems.append(f"{field}: must be a list of strings or tables, not {value!r}")
            return None
        if not value:
            self.problems.append(f"{field}: needs at least one entry")
            return None
        commands: list[str] = []
        names: dict[str, str] = {}
        problems = len(self.problems)
        for position, item in enumerate(value):
            at = f"{field}[{position}]"
            if isinstance(item, str) and item.strip():
                commands.append(item)
            elif isinstance(item, dict):
                self.refuse_unknown(item, GATE_FIELDS, at)
                name = self.string(item, "name", f"{at}.name")
                command = self.string(item, "command", f"{at}.command")
                if name is not None and command is not None:
                    commands.append(command)
                    names[command] = name
            else:
                self.problems.append(f"{field}: must be a list of strings or tables, not {item!r}")
        return None if len(self.problems) > problems else (commands, names)

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


TOP_LEVEL_FIELDS: tuple[str, ...] = (
    "base-branch", "run-branch", "mode", "adapter", "ticket-start", "keep-worktrees", "tier-overrides", "stacks", "tickets",
)
STACK_FIELDS: tuple[str, ...] = (
    "specialist", "verifier", "gates", "hotspots", "mcp-servers", "prepare",
    "specialist-notes", "specialist-notes-file", "verifier-notes", "verifier-notes-file", "gate-timeout", "agent-timeout",
)
GATE_FIELDS: tuple[str, ...] = ("name", "command")
MCP_SERVER_FIELDS: tuple[str, ...] = ("command", "url", "verifier-tools")
TICKET_FIELDS: tuple[str, ...] = ("id", "text", "text-file", "stack", "blocked-by", "tier")


def _read_stacks(reader: _Reader, value: object, root: Path) -> dict[str, Stack]:
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
        read_gates = reader.gates(stack, "gates", f"{field}.gates")
        gates, gate_names = read_gates if read_gates is not None else (None, {})
        hotspots = reader.string_list(stack, "hotspots", f"{field}.hotspots", at_least_one=False)
        servers = _read_mcp_servers(reader, stack.get("mcp-servers", {}), f"{field}.mcp-servers")
        # `prepare` is optional. When it is there, the check is the check of `gates`.
        prepare = reader.string_list(stack, "prepare", f"{field}.prepare", at_least_one=True) if "prepare" in stack else []
        gate_timeout = reader.minutes(stack, "gate-timeout", f"{field}.gate-timeout")
        agent_timeout = reader.minutes(stack, "agent-timeout", f"{field}.agent-timeout")
        problems = len(reader.problems)
        notes = {role: _read_notes(reader, stack, role, field, root) for role in ROLES}
        if None not in (specialist, verifier, gates, hotspots, servers, prepare) and len(reader.problems) == problems:
            stacks[name] = Stack(
                name, specialist, verifier, gates, hotspots, servers, tuple(prepare),  # type: ignore[arg-type]
                specialist_notes=notes["specialist"], verifier_notes=notes["verifier"],
                gate_timeout=gate_timeout, agent_timeout=agent_timeout, gate_names=gate_names,
            )
    return stacks


def _read_notes(reader: _Reader, stack: dict[str, object], role: str, field: str, root: Path) -> str | None:
    """The notes of `role` in a stack: the inline `<role>-notes`, the content of the file `<role>-notes-file`, or None.

    Both fields are optional, and a stack gives at most one of them. The caller
    sees a defect as a new entry in `reader.problems`.
    """
    inline, file = f"{role}-notes", f"{role}-notes-file"
    if inline in stack and file in stack:
        reader.problems.append(f"{field}: give at most one of {inline} and {file}")
        return None
    if inline in stack:
        return reader.string(stack, inline, f"{field}.{inline}")
    if file not in stack:
        return None
    name = reader.string(stack, file, f"{field}.{file}")
    if name is None:
        return None
    text = _read_file(reader, root, name, f"{field}: {file}")
    if text is not None and not text.strip():
        reader.problems.append(f"{field}: {file} {name!r} is empty")
        return None
    return text


def _read_file(reader: _Reader, root: Path, name: str, where: str) -> str | None:
    """The content of the file `name`, relative to `root`. A file that cannot be read is a problem that starts with `where`."""
    try:
        return (root / name).read_text()
    except FileNotFoundError:
        reader.problems.append(f"{where} {name!r} not found (looked in {root})")
    except OSError as error:
        reader.problems.append(f"{where} {name!r} cannot be read: {error}")
    return None


def _read_mcp_servers(reader: _Reader, value: object, field: str) -> tuple[McpServer, ...] | None:
    """The MCP servers of a stack: a table of server tables, each with exactly one of `command` and `url`.

    A server table can also hold `verifier-tools` (#257): at least one exact tool name, with no `*`.
    """
    table = reader.table(value, field)
    if table is None:
        return None
    servers: list[McpServer] = []
    problems = len(reader.problems)
    for name, raw in table.items():
        server_field = f"{field}.{name}"
        server = reader.table(raw, server_field)
        if server is None:
            continue
        reader.refuse_unknown(server, MCP_SERVER_FIELDS, server_field)
        tools = _read_verifier_tools(reader, server, f"{server_field}.verifier-tools")
        if ("command" in server) == ("url" in server):
            reader.problems.append(f"{server_field}: needs exactly one of command and url")
        elif "command" in server:
            command = reader.string_list(server, "command", f"{server_field}.command", at_least_one=True)
            if command is not None:
                servers.append(McpServer(name, command=tuple(command), verifier_tools=tools))
        else:
            url = reader.string(server, "url", f"{server_field}.url")
            if url is not None:
                servers.append(McpServer(name, url=url, verifier_tools=tools))
    return tuple(servers) if len(reader.problems) == problems else None


def _read_verifier_tools(reader: _Reader, server: dict[str, object], field: str) -> tuple[str, ...]:
    """The `verifier-tools` of a server table, or () when it has none. A defect is a new entry in `reader.problems`.

    Each name is one exact tool name, so an empty name and a name with `*` are defects: no measurement shows how a
    harness reads `*` in the tools of an agent. `string_list` refuses an empty name.
    """
    if "verifier-tools" not in server:
        return ()
    names = reader.string_list(server, "verifier-tools", field, at_least_one=True)
    if names is None:
        return ()
    for name in names:
        if "*" in name:
            reader.problems.append(f"{field}: the tool name {name!r} holds '*'; give each tool by its exact name")
    return tuple(dict.fromkeys(names))


def _read_tickets(reader: _Reader, value: object, root: Path, tiers: Tiers) -> list[Ticket]:
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
            id_problem = _ticket_id_problem(ticket_id)
            if id_problem is not None:
                reader.problems.append(f"{label}: id {id_problem}")
        text = _read_ticket_text(reader, raw, label, root)
        stack = reader.string(raw, "stack", f"{label}: stack")
        blocked_by = reader.string_list(raw, "blocked-by", f"{label}: blocked-by", at_least_one=False)
        # A bad tier is a problem of its own: the ticket stays, so a ticket that it blocks gets no second message.
        tier = reader.string(raw, "tier", f"{label}: tier") if "tier" in raw else None
        name_problem = _tier_name_problem(f"{label}: tier", tier, tiers) if tier is not None else None
        if name_problem is not None:
            reader.problems.append(name_problem)
        if None not in (ticket_id, text, stack, blocked_by):
            tickets.append(Ticket(ticket_id, text, stack, blocked_by, tier))  # type: ignore[arg-type]
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
    return _read_file(reader, root, name, f"{label}: text-file")


def load_workflow(path: Path, tiers: Tiers, registered_adapters: Iterable[str] = ()) -> Workflow:
    """Read and check a workflow file. Raise WorkflowError with every defect found.

    `text-file` paths and the paths of the notes files of a stack are relative to the directory of the workflow file.
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
    for field, branch in (("base-branch", base_branch), ("run-branch", run_branch)):
        problem = ref_name_problem(branch) if branch is not None else None
        if problem is not None:
            reader.problems.append(f"{field}: {branch!r} is not a branch name that git takes: it {problem}")
    if mode is not None and mode not in MODES:
        reader.problems.append(f"mode: {mode!r} is not a mode; known modes: {', '.join(MODES)}")
    # The default is `run-branch` in `assure` mode, so each ticket runs its gates once (#244). `economy` mode has a chain.
    default_start = START_RUN_BRANCH if mode == ASSURE else START_BASE_BRANCH
    ticket_start = reader.string(data, "ticket-start", "ticket-start") if "ticket-start" in data else default_start
    if ticket_start is not None and ticket_start not in TICKET_STARTS:
        reader.problems.append(
            f"ticket-start: {ticket_start!r} is not a ticket start; known values: {', '.join(TICKET_STARTS)}"
        )
    elif ticket_start == START_RUN_BRANCH and mode == ECONOMY:
        reader.problems.append(
            f"ticket-start: {ticket_start!r} works only in assure mode; in economy mode each ticket starts from the previous ticket"
        )
    keep_worktrees = data.get("keep-worktrees", False)
    if not isinstance(keep_worktrees, bool):
        reader.problems.append(f"keep-worktrees: must be true or false, not {keep_worktrees!r}")
    known_adapters = sorted({*ADAPTERS, *registered_adapters})
    if adapter is not None and adapter not in known_adapters:
        reader.problems.append(f"adapter: {adapter!r} is not an adapter; known adapters: {', '.join(known_adapters)}")

    overrides_table = reader.table(data.get("tier-overrides", {}), "tier-overrides") or {}
    overrides = {role: value for role, value in overrides_table.items() if isinstance(value, str)}
    for role in overrides_table.keys() - overrides.keys():
        reader.problems.append(f"tier-overrides.{role}: must be a string, not {overrides_table[role]!r}")
    reader.problems.extend(_tier_override_problems(overrides, tiers))

    stacks = _read_stacks(reader, data.get("stacks"), Path(path).parent)
    tickets = _read_tickets(reader, data.get("tickets"), Path(path).parent, tiers)
    if mode in MODES:
        reader.problems.extend(_ticket_tier_order_problems(mode, overrides, tickets, tiers))
    known_ids = {t.id for t in tickets}
    run_branch_valid = run_branch is not None and ref_name_problem(run_branch) is None
    for ticket in tickets:
        # The ticket branch is `<run-branch>-<id>` (the engine names it so). A bad id or a bad run branch is reported above.
        branch = f"{run_branch}-{ticket.id}"
        branch_problem = ref_name_problem(branch) if run_branch_valid and _ticket_id_problem(ticket.id) is None else None
        if branch_problem is not None:
            reader.problems.append(f"ticket {ticket.id!r}: the ticket branch {branch!r} is not a branch name that git takes: it {branch_problem}")
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
    return Workflow(
        base_branch, run_branch, mode, adapter, stacks, tickets, overrides, ticket_start, keep_worktrees,  # type: ignore[arg-type]
    )


def plan_order(workflow: Workflow) -> list[Ticket]:
    """The tickets so that each one follows every ticket that blocks it.

    Among tickets that are ready at the same time, the order of the file wins.
    Raise ValueError on a cycle; `load_workflow` already refuses one.
    """
    by_id = {ticket.id: ticket for ticket in workflow.tickets}
    return [by_id[ticket_id] for ticket_id in domain.dependency_order([(t.id, t.blocked_by) for t in workflow.tickets])]
