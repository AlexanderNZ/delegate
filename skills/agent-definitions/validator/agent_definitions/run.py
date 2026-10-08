"""`delegate run`: check a workflow file and print its plan, or build its tickets.

`--dry-run` validates the file and prints the plan in dependency order. It
creates no branch, no worktree and no journal. Without it, the engine builds
each ticket through the workflow's harness adapter.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from .engine import EngineError, run_workflow
from .tiers import Tiers, load_tiers
from .workflow import WorkflowError, load_workflow, plan_order


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delegate run", description="Check a workflow file and print its plan.")
    parser.add_argument("workflow", type=Path, help="path to the workflow TOML file")
    parser.add_argument("--dry-run", action="store_true", help="validate the workflow and print the plan; create nothing")
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository to build in; default is the current directory")
    parser.add_argument("--tiers", type=Path, help="path to a tiers.toml; default is the bundled table")
    return parser


class TierFileError(Exception):
    """The tier file cannot be used. The message names the file."""


def _load_tiers(path: Path | None) -> Tiers:
    """The tier table. Raise TierFileError, which names the file, when it cannot be used."""
    try:
        return load_tiers(path)
    except FileNotFoundError:
        raise TierFileError(f"tier file {path} not found") from None
    except (OSError, tomllib.TOMLDecodeError, KeyError) as error:
        raise TierFileError(f"tier file {path} is not usable: {error!r}") from None


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        tiers = _load_tiers(args.tiers)
        workflow = load_workflow(args.workflow, tiers)
    except TierFileError as error:
        print(f"delegate run: {error}", file=sys.stderr)
        return 1
    except WorkflowError as error:
        for problem in error.problems:
            print(f"delegate run: {args.workflow}: {problem}", file=sys.stderr)
        return 1
    if not args.dry_run:
        try:
            result = run_workflow(workflow, args.workflow, args.repo, tiers)
        except EngineError as error:
            print(f"delegate run: {error}", file=sys.stderr)
            return 1
        print(f"run {result.run_id}")
        print(f"journal {result.journal}")
        for ticket, reason in result.failures.items():
            print(f"delegate run: ticket {ticket}: {reason}", file=sys.stderr)
        for ticket, reason in result.skipped.items():
            print(f"delegate run: ticket {ticket}: skipped: {reason}", file=sys.stderr)
        return 0 if result.ok else 1
    print(f"workflow {args.workflow}: mode {workflow.mode}, adapter {workflow.adapter}")
    print(f"base branch {workflow.base_branch}, run branch {workflow.run_branch}")
    for number, ticket in enumerate(plan_order(workflow), start=1):
        blocked = ", ".join(ticket.blocked_by) or "none"
        print(f"ticket {ticket.id} ({number}/{len(workflow.tickets)}): stack {ticket.stack}, blocked by {blocked}")
    for role, tier in workflow.tier_overrides.items():
        print(f"tier override {role}: {tier}")
    return 0
