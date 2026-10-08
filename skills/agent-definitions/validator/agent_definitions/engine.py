"""The workflow engine: build each ticket through a harness adapter.

For each ticket the engine makes a worktree from the base branch, spawns the
stack's specialist through the adapter, and records each event in the journal.
Later steps (report validation, the gates) are added in this module.

Standard library and git through subprocess only.
"""

from __future__ import annotations

import secrets
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import adapters
from .adapters import Adapter, AdapterRequest
from .brief import specialist_brief
from .journal import Journal
from .reports import ReportError, read_specialist_report
from .tiers import Tiers
from .workflow import Ticket, Workflow, plan_order

# The tier of the specialist role in each mode, before a workflow override.
SPECIALIST_TIER: dict[str, str] = {"assure": "strong", "economy": "standard"}

# The journal keeps this many characters of the end of a gate's output.
GATE_OUTPUT_TAIL_CHARS: int = 4000

BUILT: str = "built"
FAILED: str = "failed"


class EngineError(Exception):
    """The run cannot start or go on. The message names the input at fault."""


@dataclass(frozen=True)
class RunResult:
    run_id: str
    journal: Path
    built: list[str]
    failed: list[str]
    failures: dict[str, str]

    @property
    def ok(self) -> bool:
        return not self.failed


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise EngineError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.returncode}")
    return r.stdout.strip()


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)


def _specialist_model(workflow: Workflow, tiers: Tiers, column: str) -> tuple[str, str]:
    """The tier of the specialist and its model from the adapter's own tier column."""
    tier = workflow.tier_overrides.get("specialist", SPECIALIST_TIER[workflow.mode])
    try:
        return tier, tiers.model_for(tier, column)
    except KeyError:
        raise EngineError(f"the tier table has no {column!r} column for tier {tier!r}") from None


def _branch_exists(repo: Path, name: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{name}"], capture_output=True
    ).returncode == 0


def _check_branches(workflow: Workflow, repo: Path) -> None:
    """Refuse a run whose base branch is missing or whose ticket branch is taken. Create nothing."""
    if not _branch_exists(repo, workflow.base_branch):
        raise EngineError(f"base-branch {workflow.base_branch!r} is not a branch of the repository {repo}")
    for ticket in workflow.tickets:
        branch = _ticket_branch(workflow, ticket)
        if _branch_exists(repo, branch):
            raise EngineError(f"branch {branch!r} for ticket {ticket.id!r} exists already; delete it or choose another run-branch")


def _ticket_branch(workflow: Workflow, ticket: Ticket) -> str:
    return f"{workflow.run_branch}-{ticket.id}"


def run_workflow(workflow: Workflow, workflow_path: Path, repo: Path, tiers: Tiers) -> RunResult:
    """Build the tickets of a workflow in dependency order. Return the result of the run."""
    try:
        adapter = adapters.get(workflow.adapter)
    except KeyError:
        raise EngineError(
            f"adapter {workflow.adapter!r} has no implementation yet; registered: {', '.join(adapters.registered_names()) or 'none'}"
        ) from None
    repo = Path(_git(repo, "rev-parse", "--show-toplevel"))
    state_dir = (repo / _git(repo, "rev-parse", "--git-common-dir")).resolve() / "delegate"
    _check_branches(workflow, repo)
    tier, model = _specialist_model(workflow, tiers, adapter.tier_column)

    run_id = _new_run_id()
    journal = Journal(state_dir / "runs" / run_id / "journal.jsonl")
    order = plan_order(workflow)
    journal.append(
        "run-start", run_id=run_id, workflow=str(workflow_path), mode=workflow.mode, adapter=workflow.adapter,
        base_branch=workflow.base_branch, run_branch=workflow.run_branch, tickets=[t.id for t in order],
    )
    built: list[str] = []
    failed: list[str] = []
    failures: dict[str, str] = {}
    for ticket in order:
        reason = _build_ticket(workflow, ticket, adapter, model, tier, repo, state_dir, run_id, journal)
        journal.append("step-end", ticket=ticket.id, state=FAILED if reason else BUILT, reason=reason)
        if reason:
            failed.append(ticket.id)
            failures[ticket.id] = reason
            break
        built.append(ticket.id)
    journal.append("run-end", result=BUILT if not failed else FAILED, built=built, failed=failed)
    return RunResult(run_id, journal.path, built, failed, failures)


def _build_ticket(
    workflow: Workflow, ticket: Ticket, adapter: Adapter, model: str, tier: str,
    repo: Path, state_dir: Path, run_id: str, journal: Journal,
) -> str | None:
    """Build one ticket. Return None when it is built, or the reason it failed."""
    stack = workflow.stacks[ticket.stack]
    branch = _ticket_branch(workflow, ticket)
    worktree = state_dir / "worktrees" / run_id / ticket.id
    report_path = state_dir / "runs" / run_id / "reports" / f"{ticket.id}.specialist.json"
    base_commit = _git(repo, "rev-parse", workflow.base_branch)
    _git(repo, "worktree", "add", "-q", "-b", branch, str(worktree), workflow.base_branch)
    journal.append(
        "step-start", ticket=ticket.id, stack=ticket.stack, branch=branch, worktree=str(worktree),
        base_commit=base_commit, agent=stack.specialist, tier=tier, model=model,
    )
    result = adapter.run(AdapterRequest(
            stack.specialist, model,
            specialist_brief(ticket.id, ticket.text, worktree, stack.hotspots, stack.gates, report_path),
            worktree, report_path,
        ))
    journal.append(
        "adapter-result", ticket=ticket.id, exit_status=result.exit_status, end_state=result.end_state,
        session_id=result.session_id, event_stream=str(result.event_stream),
    )
    if result.end_state != adapters.FINISHED or result.exit_status != 0:
        return f"the specialist ended {result.end_state} with exit status {result.exit_status}"
    try:
        report = read_specialist_report(report_path, ticket.id)
    except ReportError as error:
        journal.append("report-validation", ticket=ticket.id, valid=False, path=str(report_path), reason=str(error))
        return str(error)
    journal.append("report-validation", ticket=ticket.id, valid=True, path=str(report_path), reason=None)
    if report.status != "committed":
        detail = f": {report.blocked_reason}" if report.blocked_reason else ""
        return f"the specialist reported status {report.status!r}{detail}"
    if _git(repo, "rev-list", "--count", f"{workflow.base_branch}..{branch}") == "0":
        return f"branch {branch} holds no commit beyond {workflow.base_branch}, though the report says committed"
    red = [gate for gate in stack.gates if not _run_gate(gate, ticket.id, worktree, journal)]
    if red:
        return "gates red: " + "; ".join(red)
    return None


def _run_gate(command: str, ticket: str, worktree: Path, journal: Journal) -> bool:
    """Run one gate command in the worktree, record the result, and return True when it is green."""
    r = subprocess.run(["bash", "-c", command], cwd=worktree, capture_output=True, text=True)
    output = (r.stdout + r.stderr)[-GATE_OUTPUT_TAIL_CHARS:]
    journal.append(
        "gate-result", ticket=ticket, command=command, exit_status=r.returncode, green=r.returncode == 0,
        output_tail=output,
    )
    return r.returncode == 0
