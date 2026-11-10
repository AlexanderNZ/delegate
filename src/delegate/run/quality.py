"""The quality check of a workflow (#247).

A coordinator that copies an old workflow file does not see a new optional field in a reference. It reads the output of
`delegate run --dry-run` before each launch. So the dry run prints this check after the plan: for each stack or for the
workflow, one line with `ok` and the setting that the workflow uses, or `warn`, the field to set and the effect of a
missing setting on the quality or the time of the run. A `warn` does not refuse the workflow.

The check `verifier-tier` has no `warn`: `load_workflow` refuses a verifier override below the verifier tier (#14), so
a workflow that loads always has a verifier on the tier `strong` or `verifier`. Its `ok` line names that tier.

A later change that adds an optional field that changes the quality or the time of a run adds its check here, and a
row in the table of checks of the run reference.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain import ASSURE, DEFAULT_VERIFIER_DIFF_LIMIT, START_BASE_BRANCH, START_RUN_BRANCH, role_tier
from .output import gate_name
from .workflow import Stack, Workflow

OK = "ok"
WARN = "warn"


@dataclass(frozen=True)
class Check:
    """The result of one check: `ok` or `warn`, the id of the check, and the reason."""

    status: str
    id: str
    reason: str

    @property
    def warns(self) -> bool:
        return self.status == WARN

    def line(self) -> str:
        return f"{self.status} {self.id}: {self.reason}"


def quality_checks(workflow: Workflow) -> list[Check]:
    """The checks of `workflow`, in the order of the table of checks: the checks of a stack give one line for each stack.

    The check `verifier-tools` gives one line for each MCP server of a stack (#257), and no line for a stack with no server.
    """
    stacks = list(workflow.stacks.values())
    return [
        *(_gate_timeout(stack) for stack in stacks),
        *(_timeout("agent-timeout", stack.name, stack.agent_timeout, "an agent") for stack in stacks),
        _ticket_start(workflow),
        *(_hotspots(stack) for stack in stacks),
        Check(OK, "verifier-tier", f"the verifier runs on the tier {role_tier(workflow.mode, 'verifier', workflow.tier_overrides)}"),
        *(_gate_names(stack) for stack in stacks),
        *(_verifier_diff_limit(stack) for stack in stacks),
        *(check for stack in stacks for check in _verifier_tools(stack)),
    ]


def _gate_timeout(stack: Stack) -> Check:
    """A gate always has a limit (#275): the `gate-timeout` of the stack, or the default."""
    if stack.gate_timeout is None:
        return Check(OK, "gate-timeout", f"stack {stack.name}: gate-timeout = {stack.gate_limit:g} minutes (the default)")
    return _timeout("gate-timeout", stack.name, stack.gate_timeout, "a gate")


def _timeout(check_id: str, stack: str, minutes: float | None, what: str) -> Check:
    if minutes is None:
        return Check(WARN, check_id, f"stack {stack}: set {check_id}; without it, {what} that hangs stops the run with no limit")
    return Check(OK, check_id, f"stack {stack}: {check_id} = {minutes:g} minutes")


def _ticket_start(workflow: Workflow) -> Check:
    if workflow.mode != ASSURE:
        return Check(OK, "ticket-start", f"{workflow.mode} mode: each ticket starts from the previous ticket")
    if workflow.ticket_start == START_RUN_BRANCH:
        return Check(OK, "ticket-start", f'ticket-start = "{START_RUN_BRANCH}": each ticket starts from the run branch')
    unblocked = sum(1 for ticket in workflow.tickets if not ticket.blocked_by)
    setting = f'ticket-start = "{START_BASE_BRANCH}"'
    if unblocked < 2:
        return Check(OK, "ticket-start", f"{setting}, and fewer than two tickets have no blocked-by")
    return Check(
        WARN, "ticket-start",
        f'{setting}, and {unblocked} tickets have no blocked-by; set ticket-start = "{START_RUN_BRANCH}"; without it, each '
        "such ticket after the first runs its gates again after the rebase, which doubles the gate time and the chance "
        "of a flaky failure",
    )


def _hotspots(stack: Stack) -> Check:
    if not stack.hotspots:
        return Check(
            WARN, "hotspots",
            f"stack {stack.name}: hotspots = []; set hotspots; without them, only the built-in agent directories are "
            "guarded, and a specialist can change the CI and the configuration",
        )
    return Check(OK, "hotspots", f"stack {stack.name}: hotspots {', '.join(stack.hotspots)}")


def _gate_names(stack: Stack) -> Check:
    clashes = stack.gate_name_clashes()
    if not clashes:
        names = ", ".join(gate_name(command, stack.gate_names.get(command)) for command in stack.gates)
        return Check(OK, "gate-names", f"stack {stack.name}: each gate has its own short name: {names}")
    # The text of the warning before #247, with the effect at the end.
    same = "; ".join(
        f"the gates {', '.join(f'`{command}`' for command in commands)} have the same short name {name!r}" for name, commands in clashes
    )
    return Check(
        WARN, "gate-names",
        f'stack {stack.name}: {same}; give each one a name: {{ name = "<name>", command = "<command>" }}; '
        "without it, the views cannot tell the gates apart",
    )


def _verifier_diff_limit(stack: Stack) -> Check:
    """The size of the diff that the verifier brief of the stack holds inline (#292). Always `ok`: the field has a default."""
    limit = stack.verifier_diff_limit
    suffix = " (the default)" if limit == DEFAULT_VERIFIER_DIFF_LIMIT else ""
    return Check(OK, "verifier-diff-limit", f"stack {stack.name}: verifier-diff-limit = {limit} characters{suffix}")


def _verifier_tools(stack: Stack) -> list[Check]:
    """One check for each MCP server of the stack: `warn` for a server with no `verifier-tools` (#257).

    The verifier of a harness with a list of tools gets only the MCP tools that the list names, so it gets no tool of
    such a server.
    """
    return [
        Check(OK, "verifier-tools", f"stack {stack.name}: server {server.name}: verifier-tools {', '.join(server.verifier_tools)}")
        if server.verifier_tools else
        Check(
            WARN, "verifier-tools",
            f"stack {stack.name}: server {server.name} has no verifier-tools; set verifier-tools; without it, the verifier "
            "of the stack gets no tool of that server",
        )
        for server in stack.mcp_servers
    ]
