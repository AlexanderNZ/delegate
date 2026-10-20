"""The `delegate run` command: check a workflow file and print its plan, or build its tickets.

`--dry-run` validates the file and prints the plan in dependency order. It
creates no branch, no worktree and no journal. Without it, the engine builds
each ticket through the workflow's harness adapter. `--resume <run-id>` goes on
with a run that stopped or paused, from its journal. `--retry <ticket>` with it goes on
with one failed step of that run, also when the run ended. A run that `delegate
pause` stopped at a step boundary exits with `EXIT_PAUSED`.

A resume with a workflow whose tickets differ from the newest plan of the run is
a plan change. `--reviewed <ids>` names the later tickets that the coordinator
reviewed. `--resume <run-id> --dry-run` prints the old order, the new order and
the verdict of each rule of a plan change, and changes nothing.

When the run holds its lock and its journal records the start, the command prints the start lines of frame 1k of the
design (#119): `run <id>`, `journal <path>`, and the line that tells how to follow and pause the run. When the run ends
or pauses, it prints the per-ticket lines on stderr, then the summary line `run <result> · <used> · ...` from the
snapshot of the journal, and one retry line for each failed step. On a terminal the result has the colour of its role.

Ctrl-C stops a run in the foreground at once. The engine releases the lock and records `interrupted` in the
journal. The command prints one line on stderr that tells how to resume, and exits `EXIT_INTERRUPTED` (130). The
start lines are on stdout already. `delegate pause` stays the way to stop a run at a step boundary.

`--detach` starts the run in a new process with the setsid system call, with its
output in the log file of the run, then prints the run id and the log path and
exits 0. A harness that stops its tool shell, or a background task at a time
limit, then does not stop the run. Before it starts the process, it makes the
checks of the run that need no agent (`engine.check_start`): the workflow, the
tiers, the lock, for a new run the base branch and a free run branch, and for a
resume the rules of a plan change with `--reviewed`. A check that fails prints
its error on stderr, exits 1, and starts no process, as a run in the foreground
does.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

from .. import adapters
from ..adapters.git import GitVersionControl
from ..adapters.shell import ShellGateRunner
from ..run import status
from ..run.domain import START_RUN_BRANCH
from ..run.domain import PLAN_RULES
from ..run.engine import (
    EngineError, PlanReview, RunInterrupted, check_start, new_run_id, review_plan_change, run_workflow, state_directory,
    workflow_of_run,
)
from ..run.output import glyphs, short_run_id, styled
from ..run.runs import LOG_NAME, RunsError, journal_of, run_id_of
from ..run.watch import EXIT_INTERRUPTED
from ..run.workflow import WorkflowError, load_workflow, plan_order
from ..shared.tiers import Tiers, load_tiers
from .agent_definitions import add_opencode_override_flags, apply_opencode_override
from .status import stream_reader_of, utc_now
from .title import SEPARATOR, Title

# The exit status of a run that a pause request stopped at a step boundary (see `delegate pause`).
EXIT_PAUSED: int = 3

# The paint role of the result of a run in its summary line (tokens.md): the result word is bold.
RESULT_ROLES: dict[str, str] = {status.BUILT: "ok bold", status.FAILED: "bad bold", status.PAUSED: "warn bold"}

# The program of a detached run process: it imports the package from the import path of the command that started it
# (argv[1]), then runs `delegate` with the rest of its arguments. A Nix wrapper puts the package on the import path of
# its own process only, so `python -m delegate...` can fail to find it. The path goes as an argument, not in the
# environment, so the agents and the gates of the run do not get it.
DETACHED_PROGRAM: str = (
    "import json, sys; sys.path[:0] = json.loads(sys.argv[1]); "
    "from delegate.cli.delegate import main; sys.exit(main(sys.argv[2:]))"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delegate run", description="Build the tickets of a workflow file, or check it and print its plan.")
    parser.add_argument(
        "workflow", type=Path, nargs="?",
        help="path to the workflow TOML file; with --resume the default is the file that the run started from",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate the workflow and print the plan; with --resume, print the old order, the new order and the verdict of "
        "each rule of a plan change; create nothing",
    )
    parser.add_argument(
        "--resume", metavar="RUN_ID",
        help="go on with the run RUN_ID from its journal; build the steps that are not complete; a workflow whose tickets "
        "differ from the newest plan of the run is a plan change",
    )
    parser.add_argument(
        "--reviewed", metavar="IDS", action="append",
        help="with --resume, the later tickets of a plan change that you reviewed against the added and changed tickets: "
        "ticket ids separated by commas, or all",
    )
    parser.add_argument(
        "--retry", metavar="TICKET",
        help="with --resume, go on with the failed step of TICKET, also in a run that ended; "
        "use its worktree and its commits again, and go on at the first phase that did not complete",
    )
    parser.add_argument(
        "--break-lock", action="store_true",
        help="remove the lock of the run branch when its process no longer exists; a lock whose process is alive stays",
    )
    parser.add_argument(
        "--detach", action="store_true",
        help="make the checks of the run that need no agent, and exit 1 with the error when one fails; then start the run in a "
        "new process with setsid, outside the process group of the shell; write its output to the file run.log in the directory "
        "of the run; print the run id and the log path, and exit 0; it cannot go with --dry-run",
    )
    # The run id that a detached start gives to the run process. The run process then runs in the foreground.
    parser.add_argument("--detached-run-id", help=argparse.SUPPRESS)
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


def _detach(argv: list[str], run_id: str, repo: Path, resume: bool, vcs: GitVersionControl) -> int:
    """Start `delegate run <argv>` for the run `run_id` in a new session, print the run id and the log path, and return 0.

    The run process writes its standard output and its standard error to the log file of the run. A resume appends
    to the log of its run. Return 1, and start nothing, when `repo` is not a repository or the run to resume is unknown.
    """
    try:
        if resume:
            run_dir = journal_of(repo, run_id, vcs).parent
        else:
            _, state = state_directory(repo, vcs)
            run_dir = state / "runs" / run_id
    except (EngineError, RunsError) as error:
        print(f"delegate run: {error}", file=sys.stderr)
        return 1
    run_dir.mkdir(parents=True, exist_ok=True)
    log = run_dir / LOG_NAME
    with log.open("a") as out:
        # start_new_session makes the setsid system call in the new process, also where the setsid program is absent.
        # -u writes each line to the log at once, so the log shows the run while it works.
        subprocess.Popen(
            [sys.executable, "-u", "-c", DETACHED_PROGRAM, json.dumps(sys.path), "run", *argv, "--detached-run-id", run_id],
            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(f"run {run_id}")
    print(f"log {log}")
    return 0


def _print_review(review: PlanReview) -> int:
    """Print what a resume does with the plan of the run: the orders, the verdict of each rule, and the later tickets.

    Return 0 when the rules of a plan change accept the resume, and 1 when one refuses it. Each refusal also goes to stderr.
    """
    if review.check is None:
        print(f"run {review.run_id}: no plan change")
        print(f"order: {', '.join(review.new_order)}")
        return 0
    check = review.check
    print(f"run {review.run_id}: plan change")
    print(f"old order: {', '.join(review.old_order)}")
    print(f"new order: {', '.join(review.new_order)}")
    for rule, verdict, detail in review.verdicts():
        print(f"rule {rule}: {verdict}: {PLAN_RULES[rule]}: {detail}")
    if check.later:
        print(f"later tickets to review, in plan order: {', '.join(check.later)}")
    for ticket in review.new_order:
        if ticket in review.texts:
            print(f"ticket {ticket} ({'added' if ticket in check.added else 'changed'}):")
            for line in review.texts[ticket].rstrip("\n").splitlines():
                print(f"  {line}" if line else "")
    problems = review.problems()
    for problem in problems:
        print(f"delegate run: {problem}", file=sys.stderr)
    return 1 if problems else 0


def _start_lines(run_id: str, journal: Path) -> list[str]:
    """The start lines of a run: its id, its journal, and how to follow it and pause it with the short run id."""
    dot = f" {glyphs().pending} "  # the separator of tokens.md, which has an ASCII form
    return [f"run {run_id}", f"journal {journal}", f"follow it with delegate watch{dot}pause it with delegate pause {short_run_id(run_id)}"]


def _end_lines(snap: status.Snapshot) -> list[str]:
    """The summary line of a run that ended or paused, and one retry line for each failed step, in plan order.

    The summary has the two token counts only when the snapshot has them. On a terminal, the result is in the colour of
    its role, and the token counts are faint.
    """
    dot = f" {glyphs().pending} "
    states = [ticket.state for ticket in snap.tickets]
    parts = [
        styled(RESULT_ROLES.get(snap.state, ""), f"run {snap.state}"), status.format_duration(snap.elapsed_seconds),
        *(f"{states.count(state)} {state}" for state in (status.BUILT, status.FAILED, status.SKIPPED)),
    ]
    if snap.tokens_new is not None and snap.tokens_cache is not None:
        parts.append(styled("dim", f"{status.format_tokens(snap.tokens_new)} new{dot}{status.format_tokens(snap.tokens_cache)} cache"))
    retries = [
        f"retry with delegate run --resume {short_run_id(snap.run_id)} --retry {ticket.id}"
        for ticket in snap.tickets if ticket.state == status.FAILED
    ]
    return [dot.join(parts), *retries]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    # The composition point also makes the version-control backend and the gate runner that the engine drives.
    vcs = GitVersionControl()
    if args.workflow is None and args.resume is None:
        parser.error("a workflow file or --resume RUN_ID is required")
    if args.retry is not None and args.resume is None:
        parser.error("--retry needs --resume RUN_ID")
    if args.reviewed is not None and args.resume is None:
        parser.error("--reviewed needs --resume RUN_ID")
    reviewed = tuple(ticket.strip() for value in args.reviewed or [] for ticket in value.split(",") if ticket.strip())
    if args.detach and args.dry_run:
        parser.error("--detach and --dry-run cannot go together")
    if args.resume is not None:
        try:
            args.resume = run_id_of(args.repo, args.resume, vcs)  # a short id names its run by the full id from here on
        except RunsError as error:
            print(f"delegate run: {error}", file=sys.stderr)
            return 1
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
    if args.dry_run and args.resume is not None:
        try:
            review = review_plan_change(workflow, args.workflow, args.repo, args.resume, vcs, reviewed)
        except EngineError as error:
            print(f"delegate run: {error}", file=sys.stderr)
            return 1
        return _print_review(review)
    if not args.dry_run:
        # The composition point: the workflow names the adapter, and this driver picks it for the engine.
        try:
            adapter = adapters.get(workflow.adapter)
        except KeyError:
            registered = ", ".join(adapters.registered_names()) or "none"
            print(f"delegate run: adapter {workflow.adapter!r} has no implementation yet; registered: {registered}", file=sys.stderr)
            return 1
        if args.detach and args.detached_run_id is None:
            run_id = args.resume if args.resume is not None else new_run_id()
            try:
                # The checks that need no agent come before the run process, so their error goes to the caller.
                check_start(
                    workflow, args.workflow, args.repo, tiers, adapter, vcs, run_id, resume=args.resume is not None,
                    break_lock=args.break_lock, reviewed=reviewed,
                )
            except EngineError as error:
                print(f"delegate run: {error}", file=sys.stderr)
                return 1
            return _detach(argv, run_id, args.repo, args.resume is not None, vcs)
        title = Title(sys.stdout)
        started: list[str] = []

        def on_start(run_id: str, journal: Path) -> None:
            title.set(f"delegate run{SEPARATOR}{short_run_id(run_id)}")
            print("\n".join(_start_lines(run_id, journal)), flush=True)
            started.append(run_id)

        try:
            result = run_workflow(
                workflow, args.workflow, args.repo, tiers, adapter, vcs, ShellGateRunner(), resume=args.resume, retry=args.retry,
                break_lock=args.break_lock, on_start=on_start, run_id=args.detached_run_id, reviewed=reviewed,
            )
        except EngineError as error:
            print(f"delegate run: {error}", file=sys.stderr)
            return 1
        except RunInterrupted as stop:
            if stop.journal is None:
                print("delegate: interrupted", file=sys.stderr)  # the run wrote no journal, so there is nothing to resume
                return EXIT_INTERRUPTED
            if not started:  # Ctrl-C came before the journal recorded the start of a resume
                print(f"run {stop.run_id}")
                print(f"journal {stop.journal}")
            print(f"delegate run: interrupted; the run {stop.run_id} stopped. Go on with delegate run --resume {stop.run_id}", file=sys.stderr)
            return EXIT_INTERRUPTED
        if result.taken_over is not None:
            print(
                f"delegate run: took over the lock of {workflow.run_branch} from run {result.taken_over.run_id}, "
                f"process {result.taken_over.pid}, which no longer exists",
                file=sys.stderr,
            )
        for ticket, reason in result.failures.items():
            print(f"delegate run: ticket {ticket}: {reason}", file=sys.stderr)
        for finding in result.unmapped:
            print(f"delegate run: unmapped finding: {finding}", file=sys.stderr)
        for ticket, reason in result.skipped.items():
            print(f"delegate run: ticket {ticket}: skipped: {reason}", file=sys.stderr)
        print("\n".join(_end_lines(status.snapshot(args.repo, result.run_id, vcs, stream_reader_of, utc_now()))))
        if result.paused is not None:
            print(f"paused {result.paused}; go on with delegate run --resume {result.run_id}")
            return EXIT_PAUSED
        return 0 if result.ok else 1
    print(f"workflow {args.workflow}: mode {workflow.mode}, adapter {workflow.adapter}")
    print(f"base branch {workflow.base_branch}, run branch {workflow.run_branch}")
    if workflow.ticket_start == START_RUN_BRANCH:
        print(f"each ticket starts from the run branch {workflow.run_branch}")
    for number, ticket in enumerate(plan_order(workflow), start=1):
        blocked = ", ".join(ticket.blocked_by) or "none"
        print(f"ticket {ticket.id} ({number}/{len(workflow.tickets)}): stack {ticket.stack}, blocked by {blocked}")
    for stack in workflow.stacks.values():
        for command in stack.prepare:
            print(f"stack {stack.name} prepare: {command}")
        for role, notes in (("specialist", stack.specialist_notes), ("verifier", stack.verifier_notes)):
            if notes is not None:
                print(f"stack {stack.name} {role}-notes:")
                print("\n".join(f"  {line}" if line.strip() else "" for line in notes.strip().splitlines()))
    for role, tier in workflow.tier_overrides.items():
        print(f"tier override {role}: {tier}")
    return 0
