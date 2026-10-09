"""`delegate run`: check a workflow file and print its plan, or build its tickets.

`--dry-run` validates the file and prints the plan in dependency order. It
creates no branch, no worktree and no journal. Without it, the engine builds
each ticket through the workflow's harness adapter. `--resume <run-id>` goes on
with a run that stopped, from its journal.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from . import adapters
from .adapters.git import GitVersionControl
from .cli import add_opencode_override_flags, apply_opencode_override
from .engine import EngineError, run_workflow, workflow_of_run
from .tiers import Tiers, load_tiers
from .workflow import WorkflowError, load_workflow, plan_order


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delegate run", description="Build the tickets of a workflow file, or check it and print its plan.")
    parser.add_argument(
        "workflow", type=Path, nargs="?",
        help="path to the workflow TOML file; with --resume the default is the file that the run started from",
    )
    parser.add_argument("--dry-run", action="store_true", help="validate the workflow and print the plan; create nothing")
    parser.add_argument("--resume", metavar="RUN_ID", help="go on with the run RUN_ID from its journal; build the steps that are not complete; it cannot go with --dry-run")
    parser.add_argument(
        "--break-lock", action="store_true",
        help="remove the lock of the run branch when its process no longer exists; a lock whose process is alive stays",
    )
    parser.add_argument("--repo", type=Path, default=Path("."), help="the git repository to build in; default is the current directory")
    parser.add_argument("--tiers", type=Path, help="path to a tiers.toml; default is the bundled table")
    add_opencode_override_flags(parser)
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


def _override_tiers(tiers: Tiers, args: argparse.Namespace) -> Tiers:
    """The tier table with the OpenCode override of the command line applied.

    A model that the override names must be in the allowed set, which
    `--opencode-allow` extends. A run has no validator step, so this check is
    the one that stops a model no provider serves (ADR 0003).
    """
    try:
        overridden = apply_opencode_override(tiers, args)
    except ValueError as error:
        raise TierFileError(str(error)) from None
    for tier, model in args.opencode_model or []:
        if model not in overridden.allowed_models["opencode"]:
            raise TierFileError(
                f"--opencode-model {tier}={model}: {model!r} is not an allowed OpenCode model; name it with --opencode-allow {model}"
            )
    return overridden


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # The composition point also makes the version-control backend that the engine drives.
    vcs = GitVersionControl()
    if args.workflow is None and args.resume is None:
        parser.error("a workflow file or --resume RUN_ID is required")
    if args.resume is not None and args.dry_run:
        parser.error("--resume and --dry-run cannot go together")
    if args.workflow is None:
        try:
            args.workflow = workflow_of_run(args.repo, args.resume, vcs)
        except EngineError as error:
            print(f"delegate run: {error}", file=sys.stderr)
            return 1
    try:
        tiers = _override_tiers(_load_tiers(args.tiers), args)
        workflow = load_workflow(args.workflow, tiers, adapters.registered_names())
    except TierFileError as error:
        print(f"delegate run: {error}", file=sys.stderr)
        return 1
    except WorkflowError as error:
        for problem in error.problems:
            print(f"delegate run: {args.workflow}: {problem}", file=sys.stderr)
        return 1
    if not args.dry_run:
        # The composition point: the workflow names the adapter, and this driver picks it for the engine.
        try:
            adapter = adapters.get(workflow.adapter)
        except KeyError:
            registered = ", ".join(adapters.registered_names()) or "none"
            print(f"delegate run: adapter {workflow.adapter!r} has no implementation yet; registered: {registered}", file=sys.stderr)
            return 1
        try:
            result = run_workflow(workflow, args.workflow, args.repo, tiers, adapter, vcs, resume=args.resume, break_lock=args.break_lock)
        except EngineError as error:
            print(f"delegate run: {error}", file=sys.stderr)
            return 1
        print(f"run {result.run_id}")
        print(f"journal {result.journal}")
        for ticket, reason in result.failures.items():
            print(f"delegate run: ticket {ticket}: {reason}", file=sys.stderr)
        for finding in result.unmapped:
            print(f"delegate run: unmapped finding: {finding}", file=sys.stderr)
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
