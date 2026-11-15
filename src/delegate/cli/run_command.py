"""The `delegate run` command: check a workflow file and print its plan, or build its tickets.

`--dry-run` validates the file and prints the plan in dependency order, with the state directory of the run (#200). Then it checks the setup (#182, `setup_check.py`):
for each stack, the agent files exist for the adapter, and the verifier permits each gate; it exits 1 on a problem. It
creates no branch, no worktree and no journal. Without it, the engine builds
each ticket through the workflow's harness adapter. `--resume <run-id>` goes on
with a run that stopped or paused, from its journal. `--retry <ticket>` with it goes on
with one failed step of that run, also when the run ended, and `--coordinator-commit <sha>` with it names a commit of the
coordinator that changes only hotspot paths (#225). A run that `delegate
pause` stopped at a step boundary exits with `EXIT_PAUSED`. A run that a failure of the harness (an `AdapterError`)
halted exits with `EXIT_HALTED`: it has no `run-end`, and `--resume` goes on at the open step. A run that a crash stopped
exits with `EXIT_HALTED` too, for the same reason (#281).

A resume with a workflow whose tickets differ from the newest plan of the run is
a plan change. `--reviewed <ids>` names the later tickets that the coordinator
reviewed. `--resume <run-id> --dry-run` prints the old order, the new order and
the verdict of each rule of a plan change, then what the resume does with each ticket that the run skipped (#260), and
changes nothing.

When the run holds its lock and its journal records the start, the command prints the start lines of frame 1k of the
design (#119): `run <id>`, `journal <path>`, and the line that tells how to follow and pause the run. When the run ends
or pauses, it prints the per-ticket lines on stderr, then the summary line `run <result> · <used> · ...` from the
snapshot of the journal, and one retry line for each failed step. On a terminal the result has the colour of its role.

Ctrl-C stops a run in the foreground at once. The engine releases the lock and records `interrupted` in the
journal. The command prints one line on stderr that tells how to resume, and exits `EXIT_INTERRUPTED` (130). The
start lines are on stdout already. `delegate pause` stays the way to stop a run at a step boundary.

SIGTERM and SIGHUP stop a run as Ctrl-C does (#178): the same interrupt, the same journal event, the same line on
stderr, and the same exit status. `kill <pid>` is the way to stop a detached run, which has no terminal. Before the
engine releases the lock, it ends the process group of the harness or the gate that runs, so no child process of the
run stays alive. A signal that comes while the engine ends the run is ignored: the first one decides.

A background shell that is not interactive sets SIGINT to ignored for its command, and the detached run inherits that
(#198). In that case Python installs no handler for SIGINT, and `kill -INT <pid>` did nothing. The run now handles
SIGINT as SIGTERM, so it stops within seconds.

`--detach` starts the run in a new process with the setsid system call, with its
output in the log file of the run, then prints the run id and the log path and
exits 0. A harness that stops its tool shell, or a background task at a time
limit, then does not stop the run. Before it starts the process, it makes the
checks of the run that need no agent (`engine.check_start`): the workflow, the
tiers, the lock, for a new run the base branch and a free run branch, and for a
resume the rules of a plan change with `--reviewed`. A check that fails prints
its error on stderr, exits 1, and starts no process, as a run in the foreground
does. The command only looks at the lock. The run process takes the lock, so the
kernel lock is on the run process, and the kernel releases it when that process
ends (#288).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .. import adapters
from ..adapters.git import GitVersionControl
from ..adapters.shell import ShellGateRunner
from ..ports.harness import Adapter
from ..run import status, status_model
from ..run.domain import START_RUN_BRANCH
from ..run.domain import PLAN_RULES
from ..run.dry_run import review_plan_change
from ..run.context import ResumeRequest, project_config_paths
from ..run.engine import RunInterrupted, RunResult, check_start, new_run_id, run_context, run_workflow
from ..run.errors import EngineError
from ..run.host import host_name
from ..run.index import index_observers, record_runs
from ..run.output import glyphs, short_run_id, styled
from ..run.plan_review import PlanReview
from ..run.quality import quality_checks
from ..run.retry import RetryHint, retry_hints
from ..run.runs import LOG_NAME, RunsError, journal_of, locate, repository_of_run, run_id_of
from ..run.state_location import StateLocation, StateRootError, locate_state, record_state_root, workflow_of_run
from ..run.user_config import UserConfigError, config_path
from ..run.watch import EXIT_INTERRUPTED
from ..run.workflow import Workflow, WorkflowError, load_workflow, plan_order, specialist_tier
from ..shared.tiers import Tiers, load_tiers
from .location import report_location
from .epilog import RUN_PAGE, WORKFLOW_PAGE, HelpFormatter, epilog
from .machine_settings import MachineSettings, index_check, machine_settings
from .opencode_override import add_opencode_override_flags, apply_opencode_override
from .setup_check import check_setup
from .stop_signals import stop_signals_as_ctrl_c
from .status import stream_reader_of, utc_now
from .title import SEPARATOR, Title

# The exit status of a run that a pause request stopped at a step boundary (see `delegate pause`).
EXIT_PAUSED: int = 3

# The exit status of a run that a failure of the harness halted: an `AdapterError` (#177). A crash stops a run with it too (#281).
EXIT_HALTED: int = 4

# The paint role of the result of a run in its summary line (tokens.md): the result word is bold.
RESULT_ROLES: dict[str, str] = {status_model.BUILT: "ok bold", status_model.FAILED: "bad bold", status_model.PAUSED: "warn bold", status_model.HALTED: "bad bold"}

# The program of a detached run process: it imports the package from the import path of the command that started it
# (argv[1]), then runs `delegate` with the rest of its arguments. A Nix wrapper puts the package on the import path of
# its own process only, so `python -m delegate...` can fail to find it. The path goes as an argument, not in the
# environment, so the agents and the gates of the run do not get it.
DETACHED_PROGRAM: str = (
    "import json, sys; sys.path[:0] = json.loads(sys.argv[1]); "
    "from delegate.cli.delegate import main; sys.exit(main(sys.argv[2:]))"
)


# The text at the end of the help, before the docs site (#247, #248). A coordinator reads the help and the dry run.
RUN_EPILOG: str = (
    "Before each launch and each resume, run delegate run <workflow> --dry-run and read its quality check. "
    "A warn line names a setting to add or change, and its effect on the quality or the time of the run."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delegate run",
        description="Build the tickets of a workflow file, or check it and print its plan.",
        epilog=epilog(RUN_PAGE, WORKFLOW_PAGE, before=RUN_EPILOG),
        formatter_class=HelpFormatter,
    )
    parser.add_argument(
        "workflow", type=Path, nargs="?",
        help="path to the workflow TOML file; with --resume the default is the file that the run started from",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate the workflow and print the plan and the state directory of the run, then the quality check of the workflow, then check that each "
        "stack has its agent files and that its verifier permits its gates, then run the guard probe of each verifier when the adapter has one; "
        "with --resume, print the old order, the new order and the verdict of each rule of a plan change, and no quality check; "
        "create no branch, no worktree and no journal",
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
        "use its worktree and its commits again, and go on at the first phase that did not complete; "
        "when the text of TICKET in the workflow changed, start TICKET again from its base with a new specialist, "
        "and keep the old branch as <branch>-failed-<n>",
    )
    parser.add_argument(
        "--coordinator-commit", metavar="SHA",
        help="with --retry, a commit of the coordinator at the tip of the branch of TICKET that changes only hotspot paths, "
        "for example a new render of the agent pair; the hotspot guard does not count its paths, the gates and the verifier "
        "check it as part of the branch, and in economy mode the retry of the ticket at the tip of a chain whose verification "
        "failed goes on with the commit",
    )
    parser.add_argument(
        "--agents-changed", action="store_true",
        help="with --resume, seal each agent again from the main checkout, record the new seal in the resume event, and go on with "
        "it as the agents of the run; without it, a file of a seal that differs from the record of the run fails the step",
    )
    parser.add_argument(
        "--stack-changed", action="store_true",
        help="with --resume, go on with a workflow file that changes the configuration of a stack, and record the change in a "
        "stack-change event before the resume event, with the old and the new value of each changed field; later resumes compare "
        "with it; the flag with no change of a stack is refused; with --dry-run it prints the difference and exits 0",
    )
    parser.add_argument(
        "--break-lock", action="store_true",
        help="not necessary, and 2.0.0 removes it: a lock that no process holds is free, so the run takes it; with the flag, "
        "the run prints that the lock is free",
    )
    parser.add_argument(
        "--detach", action="store_true",
        help="make the checks of the run that need no agent, and exit 1 with the error when one fails; then start the run in a "
        "new process with setsid, outside the process group of the shell; write its output to the file run.log in the directory "
        "of the run; print the run id and the log path, and exit 0; it cannot go with --dry-run",
    )
    # The run id that a detached start gives to the run process. The run process then runs in the foreground.
    parser.add_argument("--detached-run-id", help=argparse.SUPPRESS)
    parser.add_argument(
        "--repo", type=Path, default=None,
        help="the git repository to build in; default is the current directory; with --resume and no --repo, the repository that the run "
        "records when the run is not in the current directory",
    )
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


def _detach(argv: list[str], run_id: str, repo: Path, resume: bool, vcs: GitVersionControl, location: StateLocation) -> int:
    """Start `delegate run <argv>` for the run `run_id` in a new session, print the run id and the log path, and return 0.

    The run process writes its standard output and its standard error to the log file of the run. A resume appends
    to the log of its run. Return 1, and start nothing, when the run to resume is unknown. `location` is the location
    that the checks of the start looked up (#239).
    """
    try:
        if resume:
            run_dir = journal_of(repo, run_id, vcs, location=location).parent
        else:
            state = location.state_directory(run_id)
            record_state_root(location, state)  # the command that prints the run id finds the run, before the run process starts
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


def _print_stack_changes(review: PlanReview) -> None:
    """Print the difference that `--stack-changed` lets the resume record (#290), and say that the resume records it. Print nothing without it."""
    for change in review.stack_changes:
        print(f"stack {change.stack!r}: {', '.join(change.fields)}")
        for name, (old, new) in change.fields.items():
            print(f"  {name}: was {json.dumps(old)}, now {json.dumps(new)}")
    if review.stack_changes:
        print("the resume will record the change in a stack-change event")


def _print_review(review: PlanReview) -> int:
    """Print what a resume does with the plan of the run: the orders, the verdict of each rule, and the later tickets.

    Return 0 when the rules of a plan change accept the resume, and 1 when one refuses it. Each refusal also goes to stderr.
    """
    if review.check is None:
        print(f"run {review.run_id}: no plan change")
        print(f"order: {', '.join(review.new_order)}")
        _print_stack_changes(review)
        _print_skipped(review)
        _print_retry(review)
        problems = [*review.problems(), *_engine_problems(review)]
        for problem in problems:
            print(f"delegate run: {problem}", file=sys.stderr)
        return 1 if problems else 0
    check = review.check
    print(f"run {review.run_id}: plan change")
    print(f"old order: {', '.join(review.old_order)}")
    print(f"new order: {', '.join(review.new_order)}")
    for rule, verdict, detail in review.verdicts():
        print(f"rule {rule}: {verdict}: {PLAN_RULES[rule]}: {detail}")
    _print_stack_changes(review)
    if check.later:
        print(f"later tickets to review, in plan order: {', '.join(check.later)}")
    for ticket in review.new_order:
        if ticket in review.texts:
            print(f"ticket {ticket} ({'added' if ticket in check.added else 'changed'}):")
            for line in review.texts[ticket].rstrip("\n").splitlines():
                print(f"  {line}" if line else "")
    _print_skipped(review)
    _print_retry(review)
    problems = [*review.problems(), *_engine_problems(review)]
    for problem in problems:
        print(f"delegate run: {problem}", file=sys.stderr)
    return 1 if problems else 0


def _print_index_check(settings: MachineSettings | None, problem: str | None) -> int:
    """Print the part of the setup check for the run index (#323): the configuration file, the index directory, and the write probe.

    `problem` is the error of the settings when `settings` is None: a configuration file or a state root that cannot be used.
    Each problem goes to stderr. Return 1 with a problem, else 0.
    """
    if settings is None:
        print(f"config: {config_path(os.environ)}")
        print(f"delegate run: {problem}", file=sys.stderr)
        return 1
    lines, problems = index_check(settings)
    for line in lines:
        print(line)
    for found in problems:
        print(f"delegate run: {found}", file=sys.stderr)
    return 1 if problems else 0


def _engine_problems(review: PlanReview) -> list[str]:
    """The refusals of the engine that are not a rule of the plan, as problems of the dry run: the refusal of the retry of
    `--retry` (#261), and the refusal of another check of the resume (#357). None when the engine accepts the resume."""
    return [message for message in (review.retry_refusal, review.resume_refusal) if message is not None]


def _print_retry(review: PlanReview) -> None:
    """Print the phase at which the retry of `--retry` goes on, when the engine accepts it (#269). Print nothing else."""
    if review.retry_outlook is not None and not review.problems():
        print(review.retry_outlook)


def _print_skipped(review: PlanReview) -> None:
    """Print what the resume does with each ticket that the run skipped (#260). Print nothing when the run skipped no
    ticket, or when the rules refuse the resume."""
    if not review.skipped or review.problems():
        return
    print("skipped tickets:")
    for skipped in review.skipped:
        print(f"  ticket {skipped.ticket}: {skipped.text}")


def _start_lines(run_id: str, journal: Path) -> list[str]:
    """The start lines of a run: its id, its journal, and how to follow it and pause it with the short run id."""
    dot = f" {glyphs().pending} "  # the separator of tokens.md, which has an ASCII form
    return [f"run {run_id}", f"journal {journal}", f"follow it with delegate watch{dot}pause it with delegate pause {short_run_id(run_id)}"]


def _end_lines(snap: status_model.Snapshot, hints: list[RetryHint]) -> list[str]:
    """The summary line of a run that ended or paused, and one retry line for each retry that the engine accepts, in plan order.

    `hints` gives the retries (see `retry_hints`), also a retry that goes on only with a new text of the ticket. In
    `economy` mode after the verification of the chain, that is one ticket of the chain, not each ticket that the
    verification failed (#261). A retry that needs a commit of the coordinator has `--coordinator-commit <sha>` in its line:
    in `economy` mode, only when the newest report of a chain fix-up names a hotspot path (#269).

    The summary has the two token counts only when the snapshot has them. On a terminal, the result is in the colour of
    its role, and the token counts are faint.
    """
    dot = f" {glyphs().pending} "
    states = [ticket.state for ticket in snap.tickets]
    parts = [
        styled(RESULT_ROLES.get(snap.state, ""), f"run {snap.state}"), status_model.format_duration(snap.elapsed_seconds),
        *(f"{states.count(state)} {state}" for state in (status_model.BUILT, status_model.FAILED, status_model.SKIPPED)),
    ]
    if snap.tokens_new is not None and snap.tokens_cache is not None:
        parts.append(styled("dim", f"{status_model.format_tokens(snap.tokens_new)} new{dot}{status_model.format_tokens(snap.tokens_cache)} cache"))
    retries = [
        f"retry with delegate run --resume {short_run_id(snap.run_id)} --retry {hint.ticket}"
        + (" --coordinator-commit <sha>" if hint.coordinator else "")
        for hint in hints
    ]
    return [dot.join(parts), *retries]


class _Stop(Exception):
    """A step before the run printed its error. `main` returns `code`."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _Setup:
    """What the paths of the command share, after the checks of the arguments, the settings, the repository and the workflow."""

    args: argparse.Namespace
    argv: list[str]
    vcs: GitVersionControl
    reviewed: tuple[str, ...]
    workflow: Workflow
    tiers: Tiers
    location: StateLocation | None
    settings: MachineSettings | None
    settings_problem: str | None


def _refuse(message: str) -> _Stop:
    """Print `message` as an error of the command on stderr, and give the stop that exits 1."""
    print(f"delegate run: {message}", file=sys.stderr)
    return _Stop(1)


def _reviewed_tickets(parser: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[str, ...]:
    """Check that each option that needs `--resume` or `--retry` has it, and give the tickets of `--reviewed`. Exit 2 with a usage error when not."""
    if args.workflow is None and args.resume is None:
        parser.error("a workflow file or --resume RUN_ID is required")
    if args.retry is not None and args.resume is None:
        parser.error("--retry needs --resume RUN_ID")
    if args.coordinator_commit is not None and args.retry is None:
        parser.error("--coordinator-commit needs --retry TICKET")
    if args.reviewed is not None and args.resume is None:
        parser.error("--reviewed needs --resume RUN_ID")
    if args.agents_changed and args.resume is None:
        parser.error("--agents-changed needs --resume RUN_ID")
    if args.stack_changed and args.resume is None:
        parser.error("--stack-changed needs --resume RUN_ID")
    if args.detach and args.dry_run:
        parser.error("--detach and --dry-run cannot go together")
    return tuple(ticket.strip() for value in args.reviewed or [] for ticket in value.split(",") if ticket.strip())


def _machine_settings(dry_run: bool) -> tuple[MachineSettings | None, str | None]:
    """The configuration file and the run index of this machine (#323), and the problem of them when they cannot be used.

    A run, a detached run and a resume with an error of the configuration start nothing: raise `_Stop` for them. The dry
    run reports the error as a problem of its setup check.
    """
    try:
        return machine_settings(), None
    except (UserConfigError, RunsError) as error:
        if not dry_run:
            raise _refuse(str(error)) from None
        return None, str(error)


def _locate_resume(args: argparse.Namespace, vcs: GitVersionControl) -> StateLocation | None:
    """Look up the repository of a resume and its state directories, one time, to pass them on (#239). A new run looks them up in its checks of the start.

    Set `args.repo` and the full run id in `args.resume`. Return None for a new run.
    """
    if args.resume is None:
        return None
    try:
        if args.repo is None:
            args.repo = repository_of_run(args.resume, vcs)  # the current directory, else the repository that `run-start` records (#306)
        location = report_location(args.repo, vcs) or locate(args.repo, vcs)
        args.resume = run_id_of(args.repo, args.resume, vcs, location=location)  # a short id names its run by the full id from here on
    except RunsError as error:
        raise _refuse(str(error)) from None
    return location


def _setup(parser: argparse.ArgumentParser, argv: list[str], args: argparse.Namespace, vcs: GitVersionControl) -> _Setup:
    """Check the arguments, then look up the settings, the repository, the workflow and the tiers. Raise `_Stop` after the error that it prints."""
    reviewed = _reviewed_tickets(parser, args)
    settings, settings_problem = _machine_settings(args.dry_run)
    location = _locate_resume(args, vcs)
    if args.repo is None:
        args.repo = Path(".")
    if args.workflow is None:
        try:
            args.workflow = workflow_of_run(args.repo, args.resume, vcs, location=location)
        except EngineError as error:
            raise _refuse(str(error)) from None
    try:
        tiers = _override_tiers(_load_tiers(args.tiers), args)
        workflow = load_workflow(args.workflow, tiers, adapters.registered_names())
    except TierFileError as error:
        raise _refuse(str(error)) from None
    except WorkflowError as error:
        for problem in error.problems:
            print(f"delegate run: {args.workflow}: {problem}", file=sys.stderr)
        raise _Stop(1) from None
    if location is None:
        location = report_location(args.repo, vcs)  # prints the note of the lookup; None when it fails, and the checks say so
    return _Setup(args, argv, vcs, reviewed, workflow, tiers, location, settings, settings_problem)


def _dry_run_of_resume(setup: _Setup) -> int:
    """`--resume --dry-run`: print what the resume does with the plan of the run, and the check of the run index. Change nothing."""
    args = setup.args
    try:
        request = ResumeRequest(args.resume, setup.reviewed, args.retry, args.coordinator_commit, args.stack_changed)
        review = review_plan_change(
            setup.workflow, args.workflow, args.repo, setup.vcs, request, location=setup.location,
            config_paths=project_config_paths(adapters.get(setup.workflow.adapter)),
        )
    except EngineError as error:
        print(f"delegate run: {error}", file=sys.stderr)
        return 1
    return max(_print_review(review), _print_index_check(setup.settings, setup.settings_problem))


def _print_plan(workflow: Workflow, config_paths: tuple[str, ...]) -> None:
    """Print the plan of a dry run: the tickets in dependency order, and for each stack its hotspots, its prepare commands and its notes."""
    if workflow.ticket_start == START_RUN_BRANCH:
        print(f"each ticket starts from the run branch {workflow.run_branch}")
    for number, ticket in enumerate(plan_order(workflow), start=1):
        blocked = ", ".join(ticket.blocked_by) or "none"
        print(
            f"ticket {ticket.id} ({number}/{len(workflow.tickets)}): stack {ticket.stack}, blocked by {blocked}, "
            f"specialist tier {specialist_tier(workflow, ticket)}"
        )
    for stack in workflow.stacks.values():
        # The paths that no specialist commit changes, whatever the workflow names (#188, #273).
        print(f"stack {stack.name} built-in hotspots: {', '.join(stack.built_in_hotspots(config_paths))}")
        for command in stack.prepare:
            print(f"stack {stack.name} prepare: {command}")
        for role, notes in (("specialist", stack.specialist_notes), ("verifier", stack.verifier_notes)):
            if notes is not None:
                print(f"stack {stack.name} {role}-notes:")
                print("\n".join(f"  {line}" if line.strip() else "" for line in notes.strip().splitlines()))
    for role, tier in workflow.tier_overrides.items():
        print(f"tier override {role}: {tier}")


def _dry_run(setup: _Setup) -> int:
    """`--dry-run`: print the plan, the state directory of the run, the quality check, the setup check and the check of the run index. Create nothing."""
    args, workflow = setup.args, setup.workflow
    state: Path | None = None
    try:
        state = (setup.location or locate_state(args.repo, setup.vcs)).state_directory()
    except StateRootError as error:
        print(f"delegate run: {error}", file=sys.stderr)
        return 1
    except EngineError:
        pass  # a dry run needs no repository, so a directory that is not a repository has no state directory to print
    print(f"workflow {args.workflow}: mode {workflow.mode}, adapter {workflow.adapter}")
    print(f"base branch {workflow.base_branch}, run branch {workflow.run_branch}")
    if state is not None:
        print(f"state directory {state}")
    _print_plan(workflow, project_config_paths(adapters.get(workflow.adapter)))
    print("quality check")
    for check in quality_checks(workflow):
        print(check.line())
    setup_check = check_setup(workflow, adapters.get(workflow.adapter), args.repo, setup.tiers, setup.vcs)
    for line in setup_check.lines:
        print(line)
    for problem in setup_check.problems:
        print(f"delegate run: {problem}", file=sys.stderr)
    index_code = _print_index_check(setup.settings, setup.settings_problem)
    return 1 if setup_check.problems or index_code else 0


def _adapter_of(workflow: Workflow) -> Adapter:
    """The adapter that the workflow names: the composition point, where this driver picks it for the engine. Raise `_Stop` when it has no implementation."""
    try:
        return adapters.get(workflow.adapter)
    except KeyError:
        registered = ", ".join(adapters.registered_names()) or "none"
        raise _refuse(f"adapter {workflow.adapter!r} has no implementation yet; registered: {registered}") from None


def _detached_start(setup: _Setup, adapter: Adapter) -> int:
    """`--detach`: make the checks of the start that need no agent, then start the run process and print its run id and its log."""
    args = setup.args
    run_id = args.resume if args.resume is not None else new_run_id()
    try:
        # The checks that need no agent come before the run process, so their error goes to the caller.
        location = setup.location or locate_state(args.repo, setup.vcs)
        run = run_context(setup.workflow, args.workflow, setup.tiers, adapter, setup.vcs, ShellGateRunner(), run_id, location)
        resume = (
            ResumeRequest(run_id, setup.reviewed, args.retry, args.coordinator_commit, args.stack_changed)
            if args.resume is not None else None
        )
        location = check_start(run, location, resume=resume, detach=True)
    except EngineError as error:
        print(f"delegate run: {error}", file=sys.stderr)
        return 1
    return _detach(setup.argv, run_id, args.repo, args.resume is not None, setup.vcs, location)


def _print_end(setup: _Setup, adapter: Adapter, result: RunResult) -> int:
    """Print the notes, the failures and the summary of a run that stopped, and give its exit status."""
    args, workflow = setup.args, setup.workflow
    if args.break_lock:
        print(
            f"delegate run: --break-lock: the lock of {workflow.run_branch} is free, because no process holds it; "
            "the flag is not necessary, and 2.0.0 removes it",
            file=sys.stderr,
        )
    if result.taken_over is not None:
        print(
            f"delegate run: took over the lock of {workflow.run_branch} from run {result.taken_over.run_id}, "
            f"process {result.taken_over.pid}, which holds the lock no longer",
            file=sys.stderr,
        )
    if result.agents_note is not None:
        print(f"delegate run: {result.agents_note}", file=sys.stderr)
    for ticket, reason in result.failures.items():
        print(f"delegate run: ticket {ticket}: {reason}", file=sys.stderr)
    for finding in result.unmapped:
        print(f"delegate run: unmapped finding: {finding}", file=sys.stderr)
    for ticket, reason in result.skipped.items():
        print(f"delegate run: ticket {ticket}: skipped: {reason}", file=sys.stderr)
    snap = status.snapshot_of_journal(result.journal, stream_reader_of, utc_now())
    print("\n".join(_end_lines(snap, retry_hints(workflow, result.journal, result.run_id, project_config_paths(adapter)))))
    if result.paused is not None:
        print(f"paused {result.paused}; go on with delegate run --resume {result.run_id}")
        return EXIT_PAUSED
    if result.halted is not None:
        print(f"delegate run: halted at {result.halted_at}: {result.halted}", file=sys.stderr)
        print(f"halted at {result.halted_at}; go on with delegate run --resume {result.run_id}")
        return EXIT_HALTED
    if result.crashed is not None:
        trace = f"; traceback {result.traceback}" if result.traceback is not None else ""
        print(f"delegate run: crashed at {result.crashed_at}: {result.crashed}{trace}", file=sys.stderr)
        again = f" --retry {result.crashed_retry}" if result.crashed_retry is not None else ""
        print(f"crashed at {result.crashed_at}; go on with delegate run --resume {result.run_id}{again}")
        return EXIT_HALTED
    return 0 if result.ok else 1


def _start(setup: _Setup, adapter: Adapter) -> int:
    """The start of a new run, the resume of a run, and the run process of a detached start: run the workflow in the foreground and print its end."""
    args, workflow, settings = setup.args, setup.workflow, setup.settings
    assert settings is not None  # a run with no settings stopped in `_setup`
    # The run process prints each warning of the quality check at its start; a detached run writes it to its log.
    for check in quality_checks(workflow):
        if check.warns:
            print(f"delegate run: {check.line()}", file=sys.stderr)
    # The records of the runs of this machine that have none, or an older one (#323). A failure here changes no run: the run
    # adds `index-warning` to its own journal when its own write fails.
    try:
        record_runs(settings.root, host_name(), utc_now(), settings.index.index)
    except Exception:  # noqa: BLE001 - nothing that the records of the other runs do may change this run
        pass
    title = Title(sys.stdout)
    started: list[str] = []

    def on_start(run_id: str, journal: Path) -> None:
        title.set(f"delegate run{SEPARATOR}{short_run_id(run_id)}")
        print("\n".join(_start_lines(run_id, journal)), flush=True)
        started.append(run_id)

    try:
        with stop_signals_as_ctrl_c():
            result = run_workflow(
                workflow, args.workflow, args.repo, setup.tiers, adapter, setup.vcs, ShellGateRunner(), resume=args.resume, retry=args.retry,
                break_lock=args.break_lock, on_start=on_start, run_id=args.detached_run_id, reviewed=setup.reviewed,
                agents_changed=args.agents_changed, coordinator_commit=args.coordinator_commit, location=setup.location,
                stack_changed=args.stack_changed,
                observers=index_observers(settings.index.index, setup.location.top if setup.location else args.repo),
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
    return _print_end(setup, adapter, result)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    # The composition point also makes the version-control backend and the gate runner that the engine drives.
    vcs = GitVersionControl()
    try:
        setup = _setup(parser, argv, args, vcs)
        if args.dry_run:
            return _dry_run_of_resume(setup) if args.resume is not None else _dry_run(setup)
        adapter = _adapter_of(setup.workflow)
        if args.detach and args.detached_run_id is None:
            return _detached_start(setup, adapter)
        return _start(setup, adapter)
    except _Stop as stop:
        return stop.code
