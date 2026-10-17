"""The workflow engine: build each ticket through a harness adapter.

A run has one base: the commit of the base branch at `run-start`. The run
branch starts at that commit, the journal records it, and a resume uses it, not
the tip of the base branch. For each ticket the engine makes a worktree from the
base of the run (or from the run branch, when the ticket has `blocked-by` or the
workflow sets `ticket-start = "run-branch"`), installs the
push guard in it (see `guards.py`), spawns the stack's specialist through the adapter, checks the specialist's report, runs
the stack's gates itself (in `assure` mode), and records each event in the journal. After the
specialist reports `committed`, and before any gate runs, the engine compares
the changed paths with the hotspot patterns of the stack. A match fails the
step, and no verifier runs (see `guards.py`). A fix-up gets the same check.

In `assure` mode the engine then verifies the built branch. It makes a
temporary copy of the branch, builds a full verifier brief with the brief
generator, spawns the stack's verifier on the verifier tier, and checks the
verdict report. Before the verifier starts, the engine rebases the ticket
branch onto the run branch, and runs the gates again on the rebased tree. A
gate runs at most once green on one commit: when the journal holds a green
result of the gate on the commit, the engine writes `gate-skip` and does not
run it again. So a rebase that keeps the commit, and a resume, run no gate a
second time. On ACCEPT the engine fast-forwards the run branch to the
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
ticket, and the engine runs no gate and no verifier for a ticket by itself: a
valid report that says committed, on a branch with a commit, builds the step.
After the last ticket, `_ChainRun` runs the gates of each stack once on the
chain tip, and then verifies once for each stack, over the commits of the
tickets of that stack. Red gates and a REJECT each start a fix-up round, as a
new commit on the chain tip. The gates then run again, and green gates go on
to a fresh verifier. The rounds of both causes count together against the
fix-up limit. A finding maps to a ticket by its label, and a finding with no
known label is recorded as unmapped. When every stack is accepted, the engine
fast-forwards the run branch to the chain tip.

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
valid is not spawned again. A verifier run that ended with no verdict does not
count against the fix-up limit: the resume runs that round again.

A resume with `retry` goes on with one failed step, also in a run that ended
(see `_retry_plan`). It uses the worktree and the commits of the step again,
and goes on at the first phase that did not complete. In `assure` mode it
spawns no specialist, and that phase is the gates, the verifier, or the scoped
verifier. When the run branch moved after the step failed, the retry rebases,
runs the gates, and verifies the whole branch. In `economy` mode the phase is
the specialist, which continues with the continuation limit of the mode again,
or the check of its report. The tickets after the step that the run skipped are
checked again, and the chain then goes on: the verification of the chain
covers the built tickets whose work the run branch does not hold. The journal
records `retry`, and then a new `step-end` and a new `run-end`. The newest of
each wins.

The rules of a run are in `domain.py`: the modes, the limits, the skip rule, the
start of a ticket in a chain, and the handling of a verdict. The engine reads
and writes the world, and asks that module what the rules say.

Standard library only. The engine starts no process: the version-control port
keeps the work of a run, the harness port runs the agents, and the gate port
runs the gate commands. `tests/test_architecture.py` checks that no module of
the run context imports `subprocess`.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from ..ports import harness
from ..ports.gates import GateRunner
from ..ports.harness import Adapter, AdapterRequest, AdapterResult
from ..ports.vcs import REBASED, VcsError, VersionControl
from ..shared.tiers import Tiers
from . import domain
from .brief import (
    BriefError, ChainSegment, chain_brief, continuation_brief, finding_labels_section, fixup_brief, green_gates_text,
    report_correction_brief, specialist_brief, verifier_run_brief, verifier_run_sections,
)
from .guards import (
    GuardError, InvariantViolation, changed_paths, check_worktree_unchanged, hotspot_matches, install_push_guard,
    snapshot_worktree,
)
from .journal import Journal, JournalError, read_events
from .lock import LockError, LockHolder, check_free, run_lock
from .reports import ReportError, VerifierReport, map_findings, read_specialist_report, read_verifier_report, verdict_in_text
from .workflow import Stack, Ticket, Workflow, plan_order

# The journal keeps this many characters of the end of a gate's output.
GATE_OUTPUT_TAIL_CHARS: int = 4000

# The directory of the run that holds the log file of each gate run, in one directory for each ticket.
GATE_LOG_DIRECTORY: str = "gates"

BUILT: str = "built"
FAILED: str = "failed"
SKIPPED: str = "skipped"

# The `role` of an `agent-start` event: the agent that the engine starts.
ROLE_SPECIALIST: str = "specialist"
ROLE_FIXUP: str = "fix-up"
ROLE_VERIFIER: str = "verifier"

# The `phase` of a gate result in the journal: the gates after the specialist
# built the ticket, after the rebase onto the run branch, after a fix-up, and on
# the tip of an `economy` chain.
PHASE_BUILD: str = "build"
PHASE_REBASE: str = "rebase"
PHASE_FIXUP: str = "fixup"
PHASE_CHAIN: str = domain.GATES_OF_CHAIN

# The `trigger` of a `fixup-start` of a chain: red gates on the tip of the chain, or a REJECT.
TRIGGER_GATES_RED: str = "gates-red"
TRIGGER_REJECT: str = "reject"

# The `trigger` of a `continuation` whose report check failed on a branch with work: it asks only for a corrected report.
# The report file is there but not valid, or there is no report file.
TRIGGER_REPORT_INVALID: str = "report-invalid"
TRIGGER_REPORT_MISSING: str = "report-missing"

# The `phase` of a `retry` event: the first phase of the failed step that did not complete.
# `specialist` is only in `economy` mode, where the specialist of the step continues.
RETRY_SPECIALIST: str = "specialist"
RETRY_GATES: str = "gates"
RETRY_VERIFIER: str = "verifier"
RETRY_SCOPED_VERIFIER: str = "scoped-verifier"


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
    # The holder of a lock of this run whose process no longer exists, which the resume took over without `break_lock`.
    taken_over: LockHolder | None = None

    @property
    def ok(self) -> bool:
        return not self.failed


def new_run_id() -> str:
    """A new run id: the UTC time and four hexadecimal characters, for example `20261008T093837Z-e9dc`."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)


def _role_model(workflow: Workflow, tiers: Tiers, column: str, role: str) -> tuple[str, str]:
    """The tier of a role and its model from the adapter's own tier column."""
    tier = domain.role_tier(workflow.mode, role, workflow.tier_overrides)
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


def _tip(vcs: VersionControl, repo: Path, branch: str) -> str:
    """The id of the newest commit of the branch. Raise EngineError when there is no such branch."""
    with _vcs_errors():
        return vcs.branch_tip(repo, branch)


def _head(vcs: VersionControl, worktree: Path) -> str:
    """The id of the commit that the worktree has checked out."""
    with _vcs_errors():
        return vcs.head_commit(worktree)


def _commit_lines(vcs: VersionControl, path: Path, since: str, until: str | None = None) -> list[str]:
    """The commits beyond `since`, newest first, each as `<id> <subject>`. `until` is the checkout of `path` when it is None."""
    with _vcs_errors():
        return [f"{commit.id} {commit.subject}" for commit in vcs.commits_since(path, since, until)]


def _holds(vcs: VersionControl, repo: Path, ancestor: str, descendant: str) -> bool:
    """Tell whether `descendant` holds the commit `ancestor`. A name that is no commit does not hold anything."""
    try:
        return vcs.is_ancestor(repo, ancestor, descendant)
    except VcsError:
        return False


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


# The environment variable of a host that moves the state of every repository out of its git directory. Claude Code
# in `dontAsk` mode refuses `Edit` and `Write` on a path with a `.git` component, also with an allow rule, so on such
# a host a specialist can write neither in a worktree nor its report under `<git common dir>/delegate` (#97).
STATE_ROOT_VARIABLE: str = "DELEGATE_STATE_ROOT"


class StateRootError(EngineError):
    """`DELEGATE_STATE_ROOT` has a value that the engine cannot use."""


def state_directory(repo: Path, vcs: VersionControl) -> tuple[Path, Path]:
    """The top level of the repository and the directory where the engine keeps its state.

    The state is in `<git common dir>/delegate`. When the host sets
    `DELEGATE_STATE_ROOT`, it is in `<root>/<name>-<key>` instead. Both parts
    come from the git common directory, which every worktree of a repository
    shares: `<name>` is the name of the directory that holds it (`.git` is in
    the top level of the main checkout), or its own name when it is not called
    `.git` (a bare repository or a separate git directory), and `<key>` is the
    first 12 hex digits of the SHA-256 of its absolute path. So one root serves
    every repository of a host, each repository gets its own directory, and
    every worktree of a repository finds the same one.

    Raise EngineError when `repo` is not inside a repository, and
    StateRootError when the root is not an absolute path.
    """
    with _vcs_errors():
        top = vcs.repository_root(repo)
        shared = vcs.shared_data_directory(top)
    root = os.environ.get(STATE_ROOT_VARIABLE)
    if not root:
        return top, shared / "delegate"
    if not Path(root).is_absolute():
        raise StateRootError(f"{STATE_ROOT_VARIABLE} is {root!r}, which is not an absolute path")
    name = shared.parent.name if shared.name == ".git" else shared.name
    key = hashlib.sha256(str(shared).encode()).hexdigest()[:12]
    return top, Path(root) / f"{name}-{key}"


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


def _restore(events: list[dict[str, object]], run_id: str, retrying: bool = False) -> _Progress:
    """Rebuild the progress of a run from its journal. Raise EngineError when the run has ended, unless `retrying`."""
    progress = _Progress(events=events)
    started: str | None = None
    for event in events:
        kind, ticket = event["event"], str(event.get("ticket"))
        if kind == "run-end" and not retrying:
            raise EngineError(f"run {run_id!r} has ended with the result {event['result']!r}; it cannot be resumed")
        if kind == "step-start":
            started = ticket
            # An `economy` retry checks a skipped ticket again, and can build it.
            progress.skipped.pop(ticket, None)
        elif kind == "step-end":
            # The newest step-end of a ticket wins: the verification at the end of an
            # `economy` chain can fail a ticket that was built.
            # A retry can build a ticket that failed before.
            if ticket in progress.built:
                progress.built.remove(ticket)
            if event["state"] == BUILT:
                if ticket in progress.failed:
                    progress.failed.remove(ticket)
                    del progress.failures[ticket]
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


@dataclass(frozen=True)
class _RetryPlan:
    """Where a failed step goes on: the phase and the verifier round, from the journal events of the step (`prior`).

    `rejected` and `findings` are set when the branch holds a fix-up commit: the
    commit that the last verdict rejected, and the findings of that verdict.
    """

    ticket: str
    phase: str
    round: int
    prior: tuple[dict[str, object], ...]
    rejected: str | None = None
    findings: tuple[str, ...] = ()


def _retry_plan(workflow: Workflow, progress: _Progress, run_id: str, ticket_id: str) -> _RetryPlan:
    """The first phase of the failed step of `ticket_id` that did not complete. Raise EngineError when it cannot be retried.

    The phase comes from the journal events of the step. The round is the
    number of verdicts, because a verifier run that ended with no verdict does
    not count against the fix-up limit:

    - No verifier started: the gates.
    - The last verifier started after the last verdict: that verifier again.
      It is the full verifier when the step has no verdict, and otherwise the
      scoped verifier of the fix-up commit.
    - The last verdict is a REJECT, and the fix-up specialist of the next round
      reported committed work: the gates of that fix-up, then its scoped verifier.

    A retry of `assure` mode spawns no specialist. So a step whose specialist,
    or whose fix-up specialist, gave no valid report that says committed is
    refused. So is a step that the verifier accepted, or rejected after the
    last fix-up round. For `economy` mode, see `_economy_retry_plan`.
    """
    refused = _refused(ticket_id, run_id)
    if ticket_id not in {ticket.id for ticket in workflow.tickets}:
        raise EngineError(f"{refused}: it is not a ticket of the run")
    if ticket_id in progress.built:
        raise EngineError(f"{refused}: it is built; --retry goes on only with a step that failed")
    if ticket_id in progress.skipped:
        raise EngineError(f"{refused}: it was skipped; --retry goes on only with a step that failed")
    if ticket_id == progress.open_ticket:
        raise EngineError(f"{refused}: its step is open; --resume without --retry goes on with it")
    if ticket_id not in progress.failed:
        raise EngineError(f"{refused}: its step did not start; --retry goes on only with a step that failed")
    prior = tuple(e for e in progress.events if e.get("ticket") == ticket_id)
    if workflow.mode == domain.ECONOMY:
        return _economy_retry_plan(workflow, progress, refused, ticket_id, prior)
    if not _specialist_committed(prior):
        raise EngineError(
            f"{refused}: its specialist gave no valid report that says committed; "
            "--retry goes on only at the gates, the verifier or the scoped verifier"
        )
    starts = [index for index, e in enumerate(prior) if e["event"] == "verify-start"]
    verdicts = [index for index, e in enumerate(prior) if e["event"] == "verdict"]
    round_number = len(verdicts)
    if not starts:
        return _RetryPlan(ticket_id, RETRY_GATES, 0, prior)
    if not verdicts:
        return _RetryPlan(ticket_id, RETRY_VERIFIER, 0, prior)
    last = prior[verdicts[-1]]
    if domain.is_accepted(str(last["verdict"])):
        raise EngineError(f"{refused}: its verifier accepted it, and the step failed after the verdict: {progress.failures[ticket_id]}")
    rejected = str(prior[max(index for index in starts if index < verdicts[-1])]["commit"])
    findings = tuple(str(finding) for finding in last["findings"])  # type: ignore[union-attr]
    if starts[-1] > verdicts[-1]:
        return _RetryPlan(ticket_id, RETRY_SCOPED_VERIFIER, round_number, prior, rejected, findings)
    if round_number > domain.fixup_round_limit(workflow.mode):
        raise EngineError(f"{refused}: its verifier rejected it after the last fix-up round")
    reports = [e for e in prior if e["event"] == "fixup-report" and e["round"] == round_number]
    if not reports or not reports[-1]["valid"] or reports[-1]["status"] != domain.COMMITTED:
        raise EngineError(
            f"{refused}: the fix-up specialist of round {round_number} gave no valid report that says committed; "
            "--retry goes on only at the gates, the verifier or the scoped verifier"
        )
    return _RetryPlan(ticket_id, RETRY_GATES, round_number, prior, rejected, findings)


def _refused(ticket_id: str, run_id: str) -> str:
    return f"ticket {ticket_id!r} of run {run_id!r} cannot be retried"


def _specialist_committed(prior: tuple[dict[str, object], ...]) -> bool:
    """True when the journal shows a valid specialist report that says committed, after the last spawn of the specialist."""
    validations = [e for e in prior if e["event"] == "report-validation"]
    return _specialist_done(list(prior)) and validations[-1]["status"] == domain.COMMITTED


def _economy_retry_plan(
    workflow: Workflow, progress: _Progress, refused: str, ticket_id: str, prior: tuple[dict[str, object], ...]
) -> _RetryPlan:
    """The first phase of a failed step of `economy` mode that did not complete. Raise EngineError when it cannot be retried.

    The phase is the gates when the journal shows a valid report of the
    specialist that says committed: no specialist spawns, and the engine checks
    the report, the commits and the hotspots again. The gates then run on the
    tip of the chain. Otherwise the phase is the specialist, which continues.

    A step that the verification of the chain failed is refused: the chain
    failed as a whole. So is a step after which the run built a ticket: the
    branch of that ticket does not hold the commits of this step. A step with
    no `step-start` is refused too. `_check_chain_start` refuses a step whose
    chain start moved.
    """
    if not any(e["event"] == "step-start" for e in prior):
        raise EngineError(f"{refused}: its step did not start; --retry goes on only with a step that started")
    if any(e["event"] == "step-end" and e["state"] == BUILT for e in prior):
        raise EngineError(
            f"{refused}: the verification of the chain failed it; "
            "--retry in economy mode goes on only with a step that failed before the verification of the chain"
        )
    order = [ticket.id for ticket in plan_order(workflow)]
    later = [other for other in order[order.index(ticket_id) + 1 :] if other in progress.built]
    if later:
        raise EngineError(
            f"{refused}: ticket {later[0]!r} after it in the chain is built, "
            "and its branch does not hold the commits of this step"
        )
    phase = RETRY_GATES if _specialist_committed(prior) else RETRY_SPECIALIST
    return _RetryPlan(ticket_id, phase, 0, prior)


def _check_chain_start(
    workflow: Workflow, order: list[Ticket], built: list[str], plan: _RetryPlan, vcs: VersionControl, repo: Path, run_base: str,
    run_id: str,
) -> None:
    """Refuse an `economy` retry whose step started from another commit than the chain gives it now.

    The chain before the step can change after the step started: the
    verification of the chain can add a fix-up commit to the tip, or fail the
    tickets before it. The branch of the step then does not continue the chain.
    """
    ticket = next(ticket for ticket in order if ticket.id == plan.ticket)
    started = str(next(e for e in plan.prior if e["event"] == "step-start")["base_commit"])
    with _vcs_errors():
        now = vcs.commit_of(repo, _start_ref(workflow, order, built, ticket, run_base))
    if started != now:
        raise EngineError(
            f"{_refused(plan.ticket, run_id)}: its branch starts at commit {started}, "
            f"but the chain before it now ends at commit {now}; the verification of the chain changed or failed the tickets before it"
        )


def run_workflow(
    workflow: Workflow, workflow_path: Path, repo: Path, tiers: Tiers, adapter: Adapter, vcs: VersionControl, gates: GateRunner, *,
    resume: str | None = None, retry: str | None = None, break_lock: bool = False, on_start: Callable[[str], None] | None = None,
    run_id: str | None = None,
) -> RunResult:
    """Build the tickets of a workflow in dependency order, through `adapter`. Return the result of the run.

    The caller picks the adapter that the workflow names, the version-control
    backend `vcs`, and the gate runner `gates`. The engine knows the ports only,
    never an adapter module. The run branch, the worktrees and the file-watcher
    setting come from `vcs`. The engine runs each gate command through `gates`.

    With `resume`, the run id of an earlier run, go on with that run: build the
    tickets that are not complete, and append to its journal. With `retry` too,
    the id of a ticket whose step failed, also go on with that step (see
    `_retry_plan`). Raise EngineError, which names the ticket, when the step
    cannot be retried.

    The run holds the lock of its run branch until it ends. Raise EngineError,
    which names the holder, when another run holds the lock. A resume takes over
    a lock of its own run whose process no longer exists, and the result names
    that holder in `taken_over`. `break_lock` also removes a lock of another run
    whose process no longer exists. No lock whose process is alive is removed.

    `on_start` gets the run id when the run holds its lock, before the first step: `delegate run` puts the id in the
    title of the terminal.

    `run_id` is the id of a new run. The default is a new id from `new_run_id`. `delegate run --detach` gives the id
    that it printed before it started the run process. A resume uses the id `resume`, and ignores `run_id`.
    """
    repo, state_dir = state_directory(repo, vcs)
    roles = {role: _role_model(workflow, tiers, adapter.tier_column, role) for role in ("specialist", "verifier")}
    run_id = resume if resume is not None else (run_id or new_run_id())
    try:
        # A run branch in use is reported first. A refused run creates nothing, so the lock is taken last.
        check_free(state_dir, workflow.run_branch, run_id, break_stale=break_lock)
        if resume is None:
            _check_branches(workflow, repo, vcs)
        with run_lock(state_dir, workflow.run_branch, run_id, break_stale=break_lock) as broken:
            if on_start is not None:
                on_start(run_id)
            result = _execute(
                workflow, workflow_path, adapter, vcs, gates, roles, repo, state_dir, run_id, resume is not None, broken, retry
            )
            return result if break_lock else replace(result, taken_over=broken)
    except LockError as error:
        raise EngineError(str(error)) from None


def _execute(
    workflow: Workflow, workflow_path: Path, adapter: Adapter, vcs: VersionControl, gates: GateRunner,
    roles: dict[str, tuple[str, str]], repo: Path, state_dir: Path, run_id: str, resuming: bool, broken: LockHolder | None,
    retry: str | None = None,
) -> RunResult:
    order = plan_order(workflow)
    plan: _RetryPlan | None = None
    if not resuming:
        with _vcs_errors():
            run_base = vcs.commit_of(repo, workflow.base_branch)
            vcs.create_branch(repo, workflow.run_branch, run_base)
        journal = Journal(state_dir / "runs" / run_id / "journal.jsonl")
        journal.append(
            "run-start", run_id=run_id, workflow=str(workflow_path.resolve()), mode=workflow.mode, adapter=workflow.adapter,
            base_branch=workflow.base_branch, base_commit=run_base, run_branch=workflow.run_branch, tickets=[t.id for t in order],
        )
        progress = _Progress()
    else:
        journal, events = _journal_events(state_dir, run_id)
        progress = _restore(events, run_id, retrying=retry is not None)
        _check_same_run(workflow, workflow_path, events[0], run_id)
        with _vcs_errors():
            run_branch_exists = vcs.branch_exists(repo, workflow.run_branch)
        if not run_branch_exists:
            raise EngineError(f"run branch {workflow.run_branch!r} of run {run_id!r} is not a branch of the repository {repo}")
        run_base = _run_base(vcs, repo, workflow, events)
        # A step that cannot be retried is refused before the journal changes.
        plan = _retry_plan(workflow, progress, run_id, retry) if retry is not None else None
        if plan is not None and workflow.mode == domain.ECONOMY:
            _check_chain_start(workflow, order, progress.built, plan, vcs, repo, run_base, run_id)
        journal.append(
            "resume", run_id=run_id, built=list(progress.built), failed=list(progress.failed),
            skipped=list(progress.skipped), open=progress.open_ticket,
        )
    if broken is not None:
        journal.append("lock-broken", run_id=broken.run_id, pid=broken.pid)
    built, failed, failures, skipped = progress.built, progress.failed, progress.failures, progress.skipped
    retried_reason = ""
    if plan is not None:
        failed.remove(plan.ticket)
        retried_reason = failures.pop(plan.ticket)
        if workflow.mode == domain.ECONOMY:
            # The chain goes on after the step: the skip rule applies again to each ticket after it that was skipped.
            for later in order[[t.id for t in order].index(plan.ticket) + 1 :]:
                skipped.pop(later.id, None)
    complete = {*built, *failed, *skipped}
    ended_early = False
    for position, ticket in enumerate(order):
        if ticket.id in complete:
            continue
        skip = domain.skip_reason(ticket.blocked_by, built, failed)
        if skip:
            skipped[ticket.id] = skip
            journal.append("skip", ticket=ticket.id, blockers=domain.unbuilt_blockers(ticket.blocked_by, built), reason=skip)
            continue
        prior = [e for e in progress.events if e.get("ticket") == ticket.id] if ticket.id == progress.open_ticket else None
        start = _start_ref(workflow, order, built, ticket, run_base)
        try:
            if plan is not None and ticket.id == plan.ticket:
                reason = _retry_ticket(
                    _Step(workflow, ticket, adapter, vcs, gates, roles, repo, state_dir, run_id, journal), plan, retried_reason, start
                )
            else:
                reason = _build_ticket(workflow, ticket, adapter, vcs, gates, roles, repo, state_dir, run_id, journal, start, prior)
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
                if unreached.id in complete:
                    continue  # a retry: the ticket ended in the run before
                skipped[unreached.id] = domain.unreached_reason(ticket.id, halted)
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
        chain = _ChainRun(workflow, order, adapter, vcs, gates, roles, repo, state_dir, run_id, journal, built, failed, failures)
        unmapped = chain.verify()
    journal.append(
        "run-end", result=BUILT if not failed else FAILED, built=built, failed=failed, skipped=list(skipped)
    )
    return RunResult(run_id, journal.path, built, failed, failures, skipped, unmapped)


def _run_base(vcs: VersionControl, repo: Path, workflow: Workflow, events: list[dict[str, object]]) -> str:
    """The base of a run that goes on: the commit of the base branch at `run-start`, which the journal records.

    The engine of 1.2.0 and before did not record it. The run branch started at that
    commit and moves only by fast-forward, so it is then the `from_commit` of the
    first `run-branch-advance`, or the tip of the run branch when it did not move.
    """
    recorded = events[0].get("base_commit")
    if recorded is not None:
        return str(recorded)
    advances = [e for e in events if e["event"] == "run-branch-advance"]
    if advances:
        return str(advances[0]["from_commit"])
    return _tip(vcs, repo, workflow.run_branch)


def _start_ref(workflow: Workflow, order: list[Ticket], built: list[str], ticket: Ticket, run_base: str) -> str:
    """The branch or the commit that `ticket` starts from.

    It is the branch of its predecessor in a chain (see `domain.chain_predecessor`),
    the run branch when the ticket has blockers or the workflow says so (see
    `domain.starts_from_run_branch`), or else `run_base`, the base of the run. It
    is never the base branch, which can move while the run goes on.
    """
    predecessor = domain.chain_predecessor(workflow.mode, [ticket.id for ticket in order], built)
    if predecessor is None:
        if domain.starts_from_run_branch(workflow.mode, workflow.ticket_start, ticket.blocked_by):
            return workflow.run_branch
        return run_base
    return _ticket_branch(workflow, next(ticket for ticket in order if ticket.id == predecessor))


def _stream_path(report_path: Path) -> Path:
    """The file for the event stream of an agent: beside its report, and never over an earlier stream. Make its directory."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    path = report_path.parent / f"{report_path.stem}.stream.jsonl"
    number = 1
    while path.exists():
        number += 1
        path = report_path.parent / f"{report_path.stem}.stream-{number}.jsonl"
    return path


def _start_agent(journal: Journal, ticket: str, role: str, round_number: int, report_path: Path) -> Path:
    """Choose the event stream of the agent that starts now, and journal it before the agent starts. Return the stream."""
    stream = _stream_path(report_path)
    journal.append("agent-start", ticket=ticket, role=role, round=round_number, event_stream=str(stream))
    return stream


def _report_check(read: Callable[[], object]) -> Callable[[], bool]:
    """The check of a report that an adapter calls: true when `read` gives a report, false when it raises ReportError."""

    def valid() -> bool:
        try:
            read()
        except ReportError:
            return False
        return True

    return valid


def _request(
    agent: str, stack: Stack, model: str, prompt: str, cwd: Path, report_path: Path, stream: Path,
    report_valid: Callable[[], bool], resume_session: str | None = None,
) -> AdapterRequest:
    """The request for one agent run of the stack, with the idle timeout of the run, the check of its report, and the MCP servers of the stack."""
    return AdapterRequest(
        agent, model, prompt, cwd, report_path, stream, resume_session,
        idle_timeout=domain.IDLE_TIMEOUT_SECONDS, report_valid=report_valid, mcp_servers=stack.mcp_servers,
    )


def _journal_idle(journal: Journal, result: AdapterResult, ticket: str, role: str, round_number: int) -> None:
    """Record that the adapter ended the session of the agent at the idle timeout, if it did, before its result event."""
    if result.ended_idle:
        journal.append("idle-timeout", ticket=ticket, role=role, round=round_number, seconds=domain.IDLE_TIMEOUT_SECONDS)


def _session_done(result: AdapterResult, report_valid: Callable[[], bool]) -> bool:
    """Tell whether the engine takes the report of an agent run.

    A session that the adapter ended at the idle timeout had a valid report. A
    session whose stream ends in a success (`finished`) counts with exit status
    0, and with any other exit status when its report is valid: a coordinator
    or the adapter can stop an idle session after its work, with SIGTERM.
    """
    if result.ended_idle:
        return True
    if result.end_state != harness.FINISHED:
        return False
    return result.exit_status == 0 or report_valid()


def _specialist_done(prior: list[dict[str, object]]) -> bool:
    """True when the journal shows a valid specialist report after the last time the specialist was spawned."""
    spawned = valid = -1
    for index, event in enumerate(prior):
        if event["event"] in ("step-start", "continuation"):
            spawned = index
        elif event["event"] == "report-validation" and event["valid"]:
            valid = index
    return valid > spawned


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
        install_push_guard(vcs, repo, worktree, hooks_dir)
    except GuardError as error:
        raise EngineError(str(error)) from None


def _build_ticket(
    workflow: Workflow, ticket: Ticket, adapter: Adapter, vcs: VersionControl, gates: GateRunner, roles: dict[str, tuple[str, str]],
    repo: Path, state_dir: Path, run_id: str, journal: Journal, start_ref: str, prior: list[dict[str, object]] | None = None,
    retried: str | None = None,
) -> str | None:
    """Build one ticket. Return None when it is built, or the reason it failed.

    The branch of the ticket starts from `start_ref`: the base of the run, the
    run branch, or the branch of the previous ticket of a chain (see `_start_ref`).

    In `assure` mode a ticket is built only when its verifier accepts it.

    `prior` holds the journal events of this ticket when the step was open in a
    run that stopped. The step then goes on in its own worktree.

    `retried` is the reason a step of `economy` mode failed, when a retry goes
    on with it (see `_economy_retry_plan`). The step then goes on in its own
    worktree with the events of the failed step in `prior`. The specialist
    continues unless its report was valid and said committed, and it has the
    continuation limit of the mode again.
    """
    tier, model = roles["specialist"]
    stack = workflow.stacks[ticket.stack]
    branch = _ticket_branch(workflow, ticket)
    worktree = state_dir / "worktrees" / run_id / ticket.id
    hooks_dir = state_dir / "hooks" / run_id / ticket.id
    report_path = state_dir / "runs" / run_id / "reports" / f"{ticket.id}.specialist.json"
    rebased_onto: str | None = None
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
            if not _holds(vcs, repo, landed, f"refs/heads/{workflow.run_branch}"):
                raise EngineError(f"run branch {workflow.run_branch} does not hold commit {landed}, which the journal says it advanced to")
            return None
        base_commit = str(next(e for e in prior if e["event"] == "step-start")["base_commit"])
        _prepare_worktree(vcs, repo, worktree, branch, start_ref, hooks_dir)
        if retried is None:
            specialist_done = _specialist_done(prior)
            continuations = sum(1 for e in prior if e["event"] == "continuation")
        else:
            specialist_done, continuations = _specialist_committed(tuple(prior)), 0
        # A verifier run that ended with no verdict does not count against the fix-up limit.
        verify_rounds = sum(1 for e in prior if e["event"] == "verdict")
        rebases = [e for e in prior if e["event"] == "rebase" and e["result"] == "rebased"]
        if rebases:
            rebased_onto = str(rebases[-1]["onto_commit"])
    # The commits of a ticket are the commits beyond the point where its branch started, or, after a rebase,
    # beyond the commit of the run branch that the branch was rebased onto. So the hotspot check and the
    # continuation brief see the commits of this ticket only.
    since = domain.work_starts_at(base_commit, rebased_onto)
    reason = _build_with_continuations(
        workflow, ticket, adapter, vcs, gates, model, repo, worktree, branch, report_path, journal, since,
        spawned=specialist_done, continuations=continuations, interrupted=prior is not None,
        stop_reason="the run was interrupted" if retried is None else f"the step failed: {retried}",
    )
    if reason:
        return reason
    if workflow.mode == "assure":
        step = _Step(workflow, ticket, adapter, vcs, gates, roles, repo, state_dir, run_id, journal)
        reason = _rebase_onto_run_branch(step)
        return reason if reason else _verify_ticket(step, verify_rounds)
    return None


def _build_with_continuations(
    workflow: Workflow, ticket: Ticket, adapter: Adapter, vcs: VersionControl, gates: GateRunner, model: str, repo: Path,
    worktree: Path, branch: str, report_path: Path, journal: Journal, since: str, spawned: bool = False, continuations: int = 0, interrupted: bool = False,
    stop_reason: str = "the run was interrupted",
) -> str | None:
    """Run the specialist, check its work, and continue it in the same worktree when the work is not done.

    `since` is the commit or branch where the branch of the ticket started: the commits beyond it
    are the work of this ticket.

    A resume sets `spawned` when the journal shows that the specialist finished
    with a valid report, so the first pass does not spawn it again. It sets
    `continuations` to the number the journal holds, so the limit counts across
    the stop. It sets `interrupted` for a step that a stop left open: when the
    branch holds commits already, the first prompt is a continuation brief,
    with `stop_reason` as the reason that the last run stopped.

    The specialist continues when it ends capped or failed, or when its gates
    are red. In `economy` mode the engine runs no gate here: a valid report that
    says committed, on a branch with a commit, builds the step. A report that
    is missing or not valid, on a branch that holds a commit, gets a
    continuation that asks only for a corrected report and names each problem
    of the check. Return None when the report is valid and the
    branch holds green work, or the reason the step fails. A report that is
    missing or not valid on a branch with no commit, or that does not say
    committed, fails the step with no continuation. After `CONTINUATION_LIMIT`
    continuations the step fails, and the journal records the count.
    """
    stack = workflow.stacks[ticket.stack]
    limit = domain.continuation_limit(workflow.mode)
    each_ticket = domain.gates_each_ticket(workflow.mode)
    prompt = specialist_brief(
        ticket.id, ticket.text, worktree, stack.hotspots, stack.gates, report_path, gates_at_chain_end=not each_ticket
    )
    if interrupted and not spawned:
        so_far = _commit_lines(vcs, worktree, since)
        if so_far:
            prompt = continuation_brief(
                ticket.id, ticket.text, worktree, stack.hotspots, stack.gates, report_path, so_far,
                stop_reason, resumed=False, gates_at_chain_end=not each_ticket,
            )
    resume_session: str | None = None
    report_valid = _report_check(lambda: read_specialist_report(report_path, ticket.id))
    while True:
        if spawned:
            spawned, result = False, None
        else:
            stream = _start_agent(journal, ticket.id, ROLE_SPECIALIST, 0, report_path)
            result = adapter.run(_request(stack.specialist, stack, model, prompt, worktree, report_path, stream, report_valid, resume_session))
            _journal_idle(journal, result, ticket.id, ROLE_SPECIALIST, 0)
            journal.append(
                "adapter-result", ticket=ticket.id, exit_status=result.exit_status, end_state=result.end_state,
                session_id=result.session_id, event_stream=str(result.event_stream), **_refused_fields(result),
            )
        gate_output: str | None = None
        problems: tuple[str, ...] = ()
        if result is not None and not _session_done(result, report_valid):
            trigger = harness.CAPPED if result.end_state == harness.CAPPED else harness.FAILED
            reason = f"the specialist ended {result.end_state} with exit status {result.exit_status}"
        else:
            try:
                report = read_specialist_report(report_path, ticket.id)
            except ReportError as error:
                journal.append(
                    "report-validation", ticket=ticket.id, valid=False, path=str(report_path), reason=str(error),
                    status=None, blocked_reason=None,
                )
                # A report that is missing or not valid, on a branch with work, gets a continuation for the report only.
                if not _commit_lines(vcs, repo, since, branch):
                    return str(error)
                trigger = TRIGGER_REPORT_INVALID if report_path.is_file() else TRIGGER_REPORT_MISSING
                reason, problems = str(error), error.problems
            else:
                journal.append(
                    "report-validation", ticket=ticket.id, valid=True, path=str(report_path), reason=None,
                    status=report.status, blocked_reason=report.blocked_reason,
                )
                unfinished = domain.unfinished_reason(report.status, report.blocked_reason, "the specialist")
                if unfinished:
                    return unfinished
                if not _commit_lines(vcs, repo, since, branch):
                    return f"branch {branch} holds no commit beyond {since}, though the report says committed"
                stop = _hotspot_stop(vcs, journal, ticket.id, stack, worktree, since, True, PHASE_BUILD, 0)
                if stop:
                    return stop
                if not each_ticket:
                    return None  # `economy` mode: the gates run once, on the tip of the chain (see `_ChainRun`)
                outcomes = [(gate, *_run_gate_with_output(gates, vcs, gate, ticket.id, worktree, journal, 0, PHASE_BUILD)) for gate in stack.gates]
                red = [(gate, output) for gate, green, output in outcomes if not green]
                if not red:
                    return None
                trigger = "gates-red"
                reason = "gates red: " + "; ".join(gate for gate, _ in red)
                gate_output = "\n".join(f"$ {gate}\n{output.rstrip(chr(10))}" for gate, output in red)
        if not domain.may_continue(workflow.mode, continuations):
            journal.append("continuation-limit", ticket=ticket.id, count=continuations, limit=limit, trigger=trigger)
            return f"{reason}; the continuation limit of {limit} is reached"
        continuations += 1
        # A harness that gave no session id cannot resume, so a new agent continues.
        resume_session = result.session_id if result is not None and adapter.supports_resume else None
        commits = _commit_lines(vcs, worktree, since)
        journal.append(
            "continuation", ticket=ticket.id, count=continuations, limit=limit, trigger=trigger,
            mode="resume" if resume_session is not None else "brief", resume_session=resume_session, commits=commits, reason=reason,
        )
        if trigger in (TRIGGER_REPORT_INVALID, TRIGGER_REPORT_MISSING):
            prompt = report_correction_brief(ticket.id, worktree, report_path, commits, problems)
        else:
            prompt = continuation_brief(
                ticket.id, ticket.text, worktree, stack.hotspots, stack.gates, report_path, commits, reason,
                resumed=resume_session is not None, gate_output=gate_output, gates_at_chain_end=not each_ticket,
            )


def _hotspot_stop(
    vcs: VersionControl, journal: Journal, ticket: str, stack: Stack, worktree: Path, since: str, merge_base: bool, phase: str, round_number: int
) -> str | None:
    """Compare the paths that the specialist changed since `since` with the hotspot patterns of the stack.

    Return None when none matches, or the reason the step fails. A match is
    recorded as `hotspot-finding`. The caller stops the step before any gate
    and any verifier runs.
    """
    matches = hotspot_matches(changed_paths(vcs, worktree, since, merge_base=merge_base), stack.hotspots)
    if not matches:
        return None
    journal.append(
        "hotspot-finding", ticket=ticket, phase=phase, round=round_number,
        matches=[{"path": m.path, "pattern": m.pattern} for m in matches],
    )
    return "hotspot finding: " + "; ".join(f"{m.path} matches the hotspot {m.pattern!r}" for m in matches)


@dataclass(frozen=True)
class _Step:
    """The state that the verifier step and the fix-up rounds of one ticket share."""

    workflow: Workflow
    ticket: Ticket
    adapter: Adapter
    vcs: VersionControl
    gates: GateRunner
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

    A rebase that keeps the commit runs no gate again: the green results of the
    build hold for the same commit, and the journal records `gate-skip` for each
    gate (see `_run_gate_with_output`).

    Return None when the branch is rebased and the gates are green, or the
    reason the step fails. A rebase that stops is aborted, so the branch and its
    worktree keep the state from before the rebase. A rebase that git refuses to
    start, or one that ended with a failed hook, leaves nothing to abort.
    """
    ticket, run_branch = step.ticket, step.workflow.run_branch
    onto = _tip(step.vcs, step.repo, run_branch)
    before = _head(step.vcs, step.worktree)
    with _vcs_errors():
        rebase = step.vcs.rebase_onto(step.worktree, run_branch)
    if rebase.status != REBASED:
        files = list(rebase.conflicts)
        step.journal.append(
            "rebase", ticket=ticket.id, result="conflict" if files else "failed", onto_commit=onto,
            from_commit=before, to_commit=None, files=files,
        )
        if files:
            return f"rebase onto {run_branch} stopped with a conflict in {', '.join(files)}"
        return f"rebase onto {run_branch} failed: {rebase.detail}"
    after = _head(step.vcs, step.worktree)
    step.journal.append(
        "rebase", ticket=ticket.id, result="rebased", onto_commit=onto, from_commit=before, to_commit=after, files=[]
    )
    if not _commit_lines(step.vcs, step.repo, run_branch, step.branch):
        return (
            f"branch {step.branch} holds no commit beyond {run_branch} after the rebase; "
            f"{run_branch} holds its work already"
        )
    red = [
        gate for gate in step.stack.gates
        if not _run_gate(step.gates, step.vcs, gate, ticket.id, step.worktree, step.journal, 0, PHASE_REBASE)
    ]
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


def _accepted_commit(step: _Step, first_round: int = 0, first_body: str | None = None) -> str:
    """Verify the branch of the step blind, and return the commit that a verifier accepted.

    A REJECT starts a fix-up round, up to `FIXUP_ROUND_LIMIT` of the mode.
    Each round, and the first pass, spawns a fresh verifier. Raise `_Rejected`
    after the last round, and `_StepFailed` for any other cause.

    A resume sets `first_round` to the number of verdicts that the journal
    holds. The first pass of the resume is a full verification at that round
    number, and the limit counts across the stop. A retry that goes on at the
    scoped verifier gives its fix-up brief in `first_body`: the first pass is
    then the scoped verifier of the fix-up commit that the branch holds.
    """
    mode = step.workflow.mode
    used_up = domain.rounds_used_up_reason(mode, first_round)
    if used_up:
        raise _StepFailed(used_up)
    rejected = ""
    report: VerifierReport | None = None
    for round_number in domain.verification_rounds(mode, first_round):
        fixup_body = first_body if round_number == first_round else None
        if report is not None:
            fixup_body = _fixup_round(step, round_number, rejected, report.findings)
        verified = _tip(step.vcs, step.repo, step.branch)
        report = _verify_round(step, round_number, fixup_body)
        if domain.is_accepted(report.verdict):
            return verified
        rejected = verified
    assert report is not None
    raise _Rejected(domain.rejection_reason(mode, report.findings), report.findings)


def _fixup_round(step: _Step, round_number: int, rejected: str, findings: list[str]) -> str:
    """Run one fix-up round and return the scoped brief for the fresh verifier.

    The engine writes the findings to a file and sends the specialist a fix-up
    brief in the same worktree (see `_run_fixup`). The fix must be a new commit
    on top of the rejected commit, which the brief generator checks. The engine
    then runs the gates itself.
    """
    findings_text = _run_fixup(step, round_number, rejected, _findings_text(findings))
    brief = _checked_fixup_brief(step, round_number, rejected, findings_text)
    _fixup_gates(step, round_number)
    return brief


def _run_fixup(
    step: _Step, round_number: int, rejected: str, findings: str, red_gates: bool = False, fields: dict[str, object] | None = None
) -> str:
    """Write the findings to a file, and run the fix-up specialist in the worktree of the step. Return the findings text.

    `findings` are the findings of a REJECT, or with `red_gates` the output of
    the gates that were red on `rejected`. `fields` go in the `fixup-start`
    event too. Raise `_StepFailed` when the fix-up specialist does not end with
    a valid report that says committed.
    """
    ticket = step.ticket
    tier, model = step.roles["specialist"]
    findings_path = step.run_dir / "findings" / f"{ticket.id}.fixup-{round_number}.md"
    findings_path.parent.mkdir(parents=True, exist_ok=True)
    findings_path.write_text(findings)
    findings_text = findings_path.read_text()
    step.journal.append(
        "fixup-start", ticket=ticket.id, round=round_number, rejected_commit=rejected, findings_file=str(findings_path),
        agent=step.stack.specialist, tier=tier, model=model, **(fields or {}),
    )
    report_path = step.run_dir / "reports" / f"{ticket.id}.fixup-{round_number}.specialist.json"
    prompt = specialist_brief(
        ticket.id, ticket.text, step.worktree, step.stack.hotspots, step.stack.gates, report_path, rejected, findings_text,
        red_gates=red_gates,
    )
    stream = _start_agent(step.journal, ticket.id, ROLE_FIXUP, round_number, report_path)
    report_valid = _report_check(lambda: read_specialist_report(report_path, ticket.id))
    result = step.adapter.run(_request(step.stack.specialist, step.stack, model, prompt, step.worktree, report_path, stream, report_valid))
    _journal_idle(step.journal, result, ticket.id, ROLE_FIXUP, round_number)
    step.journal.append(
        "fixup-result", ticket=ticket.id, round=round_number, exit_status=result.exit_status, end_state=result.end_state,
        session_id=result.session_id, event_stream=str(result.event_stream), **_refused_fields(result),
    )
    if not _session_done(result, report_valid):
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
    unfinished = domain.unfinished_reason(report.status, report.blocked_reason, "the fix-up specialist")
    if unfinished:
        raise _StepFailed(unfinished)
    return findings_text


def _findings_text(findings: list[str]) -> str:
    """The findings of a verdict as the fix-up specialist and the scoped verifier get them: one list item for each."""
    return "".join(f"- {finding}\n" for finding in findings)


def _checked_fixup_brief(step: _Step, round_number: int, rejected: str, findings_text: str, gates_text: str | None = None) -> str:
    """Check the fix-up commit of a round, and return the scoped brief for the fresh verifier.

    The paths that the fix-up changed must match no hotspot, and the brief
    generator must accept the fix as a new commit on top of the rejected commit.
    Raise `_StepFailed` otherwise. `gates_text` is the text of the gates section
    of the brief when the engine ran the gates green (see `green_gates_text`).
    """
    ticket = step.ticket
    stop = _hotspot_stop(
        step.vcs, step.journal, ticket.id, step.stack, step.worktree, rejected, False, PHASE_FIXUP, round_number
    )
    if stop:
        raise _StepFailed(stop)
    try:
        return fixup_brief(step.vcs, step.repo, step.branch, rejected, findings_text, gate_commands=step.stack.gates, gates_text=gates_text)
    except BriefError as error:
        step.journal.append("fixup-refused", ticket=ticket.id, round=round_number, reason=str(error))
        raise _StepFailed(f"the fix-up of round {round_number} is refused: {error}") from None


def _fixup_gates(step: _Step, round_number: int) -> None:
    """Run the gates after the fix-up of a round. Raise `_StepFailed` when one is red."""
    red = [
        gate for gate in step.stack.gates
        if not _run_gate(step.gates, step.vcs, gate, step.ticket.id, step.worktree, step.journal, round_number, PHASE_FIXUP)
    ]
    if red:
        raise _StepFailed("gates red: " + "; ".join(red))


def _verify_round(
    step: _Step, round_number: int, fixup_body: str | None, gates_text: str | None = None, fixups: tuple[str, str] | None = None
) -> VerifierReport:
    """Run one fresh verifier in a new temporary copy and return its valid verdict report.

    With no `fixup_body` the pass is full. A fix-up round gets the fix-up brief in `fixup_body`.
    The verifier gets the task or the findings, and the diff or the delta, and
    the gates. It never gets the report of the specialist.

    For a chain, `gates_text` gives the output of the gates that the engine ran
    green on the tip, so the verifier does not run them, and `fixups` gives the
    fix-up commits of red gates that the full pass must see (see `chain_brief`).
    """
    ticket = step.ticket
    tier, model = step.roles["verifier"]
    mode = "full" if fixup_body is None else "fix-up"
    verified = _tip(step.vcs, step.repo, step.branch)
    evidence = step.run_dir / "verifier" / ticket.id
    if round_number:
        evidence = evidence / f"fixup-{round_number}"
    scratch = Path(tempfile.mkdtemp(prefix="delegate-verify-"))
    try:
        copy = scratch / "copy"
        report_path = scratch / "verdict.json"
        try:
            with _vcs_errors():
                step.vcs.make_verifier_copy(step.repo, copy, step.branch, step.workflow.run_branch)
            if step.segments is not None:
                # A chain: the brief holds the tickets of the stack, each with its own diff, and the finding labels.
                first_pass = (
                    chain_brief(step.vcs, copy, step.segments, step.stack.gates, gates_text, fixups)
                    if fixup_body is None else fixup_body + finding_labels_section()
                )
                prompt = first_pass + verifier_run_sections(copy, report_path, mode, engine_gates=gates_text is not None)
            elif fixup_body is None:
                prompt = verifier_run_brief(
                    step.vcs, copy, step.branch, step.workflow.run_branch, ticket.text, step.stack.gates, report_path
                )
            else:
                prompt = fixup_body + verifier_run_sections(copy, report_path, mode)
        except (EngineError, BriefError) as error:
            raise _StepFailed(f"the verifier could not start: {error}") from None
        step.journal.append(
            "verify-start", ticket=ticket.id, round=round_number, agent=step.stack.verifier, tier=tier, model=model,
            commit=verified, copy=str(copy), **step.chain_fields,
        )
        before = snapshot_worktree(step.vcs, step.worktree)
        # The stream goes in the evidence directory from the start, outside the copy, so it stays when the copy goes.
        stream = _start_agent(step.journal, ticket.id, ROLE_VERIFIER, round_number, evidence / report_path.name)
        # The check reads the report where the verifier writes it, in the scratch directory; `report_path` moves later.
        in_scratch = report_path
        report_valid = _report_check(lambda: read_verifier_report(in_scratch, mode))
        result = step.adapter.run(_request(step.stack.verifier, step.stack, model, prompt, copy, report_path, stream, report_valid))
        # The check runs at once, before the evidence is moved; the journal and the halt come after it.
        violation: InvariantViolation | None = None
        try:
            check_worktree_unchanged(step.vcs, before)
        except InvariantViolation as found:
            violation = found
        # The copy goes; the verdict report and the event stream stay as evidence of the run.
        evidence.mkdir(parents=True, exist_ok=True)
        for entry in scratch.iterdir():
            if entry != copy:
                shutil.move(str(entry), evidence / entry.name)
    finally:
        step.vcs.stop_file_watcher(scratch / "copy")
        shutil.rmtree(scratch, ignore_errors=True)
    report_path = evidence / report_path.name
    stream = result.event_stream
    if stream.is_relative_to(scratch):
        stream = evidence / stream.relative_to(scratch)
    _journal_idle(step.journal, result, ticket.id, ROLE_VERIFIER, round_number)
    step.journal.append(
        "verify-result", ticket=ticket.id, round=round_number, exit_status=result.exit_status, end_state=result.end_state,
        session_id=result.session_id, event_stream=str(stream), **_refused_fields(result),
    )
    if violation is not None:
        step.journal.append(
            "invariant-violation", ticket=ticket.id, round=round_number, worktree=str(violation.worktree),
            head_before=violation.head_before, head_after=violation.head_after, changes=violation.changes,
        )
        raise violation
    # A verifier that could not write its verdict file (a permission mode that refuses a shell redirect, for example)
    # ends its final message with the verdict object, as the brief asks. The engine writes that object to the report
    # path and checks it as it checks a file (#98).
    source: dict[str, object] = {}
    ended = result.end_state == harness.FINISHED
    recovered = verdict_in_text(result.final_text) if ended and not report_path.exists() and result.final_text else None
    if recovered is not None:
        report_path.write_text(json.dumps(recovered, indent=2) + "\n")
        source = {"source": "final-text"}
    if not _session_done(result, _report_check(lambda: read_verifier_report(report_path, mode))):
        raise _StepFailed(f"the verifier ended {result.end_state} with exit status {result.exit_status}")
    try:
        report = read_verifier_report(report_path, mode)
    except ReportError as error:
        step.journal.append(
            "verify-report", ticket=ticket.id, round=round_number, valid=False, path=str(report_path), reason=str(error),
            **source,
        )
        raise _StepFailed(str(error)) from None
    step.journal.append(
        "verify-report", ticket=ticket.id, round=round_number, valid=True, path=str(report_path), reason=None, **source
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
    run_tip = _tip(step.vcs, step.repo, run_branch)
    with _vcs_errors():
        moved = step.vcs.fast_forward_branch(step.repo, run_branch, verified)
    if not moved:
        return (
            f"run branch {run_branch} cannot fast-forward to {step.branch}: "
            f"it holds a commit that {step.branch} does not hold"
        )
    step.journal.append(
        "run-branch-advance", ticket=step.ticket.id, run_branch=run_branch, from_commit=run_tip, to_commit=verified
    )
    return None


def _retry_ticket(step: _Step, plan: _RetryPlan, failure: str, start_ref: str) -> str | None:
    """Go on with a failed step at the phase of `plan`. Return None when it is built, or the reason it failed.

    `start_ref` is where the branch of the ticket starts if it is gone (see `_start_ref`).

    The step uses its worktree and its commits again, and the engine writes
    `retry` with the phase where the step goes on, and the reason it failed
    (`failure`). A step that failed before any verifier goes on at the gates of
    the first build, as an open step does after a stop. Otherwise:

    - The branch must hold the tip of the run branch. If it does not, the run
      branch moved after the step failed: the engine rebases, runs the gates,
      and starts a full verifier at the round of the plan. A fix-up commit is
      checked against the hotspots and the brief generator before the rebase.
    - The verifier phase starts a full verifier at round 0.
    - The scoped verifier phase checks the fix-up commit again, and starts the
      scoped verifier of the fix-up round.
    - The gates of a fix-up check the fix-up commit and run the gates, then the
      scoped verifier starts.

    In `economy` mode the step goes on as an open step does after a stop, at
    the specialist or at the check of its report (see `_build_ticket`).
    """
    workflow, ticket, vcs = step.workflow, step.ticket, step.vcs
    if workflow.mode == domain.ECONOMY:
        step.journal.append("retry", ticket=ticket.id, phase=plan.phase, round=plan.round, reason=failure)
        return _build_ticket(
            workflow, ticket, step.adapter, vcs, step.gates, step.roles, step.repo, step.state_dir, step.run_id, step.journal,
            start_ref, list(plan.prior), retried=failure,
        )
    if plan.phase == RETRY_GATES and plan.rejected is None:
        step.journal.append("retry", ticket=ticket.id, phase=RETRY_GATES, round=plan.round, reason=failure)
        return _build_ticket(
            workflow, ticket, step.adapter, vcs, step.gates, step.roles, step.repo, step.state_dir, step.run_id, step.journal,
            start_ref, list(plan.prior),
        )
    branch_ref = f"refs/heads/{step.branch}"
    on_tip = _holds(vcs, step.repo, _tip(vcs, step.repo, workflow.run_branch), branch_ref)
    fixup_held = plan.rejected is not None and _holds(vcs, step.repo, plan.rejected, branch_ref)
    goes_on = plan.phase if on_tip and (plan.rejected is None or fixup_held) else RETRY_GATES
    step.journal.append("retry", ticket=ticket.id, phase=goes_on, round=plan.round, reason=failure)
    _prepare_worktree(vcs, step.repo, step.worktree, step.branch, start_ref, step.state_dir / "hooks" / step.run_id / ticket.id)
    try:
        if plan.rejected is not None and fixup_held:
            brief = _checked_fixup_brief(step, plan.round, plan.rejected, _findings_text(list(plan.findings)))
            if on_tip:
                if plan.phase == RETRY_GATES:
                    _fixup_gates(step, plan.round)
                return _advance_run_branch(step, _accepted_commit(step, plan.round, brief))
        elif goes_on == RETRY_VERIFIER:
            return _advance_run_branch(step, _accepted_commit(step, plan.round))
        reason = _rebase_onto_run_branch(step)
        if reason:
            return reason
        return _advance_run_branch(step, _accepted_commit(step, plan.round))
    except _StepFailed as failed:
        return str(failed)


class _ChainRun:
    """The gates and the verification at the end of an `economy` chain.

    The chain holds the tickets that were built, in the order of the plan. Each
    ticket branch starts from the tip of the previous one, so the last branch
    holds all the work. For each stack of the chain, in the order of its first
    ticket, the engine first runs the gates of the stack once, on the chain tip,
    in the worktree of the last ticket. Red gates start a fix-up round with the
    output of the red gates, and the gates run again. Green gates go on to one
    verifier over the commits of the tickets of that stack: the brief holds a
    diff for each of those tickets, none for a ticket of another stack, and the
    output of the green gates. The verifier works in a copy of the tip.

    A REJECT starts a fix-up round too: a new commit on the chain tip, in the
    worktree of the last ticket, then the gates, then a fresh scoped verifier.
    The rounds of red gates and of a REJECT count together against
    `FIXUP_ROUND_LIMIT`. Red gates or a REJECT after the last round fail the
    stack. A finding maps to a ticket by its label (see `map_findings`). The
    tickets that the findings name fail with their findings. When no finding
    names a ticket, or the gates stay red, every ticket of the stack fails.
    The stacks that the engine did not verify yet fail too: their tickets are
    not verified, so no one may rely on them. The run branch then does not move.

    When every stack is accepted, the engine moves the run branch to the chain
    tip by fast-forward.

    The verification covers only the built tickets whose work the run branch
    does not hold yet, and reads only the journal events after the last
    `retry`. So after a retry, the chain verification of the run before it
    does not count, and the tickets that it accepted are not verified again.
    """

    def __init__(
        self, workflow: Workflow, order: list[Ticket], adapter: Adapter, vcs: VersionControl, gates: GateRunner,
        roles: dict[str, tuple[str, str]], repo: Path, state_dir: Path, run_id: str, journal: Journal, built: list[str],
        failed: list[str], failures: dict[str, str],
    ) -> None:
        self.workflow, self.adapter, self.vcs, self.gates, self.roles, self.repo = workflow, adapter, vcs, gates, roles, repo
        self.state_dir, self.run_id, self.journal = state_dir, run_id, journal
        self.chain = [ticket for ticket in order if ticket.id in built]
        self.built, self.failed, self.failures = built, failed, failures

    def verify(self) -> list[str]:
        """Verify every stack of the chain and move the run branch. Return the findings that map to no ticket."""
        try:
            run_branch = f"refs/heads/{self.workflow.run_branch}"
            self.chain = [
                ticket for ticket in self.chain
                if not _holds(self.vcs, self.repo, f"refs/heads/{_ticket_branch(self.workflow, ticket)}", run_branch)
            ]
            if not self.chain:
                return []  # the run branch holds the work of every built ticket
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

    def _segments(self, events: list[dict[str, object]], since_retry: list[dict[str, object]]) -> dict[str, ChainSegment]:
        """For each ticket of the chain, the commits that it added: from where its branch started to its tip.

        The start is the `base_commit` of the newest `step-start` of the ticket in
        `events`. The tip is the tip of the branch now. For the last ticket of the
        chain, a resume after a fix-up takes the commit that the first fix-up of
        the chain since the last retry (in `since_retry`) started from, so the
        diff of the ticket holds no fix-up commit.
        """
        fixups = [e for e in since_retry if e["event"] == "fixup-start" and "stack" in e]
        segments: dict[str, ChainSegment] = {}
        for ticket in self.chain:
            base = [str(e["base_commit"]) for e in events if e["event"] == "step-start" and e["ticket"] == ticket.id][-1]
            tip = _tip(self.vcs, self.repo, _ticket_branch(self.workflow, ticket))
            if fixups and ticket is self.chain[-1]:
                tip = str(fixups[0]["rejected_commit"])
            segments[ticket.id] = ChainSegment(ticket.id, ticket.text, base, tip)
        return segments

    def _verify_stacks(self) -> list[str]:
        every_event = read_events(self.journal.path)
        retries = [index for index, e in enumerate(every_event) if e["event"] == "retry"]
        events = every_event[retries[-1] + 1 :] if retries else every_event  # the verification of the chain since the last retry
        if any(e["event"] == "run-branch-advance" for e in events):
            return []  # a resume after the run branch moved: nothing is left to verify
        tip_ticket = self.chain[-1]
        segments = self._segments(every_event, events)
        stacks = list(dict.fromkeys(ticket.stack for ticket in self.chain))
        last: _Step | None = None
        for position, name in enumerate(stacks):
            tickets = [ticket for ticket in self.chain if ticket.stack == name]
            verdicts = [e["verdict"] for e in events if e["event"] == "verdict" and e.get("stack") == name]
            step = _Step(
                self.workflow, Ticket(tickets[-1].id, _chain_text(tickets), name, []), self.adapter, self.vcs, self.gates, self.roles,
                self.repo, self.state_dir, self.run_id, self.journal, tip_ticket, tuple(segments[ticket.id] for ticket in tickets),
            )
            last = step
            if verdicts and domain.is_accepted(verdicts[-1]):
                continue  # a resume: an earlier run accepted this stack already
            try:
                self._verify_stack(step, [e for e in events if e["event"] == "fixup-start" and e.get("stack") == name])
            except _StepFailed as failed:
                return self._reject(failed, tickets, stacks[position + 1 :])
        assert last is not None
        reason = _advance_run_branch(last, _tip(self.vcs, self.repo, last.branch))
        if reason:
            self._fail({ticket.id: reason for ticket in self.chain})
        return []

    def _verify_stack(self, step: _Step, fixup_starts: list[dict[str, object]]) -> None:
        """Run the gates of one stack on the chain tip, the fix-up rounds, and the verifiers, until a verifier accepts.

        Red gates start a fix-up round with the gate output. Green gates go on to
        the verifier: the full verifier when no verifier rejected the stack, and
        else the scoped verifier of the delta from the rejected commit. A REJECT
        starts a fix-up round. Raise `_Rejected` when the verifier rejects after
        the last fix-up round, and `_StepFailed` for any other cause, also for
        red gates after the last round.

        A resume gives the `fixup-start` events of the stack in `fixup_starts`:
        each counts as a round. The resume goes on at the gates, and its first
        verifier is a full pass, which also sees the fix-up commits.
        """
        mode = self.workflow.mode
        limit = domain.fixup_round_limit(mode)
        round_number = len(fixup_starts)
        used_up = domain.rounds_used_up_reason(mode, round_number)
        if used_up:
            raise _StepFailed(used_up)
        # The full verifier also sees each fix-up commit beyond this commit: the tip before the first fix-up of the stack.
        start = str(fixup_starts[0]["rejected_commit"]) if fixup_starts else _tip(self.vcs, self.repo, step.branch)
        rejected: str | None = None  # the commit that the last verifier rejected
        findings: list[str] = []
        while True:
            tip = _tip(self.vcs, self.repo, step.branch)
            outcomes = [
                (gate, *_run_gate_with_output(step.gates, step.vcs, gate, step.ticket.id, step.worktree, step.journal, round_number, PHASE_CHAIN))
                for gate in step.stack.gates
            ]
            red = [(gate, output) for gate, green, output in outcomes if not green]
            if red:
                if round_number >= limit:
                    raise _StepFailed(domain.chain_gates_reason(mode, [gate for gate, _ in red]))
                round_number += 1
                self._fixup(step, round_number, tip, _red_gates_text(tip, red), True)
                continue
            gates_text = green_gates_text(tip, [(gate, output) for gate, _, output in outcomes])
            if rejected is None:
                report = _verify_round(step, round_number, None, gates_text, (start, tip) if start != tip else None)
            else:
                body = _checked_fixup_brief(step, round_number, rejected, _findings_text(findings), gates_text)
                report = _verify_round(step, round_number, body, gates_text)
            if domain.is_accepted(report.verdict):
                return
            if round_number >= limit:
                raise _Rejected(domain.rejection_reason(mode, report.findings), report.findings)
            rejected, findings = tip, report.findings
            round_number += 1
            self._fixup(step, round_number, tip, _findings_text(findings), False)

    def _fixup(self, step: _Step, round_number: int, rejected: str, findings: str, red_gates: bool) -> None:
        """Run one fix-up round on the chain tip, and check its commit. The gates of the chain run after it."""
        trigger = TRIGGER_GATES_RED if red_gates else TRIGGER_REJECT
        findings_text = _run_fixup(step, round_number, rejected, findings, red_gates, {**step.chain_fields, "trigger": trigger})
        _checked_fixup_brief(step, round_number, rejected, findings_text)

    def _reject(self, failed: _StepFailed, tickets: list[Ticket], unverified: list[str]) -> list[str]:
        """Fail the tickets of a stack that did not pass, and the tickets of the stacks after it. Return the unmapped findings."""
        mapped: dict[str, list[str]] = {}
        unmapped: list[str] = []
        if isinstance(failed, _Rejected):
            mapped, unmapped = map_findings(failed.findings, [ticket.id for ticket in tickets])
        reasons = {
            ticket_id: domain.chain_rejection_reason(self.workflow.mode, findings) for ticket_id, findings in mapped.items()
        }
        if not reasons:
            reasons = {ticket.id: str(failed) for ticket in tickets}
        stack = tickets[0].stack
        for ticket in self.chain:
            if ticket.stack in unverified:
                reasons[ticket.id] = domain.unverified_chain_reason(stack)
        self._fail(reasons)
        return unmapped


def _red_gates_text(commit: str, red: list[tuple[str, str]]) -> str:
    """The findings of a fix-up round for red gates: each red gate on `commit`, and its output."""
    gates = "".join(f"- The gate `{gate}` is red.\n" for gate, _ in red)
    output = "\n\n".join(f"$ {gate}\n{text.rstrip(chr(10))}" for gate, text in red)
    return f"The engine ran the gates on the tip of the chain, the commit `{commit}`.\n\n{gates}\nThe output of the red gates:\n\n```\n{output}\n```\n"


def _chain_text(tickets: list[Ticket]) -> str:
    """The text of a stack's tickets for the fix-up specialist: the text of one ticket, or each ticket with its id."""
    if len(tickets) == 1:
        return tickets[0].text
    return "\n\n".join(f"Ticket {ticket.id}: {ticket.text.rstrip(chr(10))}" for ticket in tickets)


def _run_gate(
    gates: GateRunner, vcs: VersionControl, command: str, ticket: str, worktree: Path, journal: Journal, round_number: int, phase: str
) -> bool:
    """Run one gate command in the worktree through `gates`, record the result, and return True when it is green.

    `round_number` is 0 for the first build and the fix-up round for a later run.
    `phase` is one of PHASE_BUILD, PHASE_REBASE and PHASE_FIXUP.
    """
    return _run_gate_with_output(gates, vcs, command, ticket, worktree, journal, round_number, phase)[0]


def _run_gate_with_output(
    gates: GateRunner, vcs: VersionControl, command: str, ticket: str, worktree: Path, journal: Journal, round_number: int, phase: str
) -> tuple[bool, str]:
    """Run one gate command as `_run_gate` does. Return whether it is green, and the tail of its output.

    `phase` can also be PHASE_CHAIN: the gates on the tip of an `economy` chain.

    A gate runs at most once green on one commit. When the journal holds a green
    `gate-result` of the same command on the commit that the worktree has
    checked out, the engine does not run the gate again: it writes `gate-skip`
    with that commit and the `seq` of the green result, and the gate counts as
    green. A rebase that keeps the commit, and a resume after a stop, so cost no
    second gate run. The output of a skipped gate is the output of that green result.

    Before a gate runs, the engine makes an empty log file for it and writes
    `gate-start` with the path of the file. The gate runner appends the output
    to the file while the gate runs, so `delegate status` and `delegate watch`
    see the activity of a long gate.
    """
    commit = _head(vcs, worktree)
    green = _green_result(journal, command, commit)
    if green is not None:
        green_seq = int(green["seq"])  # type: ignore[call-overload]
        journal.append(
            "gate-skip", ticket=ticket, round=round_number, phase=phase, command=command, commit=commit, green_seq=green_seq,
            reason=f"the journal holds a green result of this gate on commit {commit} (seq {green_seq})",
        )
        return True, str(green.get("output_tail", ""))
    log = _gate_log_path(journal, ticket, phase, round_number)
    journal.append("gate-start", ticket=ticket, round=round_number, phase=phase, command=command, gate_log=str(log))
    result = gates.run(command, worktree, log=log)
    output = result.output[-GATE_OUTPUT_TAIL_CHARS:]
    journal.append(
        "gate-result", ticket=ticket, round=round_number, phase=phase, command=command, commit=commit,
        exit_status=result.exit_status, green=result.green, output_tail=output,
    )
    return result.green, output


def _gate_log_path(journal: Journal, ticket: str, phase: str, round_number: int) -> Path:
    """Make a new, empty log file for one gate run, and return its path.

    The file is `gates/<ticket>/<phase>-<round>.log` in the run directory, then
    `<phase>-<round>-2.log`, and so on. It is never an earlier log.
    """
    directory = journal.path.parent / GATE_LOG_DIRECTORY / ticket
    directory.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        path = directory / (f"{phase}-{round_number}.log" if number == 1 else f"{phase}-{round_number}-{number}.log")
        try:
            path.touch(exist_ok=False)
        except FileExistsError:
            number += 1
            continue
        return path


def _refused_fields(result: AdapterResult) -> dict[str, object]:
    """The `refused` field of a result event: each tool that a permission mode refused, with the mode. Empty when none."""
    if not result.refused:
        return {}
    return {"refused": [{"tool": tool, "mode": mode} for tool, mode in result.refused]}


def _green_result(journal: Journal, command: str, commit: str) -> dict[str, object] | None:
    """The first green `gate-result` of `command` on `commit` in the journal, or None when it holds none."""
    for event in read_events(journal.path):
        if event["event"] == "gate-result" and event["green"] and event["command"] == command and event.get("commit") == commit:
            return event
    return None
