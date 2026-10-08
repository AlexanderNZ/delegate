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
from .adapters import AdapterRequest
from .journal import Journal
from .tiers import Tiers
from .workflow import Ticket, Workflow, plan_order

# The tier of the specialist role in each mode, before a workflow override.
SPECIALIST_TIER: dict[str, str] = {"assure": "strong", "economy": "standard"}

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
    _git(repo, "rev-parse", "--verify", f"refs/heads/{workflow.base_branch}^{{commit}}")
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
    for ticket in order:
        stack = workflow.stacks[ticket.stack]
        branch = f"{workflow.run_branch}-{ticket.id}"
        worktree = state_dir / "worktrees" / run_id / ticket.id
        report_path = state_dir / "runs" / run_id / "reports" / f"{ticket.id}.specialist.json"
        base_commit = _git(repo, "rev-parse", workflow.base_branch)
        _git(repo, "worktree", "add", "-q", "-b", branch, str(worktree), workflow.base_branch)
        journal.append(
            "step-start", ticket=ticket.id, stack=ticket.stack, branch=branch, worktree=str(worktree),
            base_commit=base_commit, agent=stack.specialist, tier=tier, model=model,
        )
        result = adapter.run(AdapterRequest(stack.specialist, model, ticket.text, worktree, report_path))
        journal.append(
            "adapter-result", ticket=ticket.id, exit_status=result.exit_status, end_state=result.end_state,
            session_id=result.session_id, event_stream=str(result.event_stream),
        )
        journal.append("step-end", ticket=ticket.id, state=BUILT, reason=None)
        built.append(ticket.id)
    journal.append("run-end", result=BUILT if not failed else FAILED, built=built, failed=failed)
    return RunResult(run_id, journal.path, built, failed)
