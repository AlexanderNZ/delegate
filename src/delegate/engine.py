"""The workflow engine: build each ticket through a harness adapter.

For each ticket the engine makes a worktree from the base branch, installs the
push guard in it (see `guards.py`), spawns the stack's specialist through the adapter, checks the specialist's report, runs
the stack's gates itself, and records each event in the journal. After the
specialist reports `committed`, and before any gate runs, the engine compares
the changed paths with the hotspot patterns of the stack. A match fails the
step, and no verifier runs (see `guards.py`). A fix-up gets the same check.

In `assure` mode the engine then verifies the built branch. It makes a
temporary copy of the branch, builds a full verifier brief with the brief
generator, spawns the stack's verifier on the verifier tier, and checks the
verdict report. Before the verifier starts, the engine rebases the ticket
branch onto the run branch, and runs the gates again on the rebased tree. On ACCEPT the engine fast-forwards the run branch to the
verified commit. On REJECT the engine starts a fix-up round: it writes the
findings to a file, sends the specialist a fix-up brief in the same worktree,
requires the fix as a new commit on top of the rejected commit, runs the gates,
and spawns a fresh verifier with the scoped brief (the findings and the delta
from the rejected commit). After `FIXUP_ROUND_LIMIT` rounds the step fails and
the step fails. The run branch moves only on ACCEPT.

The engine records the HEAD and the status of the worktree of the ticket before
and after each verifier run (see `guards.py`). A difference is an invariant
violation: the journal records it with the changes, and the run halts. The
engine removes the temporary copy after every verifier run.

In `economy` mode each ticket branch starts from the tip of the previous built
ticket, and the engine does not verify a ticket by itself. After the last
ticket, `_ChainRun` verifies once for each stack, over the commits of the
tickets of that stack. A REJECT gets one fix-up round, as a new commit on the
chain tip, and then a fresh scoped verifier. A finding maps to a ticket by its
label, and a finding with no known label is recorded as unmapped. When every
stack is accepted, the engine fast-forwards the run branch to the chain tip.

A run holds many tickets, which the engine takes in dependency order. A failed
step does not end the run. A ticket whose blocker failed or was skipped is
skipped, and the journal records which blocker. A ticket with no failed blocker
still runs. A rebase conflict fails that step only: the engine aborts the
rebase, so the branch and its worktree keep the state from before the rebase.

A run holds the lock of its run branch from before it changes anything until it
ends, so a second run on the same run branch is refused (see `lock.py`).

A run can be resumed. `run_workflow` with `resume` opens the journal of an
earlier run, rebuilds the state from it, and goes on at the first step that is
not complete. It appends to the journal and never rewrites a line. A step with
a `step-end` is complete, and the engine never builds it again. A step with a
`run-branch-advance` has its commit on the run branch already, so the engine
only closes it. Any other open step restarts in its own worktree, which the
engine makes again if it is gone. A specialist whose report the journal shows as
valid is not spawned again.

Standard library and git through subprocess only.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .brief import (
    BriefError, ChainSegment, chain_brief, continuation_brief, finding_labels_section, fixup_brief, specialist_brief,
    verifier_run_brief, verifier_run_sections,
)
from .guards import (
    GuardError, InvariantViolation, changed_paths, check_worktree_unchanged, hotspot_matches, install_push_guard,
    snapshot_worktree,
)
from .journal import Journal, JournalError, read_events
from .lock import LockError, LockHolder, check_free, run_lock
from .ports import harness
from .ports.harness import Adapter, AdapterRequest
from .ports.vcs import VcsError, VersionControl
from .reports import ReportError, VerifierReport, map_findings, read_specialist_report, read_verifier_report
from .tiers import Tiers
from .workflow import Stack, Ticket, Workflow, plan_order

# The tier of the specialist role in each mode, before a workflow override.
SPECIALIST_TIER: dict[str, str] = {"assure": "strong", "economy": "standard"}

# The tier of the verifier role, before a workflow override. It is the same in every mode.
VERIFIER_TIER: str = "verifier"

# The journal keeps this many characters of the end of a gate's output.
GATE_OUTPUT_TAIL_CHARS: int = 4000

# A REJECT starts at most this many fix-up rounds. Each round is one fix-up
# commit and one fresh verifier. The limit depends on the mode: `assure` verifies
# each ticket, and `economy` verifies each stack once, at the end of the chain.
FIXUP_ROUND_LIMIT: dict[str, int] = {"assure": 2, "economy": 1}

# The specialist of a ticket continues in the same worktree at most this many
# times after it ends capped or failed, or after the gates are red. One count
# covers all three causes. The limit depends on the mode: `assure` pays for
# more assurance, `economy` for fewer tokens.
CONTINUATION_LIMIT: dict[str, int] = {"assure": 2, "economy": 1}

BUILT: str = "built"
FAILED: str = "failed"
SKIPPED: str = "skipped"

# The `phase` of a gate result in the journal: the gates after the specialist
# built the ticket, after the rebase onto the run branch, and after a fix-up.
PHASE_BUILD: str = "build"
PHASE_REBASE: str = "rebase"
PHASE_FIXUP: str = "fixup"


class EngineError(Exception):
    """The run cannot start or go on. The message names the input at fault."""


class _StepFailed(Exception):
    """A fix-up round or a verifier run cannot go on. The message is the reason the step fails."""


class _Rejected(_StepFailed):
    """The verifier rejected the branch after the last fix-up round. `findings` holds the findings of the last verdict."""

    def __init__(self, message: str, findings: list[str]) -> None:
        super().__init__(message)
        self.findings = findings


@dataclass(frozen=True)
class RunResult:
    run_id: str
    journal: Path
    built: list[str]
    failed: list[str]
    failures: dict[str, str]
    skipped: dict[str, str]
    unmapped: list[str] = field(default_factory=list)

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


def _role_model(workflow: Workflow, tiers: Tiers, column: str, role: str) -> tuple[str, str]:
    """The tier of a role and its model from the adapter's own tier column."""
    default = VERIFIER_TIER if role == "verifier" else SPECIALIST_TIER[workflow.mode]
    tier = workflow.tier_overrides.get(role, default)
    try:
        return tier, tiers.model_for(tier, column)
    except KeyError:
        raise EngineError(f"the tier table has no {column!r} column for tier {tier!r}") from None
    except ValueError:
        raise EngineError(f"the tier table has no tier {tier!r}, which the {role} needs") from None


@contextmanager
def _vcs_errors() -> Iterator[None]:
    """Turn a failure of the version-control port into an EngineError with the same message."""
    try:
        yield
    except VcsError as error:
        raise EngineError(str(error)) from None


def _check_branches(workflow: Workflow, repo: Path, vcs: VersionControl) -> None:
    """Refuse a run whose base branch is missing or whose ticket branch is taken. Create nothing."""
    with _vcs_errors():
        base_exists = vcs.branch_exists(repo, workflow.base_branch)
    if not base_exists:
        raise EngineError(f"base-branch {workflow.base_branch!r} is not a branch of the repository {repo}")
    for ticket in workflow.tickets:
        branch = _ticket_branch(workflow, ticket)
        with _vcs_errors():
            taken = vcs.branch_exists(repo, branch)
        if taken:
            raise EngineError(f"branch {branch!r} for ticket {ticket.id!r} exists already; delete it or choose another run-branch")


def _ticket_branch(workflow: Workflow, ticket: Ticket) -> str:
    return f"{workflow.run_branch}-{ticket.id}"


def state_directory(repo: Path, vcs: VersionControl) -> tuple[Path, Path]:
    """The top level of the repository and the directory where the engine keeps its state.

    Raise EngineError when `repo` is not inside a repository.
    """
    with _vcs_errors():
        top = vcs.repository_root(repo)
        return top, vcs.shared_data_directory(top) / "delegate"


def _journal_events(state_dir: Path, run_id: str) -> tuple[Journal, list[dict[str, object]]]:
    """Open the journal of an earlier run for appending. Raise EngineError when the run has none or its journal is not valid."""
    try:
        journal, events = Journal.reopen(state_dir / "runs" / run_id / "journal.jsonl")
    except JournalError as error:
        raise EngineError(f"run {run_id!r} cannot be resumed: {error}") from None
    if not events or events[0]["event"] != "run-start":
        raise EngineError(f"run {run_id!r} cannot be resumed: its journal does not start with run-start")
    return journal, events


def workflow_of_run(repo: Path, run_id: str, vcs: VersionControl) -> Path:
    """The path of the workflow file that the run `run_id` started from. Raise EngineError when the run is unknown."""
    _, state_dir = state_directory(repo, vcs)
    try:
        events = read_events(state_dir / "runs" / run_id / "journal.jsonl")
    except JournalError as error:
        raise EngineError(f"run {run_id!r} cannot be resumed: {error}") from None
    if not events or events[0]["event"] != "run-start":
        raise EngineError(f"run {run_id!r} cannot be resumed: its journal does not start with run-start")
    return Path(str(events[0]["workflow"]))


@dataclass
class _Progress:
    """What the journal of an earlier run says: the ticket outcomes, and the one step that was open."""

    built: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    open_ticket: str | None = None
    events: list[dict[str, object]] = field(default_factory=list)


def _restore(events: list[dict[str, object]], run_id: str) -> _Progress:
    """Rebuild the progress of a run from its journal. Raise EngineError when the run has ended."""
    progress = _Progress(events=events)
    started: str | None = None
    for event in events:
        kind, ticket = event["event"], str(event.get("ticket"))
        if kind == "run-end":
            raise EngineError(f"run {run_id!r} has ended with the result {event['result']!r}; it cannot be resumed")
        if kind == "step-start":
            started = ticket
        elif kind == "step-end":
            # The newest step-end of a ticket wins: the verification at the end of an
            # `economy` chain can fail a ticket that was built.
            if ticket in progress.built:
                progress.built.remove(ticket)
            if event["state"] == BUILT:
                progress.built.append(ticket)
            else:
                if ticket not in progress.failed:
                    progress.failed.append(ticket)
                progress.failures[ticket] = str(event["reason"])
            if started == ticket:
                started = None
        elif kind == "skip":
            progress.skipped[ticket] = str(event["reason"])
    progress.open_ticket = started
    return progress


def _check_same_run(workflow: Workflow, workflow_path: Path, run_start: dict[str, object], run_id: str) -> None:
    """Refuse a resume when the workflow is not the one that the run started from. The message names the field."""
    now: dict[str, object] = {
        "mode": workflow.mode, "adapter": workflow.adapter, "base_branch": workflow.base_branch,
        "run_branch": workflow.run_branch, "tickets": [t.id for t in plan_order(workflow)],
    }
    for name, value in now.items():
        if run_start[name] != value:
            raise EngineError(f"workflow {workflow_path} differs from run {run_id}: {name} was {run_start[name]!r}, is now {value!r}")


def run_workflow(
    workflow: Workflow, workflow_path: Path, repo: Path, tiers: Tiers, adapter: Adapter, vcs: VersionControl, *,
    resume: str | None = None, break_lock: bool = False,
) -> RunResult:
    """Build the tickets of a workflow in dependency order, through `adapter`. Return the result of the run.

    The caller picks the adapter that the workflow names, and the version-control
    backend `vcs`. The engine knows the ports only, never an adapter module. The
    run branch, the worktrees and the file-watcher setting come from `vcs`.

    With `resume`, the run id of an earlier run, go on with that run: build the
    tickets that are not complete, and append to its journal.

    The run holds the lock of its run branch until it ends. Raise EngineError,
    which names the holder, when another run holds the lock. `break_lock`
    removes a lock whose process no longer exists, and never one whose process
    is alive.
    """
    repo, state_dir = state_directory(repo, vcs)
    roles = {role: _role_model(workflow, tiers, adapter.tier_column, role) for role in ("specialist", "verifier")}
    run_id = resume if resume is not None else _new_run_id()
    try:
        # A run branch in use is reported first. A refused run creates nothing, so the lock is taken last.
        check_free(state_dir, workflow.run_branch, break_stale=break_lock)
        if resume is None:
            _check_branches(workflow, repo, vcs)
        with run_lock(state_dir, workflow.run_branch, run_id, break_stale=break_lock) as broken:
            return _execute(workflow, workflow_path, adapter, vcs, roles, repo, state_dir, run_id, resume is not None, broken)
    except LockError as error:
        raise EngineError(str(error)) from None


def _execute(
    workflow: Workflow, workflow_path: Path, adapter: Adapter, vcs: VersionControl, roles: dict[str, tuple[str, str]],
    repo: Path, state_dir: Path, run_id: str, resuming: bool, broken: LockHolder | None,
) -> RunResult:
    order = plan_order(workflow)
    if not resuming:
        with _vcs_errors():
            vcs.create_branch(repo, workflow.run_branch, workflow.base_branch)
        journal = Journal(state_dir / "runs" / run_id / "journal.jsonl")
        journal.append(
            "run-start", run_id=run_id, workflow=str(workflow_path.resolve()), mode=workflow.mode, adapter=workflow.adapter,
            base_branch=workflow.base_branch, run_branch=workflow.run_branch, tickets=[t.id for t in order],
        )
        progress = _Progress()
    else:
        journal, events = _journal_events(state_dir, run_id)
        progress = _restore(events, run_id)
        _check_same_run(workflow, workflow_path, events[0], run_id)
        with _vcs_errors():
            run_branch_exists = vcs.branch_exists(repo, workflow.run_branch)
        if not run_branch_exists:
            raise EngineError(f"run branch {workflow.run_branch!r} of run {run_id!r} is not a branch of the repository {repo}")
        journal.append(
            "resume", run_id=run_id, built=list(progress.built), failed=list(progress.failed),
            skipped=list(progress.skipped), open=progress.open_ticket,
        )
    if broken is not None:
        journal.append("lock-broken", run_id=broken.run_id, pid=broken.pid)
    built, failed, failures, skipped = progress.built, progress.failed, progress.failures, progress.skipped
    complete = {*built, *failed, *skipped}
    ended_early = False
    for position, ticket in enumerate(order):
        if ticket.id in complete:
            continue
        blockers = [blocker for blocker in ticket.blocked_by if blocker not in built]
        if blockers:
            reason = "blocked by " + "; ".join(
                f"{blocker}, which {'failed' if blocker in failed else 'was skipped'}" for blocker in blockers
            )
            skipped[ticket.id] = reason
            journal.append("skip", ticket=ticket.id, blockers=blockers, reason=reason)
            continue
        prior = [e for e in progress.events if e.get("ticket") == ticket.id] if ticket.id == progress.open_ticket else None
        try:
            reason = _build_ticket(
                workflow, ticket, adapter, vcs, roles, repo, state_dir, run_id, journal, prior, _start_ref(workflow, order, built)
            )
        except Exception as error:  # noqa: BLE001 - the journal records it and the command reports it
            # An exception that the engine did not plan for leaves the state of the
            # step unknown, so the run ends here. The journal says why. A verifier
            # that changed the real worktree also ends the run: the state of the
            # worktree can no longer be trusted.
            halted, ended_early = isinstance(error, InvariantViolation), True
            reason = f"invariant violation: {error}" if halted else f"crashed: {type(error).__name__}: {error}"
            journal.append("step-end", ticket=ticket.id, state=FAILED, reason=reason)
            failed.append(ticket.id)
            failures[ticket.id] = reason
            for unreached in order[position + 1 :]:
                skipped[unreached.id] = f"the run ended after ticket {ticket.id} {'halted the run' if halted else 'crashed'}"
                journal.append("skip", ticket=unreached.id, blockers=[], reason=skipped[unreached.id])
            break
        journal.append("step-end", ticket=ticket.id, state=FAILED if reason else BUILT, reason=reason)
        if reason:
            failed.append(ticket.id)
            failures[ticket.id] = reason
        else:
            built.append(ticket.id)
    unmapped: list[str] = []
    if workflow.mode == "economy" and built and not ended_early:
        chain = _ChainRun(workflow, order, adapter, roles, repo, state_dir, run_id, journal, built, failed, failures)
        unmapped = chain.verify()
    journal.append(
        "run-end", result=BUILT if not failed else FAILED, built=built, failed=failed, skipped=list(skipped)
    )
    return RunResult(run_id, journal.path, built, failed, failures, skipped, unmapped)


def _start_ref(workflow: Workflow, order: list[Ticket], built: list[str]) -> str:
    """The branch that the next ticket starts from.

    In `assure` mode every ticket starts from the base branch. In `economy`
    mode the tickets form a chain: a ticket starts from the branch of the last
    ticket before it, in the order of the plan, that was built. A ticket that
    failed or was skipped is not part of the chain.
    """
    if workflow.mode == "economy":
        previous = [ticket for ticket in order if ticket.id in built]
        if previous:
            return _ticket_branch(workflow, previous[-1])
    return workflow.base_branch


def _specialist_done(prior: list[dict[str, object]]) -> bool:
    """True when the journal shows a valid specialist report after the last time the specialist was spawned."""
    spawned = valid = -1
    for index, event in enumerate(prior):
        if event["event"] in ("step-start", "continuation"):
            spawned = index
        elif event["event"] == "report-validation" and event["valid"]:
            valid = index
    return valid > spawned


# The limit, in seconds, for the command that stops the fsmonitor daemon of a directory.
_FSMONITOR_STOP_TIMEOUT: int = 30


def _stop_fsmonitor(path: Path) -> None:
    """Stop the fsmonitor daemon of the repository at `path`, before the directory goes.

    A daemon outlives its directory. Git exits with an error when no daemon
    runs, or when the directory is not a repository, and both are fine here. A
    failure to stop never fails the run: this is a clean-up, and it is quiet.
    """
    try:
        subprocess.run(
            ["git", "-C", str(path), "fsmonitor--daemon", "stop"],
            capture_output=True, text=True, timeout=_FSMONITOR_STOP_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        pass


def _rebase_in_progress(worktree: Path) -> bool:
    """Tell whether a rebase is in progress in the worktree. `git rebase --abort` fails when none is."""
    return any(
        (worktree / _git(worktree, "rev-parse", "--git-path", name)).exists() for name in ("rebase-merge", "rebase-apply")
    )


def _prepare_worktree(vcs: VersionControl, repo: Path, worktree: Path, branch: str, base: str, hooks_dir: Path) -> None:
    """Make the worktree of a ticket through the port, or reuse the one that is there, and install the push guard in it.

    The port keeps the file watcher off for the worktree (`core.fsmonitor=false`
    in its own configuration), whatever the global setting of the user is.

    A worktree that a killed run left is reused as it is, except that a rebase
    which the kill stopped is aborted. A branch without a worktree gets a new
    worktree. Raise EngineError when the worktree holds another branch.
    """
    with _vcs_errors():
        vcs.ensure_worktree(repo, worktree, branch, base)
    try:
        install_push_guard(repo, worktree, hooks_dir)
    except GuardError as error:
        raise EngineError(str(error)) from None


def _build_ticket(
    workflow: Workflow, ticket: Ticket, adapter: Adapter, vcs: VersionControl, roles: dict[str, tuple[str, str]],
    repo: Path, state_dir: Path, run_id: str, journal: Journal, prior: list[dict[str, object]] | None = None,
    start_ref: str | None = None,
) -> str | None:
    """Build one ticket. Return None when it is built, or the reason it failed.

    The branch of the ticket starts from `start_ref`, which is the base branch
    unless the mode chains the tickets (see `_start_ref`).

    In `assure` mode a ticket is built only when its verifier accepts it.

    `prior` holds the journal events of this ticket when the step was open in a
    run that stopped. The step then goes on in its own worktree.
    """
    tier, model = roles["specialist"]
    stack = workflow.stacks[ticket.stack]
    branch = _ticket_branch(workflow, ticket)
    worktree = state_dir / "worktrees" / run_id / ticket.id
    hooks_dir = state_dir / "hooks" / run_id / ticket.id
    report_path = state_dir / "runs" / run_id / "reports" / f"{ticket.id}.specialist.json"
    start_ref = start_ref or workflow.base_branch
    if prior is None:
        with _vcs_errors():
            base_commit = vcs.commit_of(repo, start_ref)
        _prepare_worktree(vcs, repo, worktree, branch, start_ref, hooks_dir)
        journal.append(
            "step-start", ticket=ticket.id, stack=ticket.stack, branch=branch, worktree=str(worktree),
            base_commit=base_commit, agent=stack.specialist, tier=tier, model=model,
        )
        specialist_done, continuations, verify_rounds = False, 0, 0
    else:
        advances = [e for e in prior if e["event"] == "run-branch-advance"]
        if advances:
            # The run branch holds the commit already. Only the step-end is missing.
            landed = str(advances[-1]["to_commit"])
            if subprocess.run(
                ["git", "-C", str(repo), "merge-base", "--is-ancestor", landed, f"refs/heads/{workflow.run_branch}"],
                capture_output=True,
            ).returncode != 0:
                raise EngineError(f"run branch {workflow.run_branch} does not hold commit {landed}, which the journal says it advanced to")
            return None
        base_commit = str(next(e for e in prior if e["event"] == "step-start")["base_commit"])
        _prepare_worktree(vcs, repo, worktree, branch, start_ref, hooks_dir)
        specialist_done = _specialist_done(prior)
        continuations = sum(1 for e in prior if e["event"] == "continuation")
        verify_rounds = sum(1 for e in prior if e["event"] == "verify-start")
    # The commits of a ticket are the commits beyond the point where its branch started. In a chain that
    # point is the tip of the previous ticket, so the commits of earlier tickets are not counted.
    since = base_commit if workflow.mode == "economy" else workflow.base_branch
    reason = _build_with_continuations(
        workflow, ticket, adapter, model, repo, worktree, branch, report_path, journal, since,
        spawned=specialist_done, continuations=continuations, interrupted=prior is not None,
    )
    if reason:
        return reason
    if workflow.mode == "assure":
        step = _Step(workflow, ticket, adapter, roles, repo, state_dir, run_id, journal)
        reason = _rebase_onto_run_branch(step)
        return reason if reason else _verify_ticket(step, verify_rounds)
    return None


def _build_with_continuations(
    workflow: Workflow, ticket: Ticket, adapter: Adapter, model: str, repo: Path, worktree: Path, branch: str,
    report_path: Path, journal: Journal, since: str, spawned: bool = False, continuations: int = 0, interrupted: bool = False,
) -> str | None:
    """Run the specialist, check its work, and continue it in the same worktree when the work is not done.

    `since` is the commit or branch where the branch of the ticket started: the commits beyond it
    are the work of this ticket.

    A resume sets `spawned` when the journal shows that the specialist finished
    with a valid report, so the first pass does not spawn it again. It sets
    `continuations` to the number the journal holds, so the limit counts across
    the stop. It sets `interrupted` for a step that a stop left open: when the
    branch holds commits already, the first prompt is a continuation brief.

    The specialist continues when it ends capped or failed. Return None when
    the report is valid and the branch holds green work, or the reason the step
    fails. A report that is missing, is invalid, or does not say committed
    fails the step with no continuation. After `CONTINUATION_LIMIT` continuations
    the step fails, and the journal records the count.
    """
    stack = workflow.stacks[ticket.stack]
    limit = CONTINUATION_LIMIT[workflow.mode]
    prompt = specialist_brief(ticket.id, ticket.text, worktree, stack.hotspots, stack.gates, report_path)
    if interrupted and not spawned:
        so_far = _git(worktree, "log", "--format=%H %s", f"{since}..HEAD").splitlines()
        if so_far:
            prompt = continuation_brief(
                ticket.id, ticket.text, worktree, stack.hotspots, stack.gates, report_path, so_far,
                "the run was interrupted", resumed=False,
            )
    resume_session: str | None = None
    while True:
        if spawned:
            spawned, result = False, None
        else:
            result = adapter.run(AdapterRequest(stack.specialist, model, prompt, worktree, report_path, resume_session))
            journal.append(
                "adapter-result", ticket=ticket.id, exit_status=result.exit_status, end_state=result.end_state,
                session_id=result.session_id, event_stream=str(result.event_stream),
            )
        if result is not None and (result.end_state != harness.FINISHED or result.exit_status != 0):
            trigger = harness.CAPPED if result.end_state == harness.CAPPED else harness.FAILED
            reason = f"the specialist ended {result.end_state} with exit status {result.exit_status}"
            gate_output = None
        else:
            try:
                report = read_specialist_report(report_path, ticket.id)
            except ReportError as error:
                journal.append(
                    "report-validation", ticket=ticket.id, valid=False, path=str(report_path), reason=str(error),
                    status=None, blocked_reason=None,
                )
                return str(error)
            journal.append(
                "report-validation", ticket=ticket.id, valid=True, path=str(report_path), reason=None,
                status=report.status, blocked_reason=report.blocked_reason,
            )
            if report.status != "committed":
                detail = f": {report.blocked_reason}" if report.blocked_reason else ""
                return f"the specialist reported status {report.status!r}{detail}"
            if _git(repo, "rev-list", "--count", f"{since}..{branch}") == "0":
                return f"branch {branch} holds no commit beyond {since}, though the report says committed"
            stop = _hotspot_stop(journal, ticket.id, stack, worktree, since, True, PHASE_BUILD, 0)
            if stop:
                return stop
            outcomes = [(gate, *_run_gate_with_output(gate, ticket.id, worktree, journal, 0, PHASE_BUILD)) for gate in stack.gates]
            red = [(gate, output) for gate, green, output in outcomes if not green]
            if not red:
                return None
            trigger = "gates-red"
            reason = "gates red: " + "; ".join(gate for gate, _ in red)
            gate_output = "\n".join(f"$ {gate}\n{output.rstrip(chr(10))}" for gate, output in red)
        if continuations == limit:
            journal.append("continuation-limit", ticket=ticket.id, count=continuations, limit=limit, trigger=trigger)
            return f"{reason}; the continuation limit of {limit} is reached"
        continuations += 1
        # A harness that gave no session id cannot resume, so a new agent continues.
        resume_session = result.session_id if result is not None and adapter.supports_resume else None
        commits = _git(worktree, "log", "--format=%H %s", f"{since}..HEAD").splitlines()
        journal.append(
            "continuation", ticket=ticket.id, count=continuations, limit=limit, trigger=trigger,
            mode="resume" if resume_session is not None else "brief", resume_session=resume_session, commits=commits, reason=reason,
        )
        prompt = continuation_brief(
            ticket.id, ticket.text, worktree, stack.hotspots, stack.gates, report_path, commits, reason,
            resumed=resume_session is not None, gate_output=gate_output,
        )


def _hotspot_stop(
    journal: Journal, ticket: str, stack: Stack, worktree: Path, since: str, merge_base: bool, phase: str, round_number: int
) -> str | None:
    """Compare the paths that the specialist changed since `since` with the hotspot patterns of the stack.

    Return None when none matches, or the reason the step fails. A match is
    recorded as `hotspot-finding`. The caller stops the step before any gate
    and any verifier runs.
    """
    matches = hotspot_matches(changed_paths(worktree, since, merge_base=merge_base), stack.hotspots)
    if not matches:
        return None
    journal.append(
        "hotspot-finding", ticket=ticket, phase=phase, round=round_number,
        matches=[{"path": m.path, "pattern": m.pattern} for m in matches],
    )
    return "hotspot finding: " + "; ".join(f"{m.path} matches the hotspot {m.pattern!r}" for m in matches)


def _prepare_copy(repo: Path, base: str, branch: str, copy: Path) -> None:
    """Make a self-contained clone at `copy` that holds the base and the branch, with the branch checked out.

    The clone has its own git directory and no remote, so the verifier can break
    it, and cannot reach the real repository through it. The clone sets
    `core.fsmonitor=false` in its own configuration, so that no fsmonitor daemon
    starts for it, whatever the global setting of the user is.

    The clone starts on the branch that the HEAD of the repository names, and
    that branch can be the run branch. So the clone checks out the ticket branch
    first, which frees the other names, and then sets the base branch by force.
    """
    _git(repo, "clone", "-q", "-c", "core.fsmonitor=false", "--no-checkout", str(repo), str(copy))
    _git(copy, "checkout", "-q", "-B", branch, f"origin/{branch}")
    _git(copy, "branch", "-f", base, f"origin/{base}")
    _git(copy, "remote", "remove", "origin")


@dataclass(frozen=True)
class _Step:
    """The state that the verifier step and the fix-up rounds of one ticket share."""

    workflow: Workflow
    ticket: Ticket
    adapter: Adapter
    roles: dict[str, tuple[str, str]]
    repo: Path
    state_dir: Path
    run_id: str
    journal: Journal
    # A step that verifies the chain of a stack (see `_ChainRun`) works on the branch and in the worktree of
    # the ticket at the tip of the chain, `anchor`, and verifies the tickets in `segments`. The `ticket` of such
    # a step is made for the stack: it holds the id of the last ticket of the stack and the text of all of them.
    anchor: Ticket | None = None
    segments: tuple[ChainSegment, ...] | None = None

    @property
    def stack(self) -> Stack:
        return self.workflow.stacks[self.ticket.stack]

    @property
    def branch(self) -> str:
        return _ticket_branch(self.workflow, self.anchor or self.ticket)

    @property
    def worktree(self) -> Path:
        return self.state_dir / "worktrees" / self.run_id / (self.anchor or self.ticket).id

    @property
    def chain_fields(self) -> dict[str, object]:
        """The journal fields that name the stack and the tickets of a chain verification. Empty for one ticket."""
        if self.segments is None:
            return {}
        return {"stack": self.ticket.stack, "tickets": [segment.ticket for segment in self.segments]}

    @property
    def run_dir(self) -> Path:
        return self.state_dir / "runs" / self.run_id


def _rebase_onto_run_branch(step: _Step) -> str | None:
    """Rebase the ticket branch onto the run branch, then run the gates again on the rebased tree.

    Return None when the branch is rebased and the gates are green, or the
    reason the step fails. A rebase that stops is aborted, so the branch and its
    worktree keep the state from before the rebase. A rebase that git refuses to
    start, or one that ended with a failed hook, leaves nothing to abort.
    """
    ticket, run_branch = step.ticket, step.workflow.run_branch
    onto = _git(step.repo, "rev-parse", f"refs/heads/{run_branch}")
    before = _git(step.worktree, "rev-parse", "HEAD")
    rebase = subprocess.run(["git", "-C", str(step.worktree), "rebase", run_branch], capture_output=True, text=True)
    if rebase.returncode != 0:
        files = _git(step.worktree, "diff", "--name-only", "--diff-filter=U").splitlines()
        if _rebase_in_progress(step.worktree):
            _git(step.worktree, "rebase", "--abort")
        step.journal.append(
            "rebase", ticket=ticket.id, result="conflict" if files else "failed", onto_commit=onto,
            from_commit=before, to_commit=None, files=files,
        )
        if files:
            return f"rebase onto {run_branch} stopped with a conflict in {', '.join(files)}"
        return f"rebase onto {run_branch} failed: {rebase.stderr.strip() or rebase.returncode}"
    after = _git(step.worktree, "rev-parse", "HEAD")
    step.journal.append(
        "rebase", ticket=ticket.id, result="rebased", onto_commit=onto, from_commit=before, to_commit=after, files=[]
    )
    if _git(step.repo, "rev-list", "--count", f"{run_branch}..{step.branch}") == "0":
        return (
            f"branch {step.branch} holds no commit beyond {run_branch} after the rebase; "
            f"{run_branch} holds its work already"
        )
    red = [gate for gate in step.stack.gates if not _run_gate(gate, ticket.id, step.worktree, step.journal, 0, PHASE_REBASE)]
    if red:
        return "gates red: " + "; ".join(red) + f" (after the rebase onto {run_branch})"
    return None


def _verify_ticket(step: _Step, first_round: int = 0) -> str | None:
    """Verify the built branch of a ticket blind. Return None on ACCEPT, or the reason the step fails.

    On ACCEPT the run branch moves to the commit that the verifier saw.
    """
    try:
        return _advance_run_branch(step, _accepted_commit(step, first_round))
    except _StepFailed as failed:
        return str(failed)


def _accepted_commit(step: _Step, first_round: int = 0) -> str:
    """Verify the branch of the step blind, and return the commit that a verifier accepted.

    A REJECT starts a fix-up round, up to `FIXUP_ROUND_LIMIT` of the mode.
    Each round, and the first pass, spawns a fresh verifier. Raise `_Rejected`
    after the last round, and `_StepFailed` for any other cause.

    A resume sets `first_round` to the number of verifier runs that the journal
    holds. The first pass of the resume is a full verification at that round
    number, and the limit counts across the stop.
    """
    limit = FIXUP_ROUND_LIMIT[step.workflow.mode]
    if first_round > limit:
        raise _StepFailed(f"the {first_round} verifier runs of the stopped run use up the {limit} fix-up rounds")
    rejected = ""
    report: VerifierReport | None = None
    for round_number in range(first_round, limit + 1):
        fixup_body = None
        if report is not None:
            fixup_body = _fixup_round(step, round_number, rejected, report.findings)
        verified = _git(step.repo, "rev-parse", f"refs/heads/{step.branch}")
        report = _verify_round(step, round_number, fixup_body)
        if report.verdict == "ACCEPT":
            return verified
        rejected = verified
    assert report is not None
    rounds = f"{limit} fix-up round{'' if limit == 1 else 's'}"
    raise _Rejected(f"the verifier rejected the branch after {rounds}: " + "; ".join(report.findings), report.findings)


def _fixup_round(step: _Step, round_number: int, rejected: str, findings: list[str]) -> str:
    """Run one fix-up round and return the scoped brief for the fresh verifier.

    The engine writes the findings to a file and sends the specialist a fix-up
    brief in the same worktree. The fix must be a new commit on top of the
    rejected commit, which the brief generator checks. The engine then runs the
    gates itself.
    """
    ticket = step.ticket
    tier, model = step.roles["specialist"]
    findings_path = step.run_dir / "findings" / f"{ticket.id}.fixup-{round_number}.md"
    findings_path.parent.mkdir(parents=True, exist_ok=True)
    findings_path.write_text("".join(f"- {finding}\n" for finding in findings))
    findings_text = findings_path.read_text()
    step.journal.append(
        "fixup-start", ticket=ticket.id, round=round_number, rejected_commit=rejected, findings_file=str(findings_path),
        agent=step.stack.specialist, tier=tier, model=model,
    )
    report_path = step.run_dir / "reports" / f"{ticket.id}.fixup-{round_number}.specialist.json"
    prompt = specialist_brief(
        ticket.id, ticket.text, step.worktree, step.stack.hotspots, step.stack.gates, report_path, rejected, findings_text
    )
    result = step.adapter.run(AdapterRequest(step.stack.specialist, model, prompt, step.worktree, report_path))
    step.journal.append(
        "fixup-result", ticket=ticket.id, round=round_number, exit_status=result.exit_status, end_state=result.end_state,
        session_id=result.session_id, event_stream=str(result.event_stream),
    )
    if result.end_state != harness.FINISHED or result.exit_status != 0:
        raise _StepFailed(f"the fix-up specialist ended {result.end_state} with exit status {result.exit_status}")
    try:
        report = read_specialist_report(report_path, ticket.id)
    except ReportError as error:
        step.journal.append(
            "fixup-report", ticket=ticket.id, round=round_number, valid=False, path=str(report_path), reason=str(error),
            status=None, blocked_reason=None,
        )
        raise _StepFailed(str(error)) from None
    step.journal.append(
        "fixup-report", ticket=ticket.id, round=round_number, valid=True, path=str(report_path), reason=None,
        status=report.status, blocked_reason=report.blocked_reason,
    )
    if report.status != "committed":
        detail = f": {report.blocked_reason}" if report.blocked_reason else ""
        raise _StepFailed(f"the fix-up specialist reported status {report.status!r}{detail}")
    stop = _hotspot_stop(
        step.journal, ticket.id, step.stack, step.worktree, rejected, False, PHASE_FIXUP, round_number
    )
    if stop:
        raise _StepFailed(stop)
    try:
        brief = fixup_brief(step.repo, step.branch, rejected, findings_text, gate_commands=step.stack.gates)
    except BriefError as error:
        step.journal.append("fixup-refused", ticket=ticket.id, round=round_number, reason=str(error))
        raise _StepFailed(f"the fix-up of round {round_number} is refused: {error}") from None
    red = [
        gate for gate in step.stack.gates
        if not _run_gate(gate, ticket.id, step.worktree, step.journal, round_number, PHASE_FIXUP)
    ]
    if red:
        raise _StepFailed("gates red: " + "; ".join(red))
    return brief


def _verify_round(step: _Step, round_number: int, fixup_body: str | None) -> VerifierReport:
    """Run one fresh verifier in a new temporary copy and return its valid verdict report.

    Round 0 is the full pass. A later round gets the fix-up brief in `fixup_body`.
    The verifier gets the task or the findings, and the diff or the delta, and
    the gates. It never gets the report of the specialist.
    """
    ticket = step.ticket
    tier, model = step.roles["verifier"]
    mode = "full" if fixup_body is None else "fix-up"
    verified = _git(step.repo, "rev-parse", f"refs/heads/{step.branch}")
    evidence = step.run_dir / "verifier" / ticket.id
    if round_number:
        evidence = evidence / f"fixup-{round_number}"
    scratch = Path(tempfile.mkdtemp(prefix="delegate-verify-"))
    try:
        copy = scratch / "copy"
        report_path = scratch / "verdict.json"
        try:
            _prepare_copy(step.repo, step.workflow.run_branch, step.branch, copy)
            if step.segments is not None:
                # A chain: the brief holds the tickets of the stack, each with its own diff, and the finding labels.
                first_pass = chain_brief(copy, step.segments, step.stack.gates) if fixup_body is None else fixup_body + finding_labels_section()
                prompt = first_pass + verifier_run_sections(copy, report_path, mode)
            elif fixup_body is None:
                prompt = verifier_run_brief(
                    copy, step.branch, step.workflow.run_branch, ticket.text, step.stack.gates, report_path
                )
            else:
                prompt = fixup_body + verifier_run_sections(copy, report_path, mode)
        except (EngineError, BriefError) as error:
            raise _StepFailed(f"the verifier could not start: {error}") from None
        step.journal.append(
            "verify-start", ticket=ticket.id, round=round_number, agent=step.stack.verifier, tier=tier, model=model,
            commit=verified, copy=str(copy), **step.chain_fields,
        )
        before = snapshot_worktree(step.worktree)
        result = step.adapter.run(AdapterRequest(step.stack.verifier, model, prompt, copy, report_path))
        # The check runs at once, before the evidence is moved; the journal and the halt come after it.
        violation: InvariantViolation | None = None
        try:
            check_worktree_unchanged(before)
        except InvariantViolation as found:
            violation = found
        # The copy goes; the verdict report and the event stream stay as evidence of the run.
        evidence.mkdir(parents=True)
        for entry in scratch.iterdir():
            if entry != copy:
                shutil.move(str(entry), evidence / entry.name)
    finally:
        _stop_fsmonitor(scratch / "copy")
        shutil.rmtree(scratch, ignore_errors=True)
    report_path = evidence / report_path.name
    stream = result.event_stream
    if stream.is_relative_to(scratch):
        stream = evidence / stream.relative_to(scratch)
    step.journal.append(
        "verify-result", ticket=ticket.id, round=round_number, exit_status=result.exit_status, end_state=result.end_state,
        session_id=result.session_id, event_stream=str(stream),
    )
    if violation is not None:
        step.journal.append(
            "invariant-violation", ticket=ticket.id, round=round_number, worktree=str(violation.worktree),
            head_before=violation.head_before, head_after=violation.head_after, changes=violation.changes,
        )
        raise violation
    if result.end_state != harness.FINISHED or result.exit_status != 0:
        raise _StepFailed(f"the verifier ended {result.end_state} with exit status {result.exit_status}")
    try:
        report = read_verifier_report(report_path, mode)
    except ReportError as error:
        step.journal.append(
            "verify-report", ticket=ticket.id, round=round_number, valid=False, path=str(report_path), reason=str(error)
        )
        raise _StepFailed(str(error)) from None
    step.journal.append(
        "verify-report", ticket=ticket.id, round=round_number, valid=True, path=str(report_path), reason=None
    )
    chain_fields = step.chain_fields
    if step.segments is not None:
        mapped, unmapped = map_findings(report.findings, chain_fields["tickets"])  # type: ignore[arg-type]
        chain_fields = {**chain_fields, "mapped": mapped, "unmapped": unmapped}
    step.journal.append(
        "verdict", ticket=ticket.id, round=round_number, mode=report.mode, verdict=report.verdict,
        findings=report.findings, unverified=report.unverified, report=str(report_path), **chain_fields,
    )
    return report


def _advance_run_branch(step: _Step, verified: str) -> str | None:
    """Move the run branch to the commit that the verifier accepted, by fast-forward only.

    Return None when it moved, or the reason the step fails.
    """
    run_branch = step.workflow.run_branch
    run_tip = _git(step.repo, "rev-parse", f"refs/heads/{run_branch}")
    if subprocess.run(
        ["git", "-C", str(step.repo), "merge-base", "--is-ancestor", run_tip, verified], capture_output=True
    ).returncode != 0:
        return (
            f"run branch {run_branch} cannot fast-forward to {step.branch}: "
            f"it holds a commit that {step.branch} does not hold"
        )
    _git(step.repo, "update-ref", f"refs/heads/{run_branch}", verified, run_tip)
    step.journal.append(
        "run-branch-advance", ticket=step.ticket.id, run_branch=run_branch, from_commit=run_tip, to_commit=verified
    )
    return None


class _ChainRun:
    """The verification at the end of an `economy` chain.

    The chain holds the tickets that were built, in the order of the plan. Each
    ticket branch starts from the tip of the previous one, so the last branch
    holds all the work. For each stack of the chain, in the order of its first
    ticket, the engine spawns one verifier over the commits of the tickets of
    that stack: the brief holds a diff for each of those tickets, and none for a
    ticket of another stack. The verifier works in a copy of the tip.

    A REJECT gets one fix-up round (`FIXUP_ROUND_LIMIT`): a new commit on the
    chain tip, in the worktree of the last ticket, and a fresh scoped verifier. A
    second REJECT fails the stack. A finding maps to a ticket by its label (see
    `map_findings`). The tickets that the findings name fail with their
    findings. When no finding names a ticket, every ticket of the stack fails.
    The stacks that the engine did not verify yet fail too: their tickets are
    not verified, so no one may rely on them. The run branch then does not move.

    When every stack is accepted, the engine moves the run branch to the chain
    tip by fast-forward.
    """

    def __init__(
        self, workflow: Workflow, order: list[Ticket], adapter: Adapter, roles: dict[str, tuple[str, str]], repo: Path,
        state_dir: Path, run_id: str, journal: Journal, built: list[str], failed: list[str], failures: dict[str, str],
    ) -> None:
        self.workflow, self.adapter, self.roles, self.repo = workflow, adapter, roles, repo
        self.state_dir, self.run_id, self.journal = state_dir, run_id, journal
        self.chain = [ticket for ticket in order if ticket.id in built]
        self.built, self.failed, self.failures = built, failed, failures

    def verify(self) -> list[str]:
        """Verify every stack of the chain and move the run branch. Return the findings that map to no ticket."""
        try:
            return self._verify_stacks()
        except Exception as error:  # noqa: BLE001 - the journal records it and the command reports it
            halted = isinstance(error, InvariantViolation)
            reason = f"invariant violation: {error}" if halted else f"crashed: {type(error).__name__}: {error}"
            self._fail({ticket.id: reason for ticket in self.chain if ticket.id in self.built})
            return []

    def _fail(self, reasons: dict[str, str]) -> None:
        """Fail the built tickets in `reasons`. The newest `step-end` of a ticket wins, so the journal keeps both."""
        for ticket_id, reason in reasons.items():
            self.journal.append("step-end", ticket=ticket_id, state=FAILED, reason=reason)
            self.built.remove(ticket_id)
            self.failed.append(ticket_id)
            self.failures[ticket_id] = reason

    def _segments(self) -> dict[str, ChainSegment]:
        """For each ticket of the chain, the commits that it added: from where its branch started to its tip now."""
        events = read_events(self.journal.path)
        segments: dict[str, ChainSegment] = {}
        for ticket in self.chain:
            base = [str(e["base_commit"]) for e in events if e["event"] == "step-start" and e["ticket"] == ticket.id][-1]
            tip = _git(self.repo, "rev-parse", f"refs/heads/{_ticket_branch(self.workflow, ticket)}")
            segments[ticket.id] = ChainSegment(ticket.id, ticket.text, base, tip)
        return segments

    def _verify_stacks(self) -> list[str]:
        events = read_events(self.journal.path)
        if any(e["event"] == "run-branch-advance" for e in events):
            return []  # a resume after the run branch moved: nothing is left to verify
        tip_ticket = self.chain[-1]
        segments = self._segments()
        stacks = list(dict.fromkeys(ticket.stack for ticket in self.chain))
        last: _Step | None = None
        for position, name in enumerate(stacks):
            tickets = [ticket for ticket in self.chain if ticket.stack == name]
            verdicts = [e["verdict"] for e in events if e["event"] == "verdict" and e.get("stack") == name]
            step = _Step(
                self.workflow, Ticket(tickets[-1].id, _chain_text(tickets), name, []), self.adapter, self.roles, self.repo,
                self.state_dir, self.run_id, self.journal, tip_ticket, tuple(segments[ticket.id] for ticket in tickets),
            )
            last = step
            if verdicts and verdicts[-1] == "ACCEPT":
                continue  # a resume: an earlier run accepted this stack already
            first_round = sum(1 for e in events if e["event"] == "verify-start" and e.get("stack") == name)
            try:
                _accepted_commit(step, first_round)
            except _StepFailed as failed:
                return self._reject(failed, tickets, stacks[position + 1 :])
        assert last is not None
        reason = _advance_run_branch(last, _git(self.repo, "rev-parse", f"refs/heads/{last.branch}"))
        if reason:
            self._fail({ticket.id: reason for ticket in self.chain})
        return []

    def _reject(self, failed: _StepFailed, tickets: list[Ticket], unverified: list[str]) -> list[str]:
        """Fail the tickets of a stack that did not pass, and the tickets of the stacks after it. Return the unmapped findings."""
        mapped: dict[str, list[str]] = {}
        unmapped: list[str] = []
        if isinstance(failed, _Rejected):
            mapped, unmapped = map_findings(failed.findings, [ticket.id for ticket in tickets])
        limit = FIXUP_ROUND_LIMIT[self.workflow.mode]
        rounds = f"{limit} fix-up round{'' if limit == 1 else 's'}"
        reasons = {
            ticket_id: f"the verifier rejected the chain after {rounds}: " + "; ".join(findings)
            for ticket_id, findings in mapped.items()
        }
        if not reasons:
            reasons = {ticket.id: str(failed) for ticket in tickets}
        stack = tickets[0].stack
        for ticket in self.chain:
            if ticket.stack in unverified:
                reasons[ticket.id] = f"not verified: the chain ended when the verification of the stack {stack!r} failed"
        self._fail(reasons)
        return unmapped


def _chain_text(tickets: list[Ticket]) -> str:
    """The text of a stack's tickets for the fix-up specialist: the text of one ticket, or each ticket with its id."""
    if len(tickets) == 1:
        return tickets[0].text
    return "\n\n".join(f"Ticket {ticket.id}: {ticket.text.rstrip(chr(10))}" for ticket in tickets)


def _run_gate(command: str, ticket: str, worktree: Path, journal: Journal, round_number: int, phase: str) -> bool:
    """Run one gate command in the worktree, record the result, and return True when it is green.

    `round_number` is 0 for the first build and the fix-up round for a later run.
    `phase` is one of PHASE_BUILD, PHASE_REBASE and PHASE_FIXUP.
    """
    return _run_gate_with_output(command, ticket, worktree, journal, round_number, phase)[0]


def _run_gate_with_output(
    command: str, ticket: str, worktree: Path, journal: Journal, round_number: int, phase: str
) -> tuple[bool, str]:
    """Run one gate command as `_run_gate` does. Return whether it is green, and the tail of its output."""
    r = subprocess.run(["bash", "-c", command], cwd=worktree, capture_output=True, text=True)
    output = (r.stdout + r.stderr)[-GATE_OUTPUT_TAIL_CHARS:]
    journal.append(
        "gate-result", ticket=ticket, round=round_number, phase=phase, command=command, exit_status=r.returncode, green=r.returncode == 0,
        output_tail=output,
    )
    return r.returncode == 0, output
