"""`delegate run`: check a workflow file and print its plan.

Only `--dry-run` exists today. It validates the file and prints the plan in
dependency order. It creates no branch, no worktree and no journal.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .tiers import load_tiers
from .workflow import WorkflowError, load_workflow, plan_order


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delegate run", description="Check a workflow file and print its plan.")
    parser.add_argument("workflow", type=Path, help="path to the workflow TOML file")
    parser.add_argument("--dry-run", action="store_true", help="validate the workflow and print the plan; create nothing")
    parser.add_argument("--tiers", type=Path, help="path to a tiers.toml; default is the bundled table")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        workflow = load_workflow(args.workflow, load_tiers(args.tiers))
    except WorkflowError as error:
        for problem in error.problems:
            print(f"delegate run: {args.workflow}: {problem}", file=sys.stderr)
        return 1
    print(f"workflow {args.workflow}: mode {workflow.mode}, adapter {workflow.adapter}")
    print(f"base branch {workflow.base_branch}, run branch {workflow.run_branch}")
    for number, ticket in enumerate(plan_order(workflow), start=1):
        blocked = ", ".join(ticket.blocked_by) or "none"
        print(f"ticket {ticket.id} ({number}/{len(workflow.tickets)}): stack {ticket.stack}, blocked by {blocked}")
    for role, tier in workflow.tier_overrides.items():
        print(f"tier override {role}: {tier}")
    return 0
