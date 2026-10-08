"""The workflow engine: build each ticket through a harness adapter.

For each ticket the engine makes a worktree from the base branch, spawns the
stack's specialist through the adapter, checks the specialist's report, runs
the stack's gates itself, and records each event in the journal.

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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import adapters
from .adapters import Adapter, AdapterRequest
from .brief import BriefError, continuation_brief, fixup_brief, specialist_brief, verifier_run_brief, verifier_run_sections
from .journal import Journal, JournalError, read_events
from .lock import LockError, LockHolder, check_free, run_lock
from .reports import ReportError, VerifierReport, read_specialist_report, read_verifier_report
from .tiers import Tiers
from .workflow import Stack, Ticket, Workflow, plan_order

# The tier of the specialist role in each mode, before a workflow override.
SPECIALIST_TIER: dict[str, str] = {"assure": "strong", "economy": "standard"}

# The tier of the verifier role, before a workflow override. It is the same in every mode.
VERIFIER_TIER: str = "verifier"

# The journal keeps this many characters of the end of a gate's output.
GATE_OUTPUT_TAIL_CHARS: int = 4000

# A REJECT in `assure` mode starts at most this many fix-up rounds. Each round
# is one fix-up commit and one fresh verifier. The limit is the same for every ticket.
FIXUP_ROUND_LIMIT: int = 2

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


@dataclass(frozen=True)
class RunResult:
    run_id: str
    journal: Path
    built: list[str]
    failed: list[str]
    failures: dict[str, str]
    skipped: dict[str, str]

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


def state_directory(repo: Path) -> tuple[Path, Path]:
    """The top level of the repository and the directory where the engine keeps its state."""
    top = Path(_git(repo, "rev-parse", "--show-toplevel"))
    return top, (top / _git(top, "rev-parse", "--git-common-dir")).resolve() / "delegate"


def _journal_events(state_dir: Path, run_id: str) -> tuple[Journal, list[dict[str, object]]]:
    """Open the journal of an earlier run for appending. Raise EngineError when the run has none or its journal is not valid."""
    try:
        journal, events = Journal.reopen(state_dir / "runs" / run_id / "journal.jsonl")
    except JournalError as error:
        raise EngineError(f"run {run_id!r} cannot be resumed: {error}") from None
    if not events or events[0]["event"] != "run-start":
        raise EngineError(f"run {run_id!r} cannot be resumed: its journal does not start with run-start")
    return journal, events


def workflow_of_run(repo: Path, run_id: str) -> Path:
    """The path of the workflow file that the run `run_id` started from. Raise EngineError when the run is unknown."""
    _, state_dir = state_directory(repo)
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
            if event["state"] == BUILT:
                progress.built.append(ticket)
            else:
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
    workflow: Workflow, workflow_path: Path, repo: Path, tiers: Tiers, *, resume: str | None = None,
    break_lock: bool = False,
) -> RunResult:
    """Build the tickets of a workflow in dependency order. Return the result of the run.

    With `resume`, the run id of an earlier run, go on with that run: build the
    tickets that are not complete, and append to its journal.

    The run holds the lock of its run branch until it ends. Raise EngineError,
    which names the holder, when another run holds the lock. `break_lock`
    removes a lock whose process no longer exists, and never one whose process
    is alive.
    """
    try:
        adapter = adapters.get(workflow.adapter)
    except KeyError:
        raise EngineError(
            f"adapter {workflow.adapter!r} has no implementation yet; registered: {', '.join(adapters.registered_names()) or 'none'}"
        ) from None
    repo, state_dir = state_directory(repo)
    needed = ("specialist", "verifier") if workflow.mode == "assure" else ("specialist",)
    roles = {role: _role_model(workflow, tiers, adapter.tier_column, role) for role in needed}
    run_id = resume if resume is not None else _new_run_id()
    try:
        # A run branch in use is reported first. A refused run creates nothing, so the lock is taken last.
        check_free(state_dir, workflow.run_branch, break_stale=break_lock)
        if resume is None:
            _check_branches(workflow, repo)
        with run_lock(state_dir, workflow.run_branch, run_id, break_stale=break_lock) as broken:
            return _execute(workflow, workflow_path, adapter, roles, repo, state_dir, run_id, resume is not None, broken)
    except LockError as error:
        raise EngineError(str(error)) from None


def _execute(
    workflow: Workflow, workflow_path: Path, adapter: Adapter, roles: dict[str, tuple[str, str]], repo: Path,
    state_dir: Path, run_id: str, resuming: bool, broken: LockHolder | None,
) -> RunResult:
    order = plan_order(workflow)
    if not resuming:
        _git(repo, "branch", workflow.run_branch, workflow.base_branch)
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
        if not _branch_exists(repo, workflow.run_branch):
            raise EngineError(f"run branch {workflow.run_branch!r} of run {run_id!r} is not a branch of the repository {repo}")
        journal.append(
            "resume", run_id=run_id, built=list(progress.built), failed=list(progress.failed),
            skipped=list(progress.skipped), open=progress.open_ticket,
        )
    if broken is not None:
        journal.append("lock-broken", run_id=broken.run_id, pid=broken.pid)
    built, failed, failures, skipped = progress.built, progress.failed, progress.failures, progress.skipped
    complete = {*built, *failed, *skipped}
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
            reason = _build_ticket(workflow, ticket, adapter, roles, repo, state_dir, run_id, journal, prior)
        except Exception as error:  # noqa: BLE001 - the journal records it and the command reports it
            # An exception that the engine did not plan for leaves the state of the
            # step unknown, so the run ends here. The journal says why.
            reason = f"crashed: {type(error).__name__}: {error}"
            journal.append("step-end", ticket=ticket.id, state=FAILED, reason=reason)
            failed.append(ticket.id)
            failures[ticket.id] = reason
            for unreached in order[position + 1 :]:
                skipped[unreached.id] = f"the run ended after ticket {ticket.id} crashed"
                journal.append("skip", ticket=unreached.id, blockers=[], reason=skipped[unreached.id])
            break
        journal.append("step-end", ticket=ticket.id, state=FAILED if reason else BUILT, reason=reason)
        if reason:
            failed.append(ticket.id)
            failures[ticket.id] = reason
        else:
            built.append(ticket.id)
    journal.append(
        "run-end", result=BUILT if not failed else FAILED, built=built, failed=failed, skipped=list(skipped)
    )
    return RunResult(run_id, journal.path, built, failed, failures, skipped)


def _specialist_done(prior: list[dict[str, object]]) -> bool:
    """True when the journal shows a valid specialist report after the last time the specialist was spawned."""
    spawned = valid = -1
    for index, event in enumerate(prior):
        if event["event"] in ("step-start", "continuation"):
            spawned = index
        elif event["event"] == "report-validation" and event["valid"]:
            valid = index
    return valid > spawned


def _prepare_worktree(repo: Path, worktree: Path, branch: str, base: str) -> None:
    """Make the worktree of a ticket, or reuse the one that is there.

    A worktree that a killed run left is reused as it is, except that a rebase
    which the kill stopped is aborted. A branch without a worktree gets a new
    worktree. Raise EngineError when the worktree holds another branch.
    """
    if worktree.is_dir():
        for name in ("rebase-merge", "rebase-apply"):
            if (worktree / _git(worktree, "rev-parse", "--git-path", name)).exists():
                _git(worktree, "rebase", "--abort")
                break
        current = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
        if current != branch:
            raise EngineError(f"worktree {worktree} holds {current!r}, not the branch {branch!r} of its ticket")
        return
    _git(repo, "worktree", "prune")
    if _branch_exists(repo, branch):
        _git(repo, "worktree", "add", "-q", str(worktree), branch)
    else:
        _git(repo, "worktree", "add", "-q", "-b", branch, str(worktree), base)


def _build_ticket(
    workflow: Workflow, ticket: Ticket, adapter: Adapter, roles: dict[str, tuple[str, str]],
    repo: Path, state_dir: Path, run_id: str, journal: Journal, prior: list[dict[str, object]] | None = None,
) -> str | None:
    """Build one ticket. Return None when it is built, or the reason it failed.

    In `assure` mode a ticket is built only when its verifier accepts it.

    `prior` holds the journal events of this ticket when the step was open in a
    run that stopped. The step then goes on in its own worktree.
    """
    tier, model = roles["specialist"]
    stack = workflow.stacks[ticket.stack]
    branch = _ticket_branch(workflow, ticket)
    worktree = state_dir / "worktrees" / run_id / ticket.id
    report_path = state_dir / "runs" / run_id / "reports" / f"{ticket.id}.specialist.json"
    if prior is None:
        base_commit = _git(repo, "rev-parse", workflow.base_branch)
        _prepare_worktree(repo, worktree, branch, workflow.base_branch)
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
        _prepare_worktree(repo, worktree, branch, workflow.base_branch)
        specialist_done = _specialist_done(prior)
        continuations = sum(1 for e in prior if e["event"] == "continuation")
        verify_rounds = sum(1 for e in prior if e["event"] == "verify-start")
    reason = _build_with_continuations(
        workflow, ticket, adapter, model, repo, worktree, branch, report_path, journal,
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
    report_path: Path, journal: Journal, spawned: bool = False, continuations: int = 0, interrupted: bool = False,
) -> str | None:
    """Run the specialist, check its work, and continue it in the same worktree when the work is not done.

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
        so_far = _git(worktree, "log", "--format=%H %s", f"{workflow.base_branch}..HEAD").splitlines()
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
        if result is not None and (result.end_state != adapters.FINISHED or result.exit_status != 0):
            trigger = adapters.CAPPED if result.end_state == adapters.CAPPED else adapters.FAILED
            reason = f"the specialist ended {result.end_state} with exit status {result.exit_status}"
            gate_output = None
        else:
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
        commits = _git(worktree, "log", "--format=%H %s", f"{workflow.base_branch}..HEAD").splitlines()
        journal.append(
            "continuation", ticket=ticket.id, count=continuations, limit=limit, trigger=trigger,
            mode="resume" if resume_session is not None else "brief", resume_session=resume_session, commits=commits, reason=reason,
        )
        prompt = continuation_brief(
            ticket.id, ticket.text, worktree, stack.hotspots, stack.gates, report_path, commits, reason,
            resumed=resume_session is not None, gate_output=gate_output,
        )


def _prepare_copy(repo: Path, base: str, branch: str, copy: Path) -> None:
    """Make a self-contained clone at `copy` that holds the base and the branch, with the branch checked out.

    The clone has its own git directory and no remote, so the verifier can break
    it, and cannot reach the real repository through it.
    """
    _git(repo, "clone", "-q", "--no-checkout", str(repo), str(copy))
    _git(copy, "branch", base, f"origin/{base}")
    _git(copy, "checkout", "-q", "-b", branch, f"origin/{branch}")
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

    @property
    def stack(self) -> Stack:
        return self.workflow.stacks[self.ticket.stack]

    @property
    def branch(self) -> str:
        return _ticket_branch(self.workflow, self.ticket)

    @property
    def worktree(self) -> Path:
        return self.state_dir / "worktrees" / self.run_id / self.ticket.id

    @property
    def run_dir(self) -> Path:
        return self.state_dir / "runs" / self.run_id


def _rebase_onto_run_branch(step: _Step) -> str | None:
    """Rebase the ticket branch onto the run branch, then run the gates again on the rebased tree.

    Return None when the branch is rebased and the gates are green, or the
    reason the step fails. A rebase that stops is aborted, so the branch and its
    worktree keep the state from before the rebase.
    """
    ticket, run_branch = step.ticket, step.workflow.run_branch
    onto = _git(step.repo, "rev-parse", f"refs/heads/{run_branch}")
    before = _git(step.worktree, "rev-parse", "HEAD")
    rebase = subprocess.run(["git", "-C", str(step.worktree), "rebase", run_branch], capture_output=True, text=True)
    if rebase.returncode != 0:
        files = _git(step.worktree, "diff", "--name-only", "--diff-filter=U").splitlines()
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

    A REJECT starts a fix-up round, up to `FIXUP_ROUND_LIMIT` rounds. Each
    round, and the first pass, spawns a fresh verifier.

    A resume sets `first_round` to the number of verifier runs that the journal
    holds. The first pass of the resume is a full verification at that round
    number, and the limit counts across the stop.
    """
    try:
        if first_round > FIXUP_ROUND_LIMIT:
            raise _StepFailed(f"the {first_round} verifier runs of the stopped run use up the {FIXUP_ROUND_LIMIT} fix-up rounds")
        rejected = ""
        report: VerifierReport | None = None
        for round_number in range(first_round, FIXUP_ROUND_LIMIT + 1):
            fixup_body = None
            if report is not None:
                fixup_body = _fixup_round(step, round_number, rejected, report.findings)
            verified = _git(step.repo, "rev-parse", f"refs/heads/{step.branch}")
            report = _verify_round(step, round_number, fixup_body)
            if report.verdict == "ACCEPT":
                return _advance_run_branch(step, verified)
            rejected = verified
        assert report is not None
        raise _StepFailed(
            f"the verifier rejected the branch after {FIXUP_ROUND_LIMIT} fix-up rounds: " + "; ".join(report.findings)
        )
    except _StepFailed as failed:
        return str(failed)


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
    if result.end_state != adapters.FINISHED or result.exit_status != 0:
        raise _StepFailed(f"the fix-up specialist ended {result.end_state} with exit status {result.exit_status}")
    try:
        report = read_specialist_report(report_path, ticket.id)
    except ReportError as error:
        step.journal.append(
            "fixup-report", ticket=ticket.id, round=round_number, valid=False, path=str(report_path), reason=str(error)
        )
        raise _StepFailed(str(error)) from None
    step.journal.append(
        "fixup-report", ticket=ticket.id, round=round_number, valid=True, path=str(report_path), reason=None
    )
    if report.status != "committed":
        detail = f": {report.blocked_reason}" if report.blocked_reason else ""
        raise _StepFailed(f"the fix-up specialist reported status {report.status!r}{detail}")
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
            if fixup_body is None:
                prompt = verifier_run_brief(
                    copy, step.branch, step.workflow.run_branch, ticket.text, step.stack.gates, report_path
                )
            else:
                prompt = fixup_body + verifier_run_sections(copy, report_path, mode)
        except (EngineError, BriefError) as error:
            raise _StepFailed(f"the verifier could not start: {error}") from None
        step.journal.append(
            "verify-start", ticket=ticket.id, round=round_number, agent=step.stack.verifier, tier=tier, model=model,
            commit=verified, copy=str(copy),
        )
        result = step.adapter.run(AdapterRequest(step.stack.verifier, model, prompt, copy, report_path))
        # The copy goes; the verdict report and the event stream stay as evidence of the run.
        evidence.mkdir(parents=True)
        for entry in scratch.iterdir():
            if entry != copy:
                shutil.move(str(entry), evidence / entry.name)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    report_path = evidence / report_path.name
    stream = result.event_stream
    if stream.is_relative_to(scratch):
        stream = evidence / stream.relative_to(scratch)
    step.journal.append(
        "verify-result", ticket=ticket.id, round=round_number, exit_status=result.exit_status, end_state=result.end_state,
        session_id=result.session_id, event_stream=str(stream),
    )
    if result.end_state != adapters.FINISHED or result.exit_status != 0:
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
    step.journal.append(
        "verdict", ticket=ticket.id, round=round_number, mode=report.mode, verdict=report.verdict,
        findings=report.findings, unverified=report.unverified, report=str(report_path),
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
