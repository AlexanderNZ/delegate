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
second time. The verifier brief gives the output of the green gates on the
rebased commit, so the verifier does not run the gates again. On ACCEPT the
engine fast-forwards the run branch to the verified commit. On REJECT the engine starts a fix-up round: it writes the
findings to a file, sends the specialist a fix-up brief in the same worktree,
requires the fix as a new commit on top of the rejected commit, runs the gates,
and spawns a fresh verifier with the scoped brief (the findings, the delta
from the rejected commit, and the output of the green gates on the fix-up
commit). After `FIXUP_ROUND_LIMIT` rounds a REJECT or red gates fail the step, and the run branch does not move for it.
The run branch moves only on ACCEPT.

The engine checks that. The tip that the journal says is the `to_commit` of the newest `run-branch-advance`, or the
`base_commit` of `run-start`. Before each rebase onto the run branch, before each fast-forward, before the chain
verification and at each resume, the engine compares it with the real tip (see `_check_run_branch`). A difference
writes `run-branch-moved` and ends the run as an invariant violation does: the run cannot be resumed.

The engine records the HEAD and the status of the worktree of the ticket before
and after each verifier run (see `guards.py`). A difference is an invariant
violation: the journal records it with the changes, and the run halts. Before
each verifier run, the engine also makes the proof copy beside the temporary
copy: the tree of the copy with no `.git` entry, where the verifier breaks a
check for a red proof. The engine removes both copies after every verifier run.

Each agent session gets a scratch directory (#227). The engine makes it before the session, adds it to the session,
sets `TMPDIR` to it, and the brief names it. A host can refuse a write to `/tmp`, and a scratch file in the tree changes
the work, so the agent writes its scratch files there. A specialist and a fix-up get `scratch/<ticket>/<role>/` in the
directory of the run; these directories stay after the run, and `delegate clean` removes them. A verifier gets a new
private directory in the temp directory of the host, outside the directory of the run, which holds the report of the
specialist (#294). The engine removes it when the session ends, and `delegate clean` removes one that a kill left.

In `economy` mode each ticket branch starts from the tip of the previous built
ticket, and the engine runs no gate and no verifier for a ticket by itself: a
valid report that says committed, on a branch with a commit, builds the step.
After the last ticket, `chain.ChainRun` runs the gates of each stack once on the
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
only closes it. In `assure` mode, a step whose newest verdict is ACCEPT, and whose accepted commit the run branch
holds, is closed the same way: the process stopped after it moved the run branch and before it wrote
`run-branch-advance`, so the resume writes that event with `recovered: true` (see `_recover_advance`). In `economy` mode
the resume does the same for the chain, before it plans more work: when the newest verdict of the chain is ACCEPT and
the run branch holds its commit (see `_recover_chain_advance`). Any other open step restarts in its own worktree, which the
engine makes again if it is gone. A specialist whose report the journal shows as
valid is not spawned again. A verifier run that ended with no verdict does not
count against the fix-up limit: the resume runs that round again.

A resume can change the plan of the run: add tickets after the built part, and
change or remove tickets that have not started. The rules are in
`domain.check_plan_change`, and `review_workflow` applies them to the newest plan of the
run, from the journal. A refused change changes nothing. An accepted change
writes `plan-change`, also goes on with a run that ended, and gives the brief of
each later ticket the section "Plan change" (see `_plan_change_note`).

A resume with `retry` goes on with one failed step, also in a run that ended
(see `retry.retry_plan`). It uses the worktree and the commits of the step again,
and goes on at the first phase that did not complete. In `assure` mode that
phase is the gates, the verifier, or the scoped verifier, and no specialist
spawns. After a valid report of the specialist that says partial or blocked,
and names no hotspot, the phase is the specialist, which continues in the
worktree with the continuation limit of the mode again (#284). When the run
branch moved after the step failed, the retry rebases, runs the gates, and
verifies the whole branch. In `economy` mode the phase is
the specialist, which continues with the continuation limit of the mode again,
or the check of its report. The tickets after the step that the run skipped are
checked again, and the chain then goes on: the verification of the chain
covers the built tickets whose work the run branch does not hold. In `assure`
mode, the tickets that the step made the engine skip are checked again with
the skip rule (see `domain.skipped_because_of`), so a ticket that only the
step blocked is built after it, in plan order. The journal records `retry`,
and then a new `step-end` and a new `run-end`. The newest of each wins. A retry
can also change the text of its own failed ticket, and only the text (rule 10 of
a plan change): `plan-change` then holds the old and the new `text_sha256`. Such a retry does not use
the worktree and the commits again: it starts the ticket again from its base, with a new specialist, and
keeps the old branch as `<branch>-failed-<n>` (see `_restart_step`).

A retry can name a commit of the coordinator (#225): a commit at the tip of the branch of the ticket that changes only
hotspot paths, for example a new render of the agent pair (see `retry.check_coordinator_commit`). The `retry` event records it with
its paths. The hotspot check of the step does not count its change, and counts each other commit of the step (see
`_hotspot_stop`). The gates run on it before a verifier, and each verifier brief of the step names it. In `economy`
mode the retry of the ticket at the tip of a chain whose verification failed goes on with the verification of the chain,
with such a commit or with none (#269, see `retry._chain_retry_plan`). In `assure` mode it also lets the retry of a step go on whose specialist, or whose fix-up specialist of a round, gave a
valid report that says partial or blocked, because the work needs a hotspot (#264, see `retry.retry_plan`).

A run that ends with a failed step or a skipped ticket ends with the result
`failed`.

A run can pause. `delegate pause` writes a request file in the directory of the
run (see `pause.py`), and the engine reads it at each step boundary: before a
step starts, before a gate runs, and before a verifier starts (see
`pause_point`). It stops there and does not cut off an agent. The journal
records `pause-requested` and `paused`, the run writes no `run-end`, and a
resume goes on from the boundary as from a stop.

An agent session runs from the sealed agent of the run (#217). At the start of the run, the engine seals each agent
that a role names, from the main checkout, into `<run directory>/agents/<agent>/`, and `run-start` records the seal: the
path and the SHA-256 of each file. Before each agent session the engine hashes each file of the seal. A hash that differs
from the record, or a missing file, fails the step before the agent starts, and the journal records `agents-changed`
with the tree `seal` (see `_check_agent_file`). It reads no agent file in the tree of the agent. A resume with
`--agents-changed` seals again from the main checkout, and records the new seal in `resume`.

A run starts no agent session before the guard probe of each verifier shows a denial (#219, see `probe.py`). After
`run-start` or `resume` and before the first step, the engine starts one probe session for each verifier whose seal has no
denied `guard-probe` yet, and the journal records the result. A result other than `denied` raises an `EngineError` that
names the agent, the result and the limits page. The run has no `run-end`, and a resume probes again. A resume that seals
nothing, with a denied probe for each verifier, probes nothing (see `_probe_guards`).

A failure of the harness halts the run. An `AdapterError` from `Adapter.run` is a
failure of the harness, not of the work: a harness that is not logged in, a CLI
that is gone, a rate limit. The engine writes `halted` (the ticket, the role,
the round and the message), and no `step-end` and no `run-end`. The run process
ends and releases the lock, and a resume goes on at the open step, as after a
pause. Each other exception is a crash.

A crash stops the run, and leaves the step open (#281). The journal and git hold
the state of the step, as after a kill. The engine writes the traceback to a
file of the run directory, and writes `crashed` (the ticket, the phase, the type
and the message of the exception, and the path of the file), and no `step-end`
and no `run-end` (see `_record_crash`). A resume goes on at the open step. An
invariant violation and a moved run branch are no crash: they end the run.

Ctrl-C (SIGINT) stops a run at once, and does not wait for a step boundary. The
engine releases the lock, records `interrupted` in the journal, and raises
`RunInterrupted`. The run writes no `run-end`, so a resume goes on as after a
crash.

The rules of a run are in `domain.py`: the modes, the limits, the skip rule, the
start of a ticket in a chain, and the handling of a verdict. The engine reads
and writes the world, and asks that module what the rules say.

Standard library only. The engine starts no process: the version-control port
keeps the work of a run, the harness port runs the agents, and the gate port
runs the gate commands. `tests/test_architecture.py` checks that no module of
the run context imports `subprocess`.
"""

from __future__ import annotations

import json
import secrets
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from traceback import format_exception
from typing import NamedTuple

from ..ports import harness
from ..ports.gates import GateRunner
from ..ports.harness import Adapter, AdapterError, AdapterRequest, AdapterResult, SealedAgent
from ..ports.vcs import REBASED, VcsError, VersionControl
from ..shared.tiers import Tiers
from . import chain, domain, run_state, step_scans
from .context import ResumeRequest, RunContext
from .agents import agent_names, check_agent, denied_agents, recorded_agents, seal_agents, unpermitted_gates, verifier_mcp_tools
from .brief import (
    BriefError, ChainSegment, Continuation, GateLogCopy, SpecialistInputs, VerifierInputs, VerifierSession, chain_brief,
    continuation_brief, coordinator_section, finding_labels_section, fixup_brief, green_gates_text, notes_section,
    plan_change_section, plan_change_text, red_proof_copy, report_correction_brief, specialist_brief, verifier_run_brief,
    verifier_run_sections,
)
from .cleanup import TicketTree, clean_tickets, remove_empty_directories
from .errors import EngineError, Rejected, StepFailed
from .events import (
    CRASH_PHASE_CHAIN, CRASH_PHASE_SETUP, CRASHED, JOURNAL_CHANGED, RUN_START, SCHEMA_VERSION, SETUP_PROBLEM, STACK_CHANGE, UNSKIP,
    ROLE_SPECIALIST, ROLE_FIXUP, ROLE_VERIFIER,
    GATE_PHASE_BUILD, GATE_PHASE_REBASE, GATE_PHASE_FIXUP,
    TREE_WORKTREE, TREE_VERIFY_COPY, TREE_SEAL,
    TRIGGER_GATES_RED, TRIGGER_REJECT, TRIGGER_REPORT_INVALID, TRIGGER_REPORT_MISSING, TRIGGER_UNCOMMITTED,
    RETRY_PHASE_SPECIALIST, RETRY_PHASE_GATES, RETRY_PHASE_VERIFIER, RETRY_PHASE_SCOPED_VERIFIER, RETRY_PHASE_FIXUP, RETRY_PHASE_CHAIN_FIXUP, RETRY_PHASE_CHAIN,
    PAUSED_BEFORE_STEP, PAUSED_BEFORE_VERIFIER,
)
from .gate_run import copy_gate_logs, gate_log_path, gate_outcomes, green_result, red_gates_text, run_gate_with_output
from .guards import (
    GuardError, InvariantViolation, changed_paths, check_worktree_unchanged, hotspot_matches, install_push_guard,
    snapshot_worktree, WorktreeSnapshot,
)
from .host import host_name
from .journal import EventObserver, Journal, JournalChanged, JournalError, crash_where, read_events
from .lock import LockError, LockHolder, check_free, run_lock
from .probe import PROBE_TIER, DENIED, describe_tries, probe_guard, refusal, stream_path
from .plan_review import PlanReview, SkippedTicket, configuration_record, plan_of, plan_record, recorded_configuration, review_workflow
from .progress import Progress, restore_progress
from .reports import ReportError, SpecialistReport, VerifierReport, map_findings, read_specialist_report, read_verifier_report, verdict_in_text
from .retry import (
    RetryPlan, check_chain_hotspots, check_coordinator_commit, coordinator_commits, paths_without, cannot_be_retried, retry_outcome, retry_plan, rounds_used,
    specialist_committed, unfinished_report,
)
from .state_location import (
    CRASH_FILE_PREFIX, JOURNAL_NAME, PAUSE_REQUEST_NAME, SCRATCH_DIRECTORY, VERIFIER_SCRATCH_PREFIX, VERIFY_COPY_NAME, VERIFY_SCRATCH_PREFIX,
    StateLocation, locate_state, record_state_root,
)
from .step_pause import Paused, pause_point
from .time_limits import minutes_text, seconds
from .vcs_access import branch_tip, head_commit, holds_commit, vcs_errors, worktree_environment
from .workflow import Stack, Ticket, Workflow, plan_order, specialist_tier, ticket_branch

# The phase of a step that an event of the step starts, for the `phase` of a `crashed` event. An `agent-start` starts
# the phase of its role, and a valid `report-validation` starts the gates.
CRASH_PHASE_OF_EVENT: dict[str, str] = {
    "step-start": domain.PHASE_SPECIALIST, "continuation": domain.PHASE_SPECIALIST, "rebase": domain.PHASE_GATES,
    "gate-start": domain.PHASE_GATES, "verify-start": domain.PHASE_VERIFIER, "fixup-start": domain.PHASE_FIXUP,
}
CRASH_PHASE_OF_ROLE: dict[str, str] = {
    ROLE_SPECIALIST: domain.PHASE_SPECIALIST, ROLE_VERIFIER: domain.PHASE_VERIFIER, ROLE_FIXUP: domain.PHASE_FIXUP,
}

BUILT: str = "built"
FAILED: str = "failed"
SKIPPED: str = "skipped"

# The temp prefixes that each verifier guard permits, whatever the value of `TMPDIR` (`VERIFIER_TEMP_PATH_PREFIXES` of
# the definitions context; a `/private` before them counts as no prefix). The run context does not import that context,
# so the prefixes are text here, and a test of the scratch directory checks them on a host whose temp directory is
# outside them.
GUARD_TEMP_PREFIXES: tuple[str, ...] = ("/tmp/", "/var/folders/")

# The environment entry of a `prepare` command: the absolute path of the main checkout of the repository of the run (#265).
PREPARE_REPO_VARIABLE: str = "DELEGATE_REPO"


class RunInterrupted(Exception):
    """Ctrl-C (SIGINT) stopped the run. The lock of the run branch is released.

    `journal` is the path of the journal, which holds `interrupted` as its last event, so a resume goes on. It is None
    when the run stopped before it wrote its journal: then there is nothing to resume.
    """

    def __init__(self, run_id: str, journal: Path | None) -> None:
        super().__init__(f"run {run_id} was interrupted")
        self.run_id, self.journal = run_id, journal


class _Halted(Exception):
    """The harness failed: `Adapter.run` raised an `AdapterError`. The journal holds `halted`. The message is the error."""

    def __init__(self, ticket: str, role: str, round_number: int, message: str) -> None:
        super().__init__(message)
        self.ticket, self.role, self.round_number = ticket, role, round_number


class _RunBranchMoved(Exception):
    """The run branch is not where the journal says. The journal holds `run-branch-moved`. The message is the reason the run ends."""


@dataclass(frozen=True)
class RunResult:
    run_id: str
    journal: Path
    built: list[str]
    failed: list[str]
    failures: dict[str, str]
    skipped: dict[str, str]
    unmapped: list[str] = field(default_factory=list)
    # The old holder that the free lock file named, for example a run that a kill ended, which the run took over (#288).
    taken_over: LockHolder | None = None
    # Where a pause request stopped the run, for example `before the verifier of ticket a`, or None when it did not pause.
    paused: str | None = None
    # The message of the `AdapterError` that halted the run, or None when it did not halt. `halted_at` says where.
    halted: str | None = None
    halted_at: str | None = None
    # `<exception type>: <message>` of a crash that stopped the run, or None (#281). `crashed_at` says where, and
    # `traceback` is the file that holds the traceback, or None when the engine could not write it.
    crashed: str | None = None
    crashed_at: str | None = None
    traceback: str | None = None
    # The ticket of the step of a `--retry` that the crash stopped, or None: `--retry` goes on with it again.
    crashed_retry: str | None = None
    # The note of a resume that recorded the hashes of the agent files because its journal holds no record, or None (#205).
    agents_note: str | None = None

    @property
    def ok(self) -> bool:
        # A skipped ticket is work that the run did not build, so a run with one is not a success.
        return not self.failed and not self.skipped


def new_run_id() -> str:
    """A new run id: the UTC time and four hexadecimal characters, for example `20261008T093837Z-e9dc`."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)


def _tier_model(tiers: Tiers, column: str, tier: str, who: str) -> tuple[str, str]:
    """The tier and its model from the adapter's own tier column. `who` names what needs the tier in the message."""
    try:
        return tier, tiers.model_for(tier, column)
    except KeyError:
        raise EngineError(f"the tier table has no {column!r} column for tier {tier!r}") from None
    except ValueError:
        raise EngineError(f"the tier table has no tier {tier!r}, which {who} needs") from None


def _roles(workflow: Workflow, tiers: Tiers, column: str) -> dict[str, tuple[str, str]]:
    """The tier and the model of each role, by role, and of each specialist tier of the run, by `_specialist_key`.

    The specialist tiers of the run are the tier of the specialist role and the tier of each ticket that has one (#231).
    Raise EngineError when the tier table has no model for one of them in the column of the adapter.
    """
    roles = {
        role: _tier_model(tiers, column, domain.role_tier(workflow.mode, role, workflow.tier_overrides), f"the {role}")
        for role in ("specialist", "verifier")
    }
    for ticket in workflow.tickets:
        tier = specialist_tier(workflow, ticket)
        roles.setdefault(_specialist_key(tier), _tier_model(tiers, column, tier, f"the specialist of ticket {ticket.id!r}"))
    return roles


def _specialist_key(tier: str) -> str:
    """The key of the specialist tier `tier` in the roles of a run (see `_roles`)."""
    return f"specialist:{tier}"


def _specialist_model(roles: dict[str, tuple[str, str]], workflow: Workflow, ticket: Ticket) -> tuple[str, str]:
    """The tier and the model of the specialist of `ticket`, of its continuations and of its fix-ups."""
    return roles[_specialist_key(specialist_tier(workflow, ticket))]


def _commit_lines(vcs: VersionControl, path: Path, since: str, until: str | None = None) -> list[str]:
    """The commits beyond `since`, newest first, each as `<id> <subject>`. `until` is the checkout of `path` when it is None."""
    with vcs_errors():
        return [f"{commit.id} {commit.subject}" for commit in vcs.commits_since(path, since, until)]



def _ends_the_run(error: Exception) -> bool:
    """True for an error that ends the run for good: an invariant violation, a change of the journal by another writer
    (#291), and a moved run branch. Any other error is a crash."""
    return isinstance(error, (InvariantViolation, JournalChanged, _RunBranchMoved))


def _halt_reason(error: Exception) -> str:
    """The reason a step fails when an error that ends the run (`_ends_the_run`) ends it."""
    if isinstance(error, (InvariantViolation, JournalChanged)):
        return f"invariant violation: {error}"
    return f"run branch moved: {error}"


def _crash_phase(events: Sequence[dict[str, object]], ticket_id: str) -> str:
    """The phase of the step of `ticket_id` that a crash stopped: the phase that the newest event of its newest step started.

    A ticket with no `step-start` crashed before its step started: the phase is `setup`.
    """
    own = step_scans.events_of(events, ticket_id)
    start = step_scans.step_start_place(own)
    if start is None:
        return CRASH_PHASE_SETUP
    phase = domain.PHASE_SPECIALIST
    for event in own[start:]:
        kind = str(event["event"])
        if kind == "agent-start":
            phase = CRASH_PHASE_OF_ROLE.get(str(event.get("role")), phase)
        elif kind == "report-validation" and event.get("valid"):
            phase = domain.PHASE_GATES
        elif kind == "gate-start" and event.get("phase") == GATE_PHASE_FIXUP:
            continue  # a gate of a fix-up round is part of the round
        elif kind in CRASH_PHASE_OF_EVENT:
            phase = CRASH_PHASE_OF_EVENT[kind]
    return phase


@dataclass(frozen=True)
class _Crash:
    """A crash that stopped the run: where, the error, and the file of its traceback (None when it could not be written)."""

    ticket: str
    phase: str
    error: str
    traceback: str | None
    retry: bool

    @property
    def where(self) -> str:
        """Where the crash stopped the run, for the lines of `delegate run`."""
        return crash_where(self.ticket, self.phase)


def _record_crash(journal: Journal, ticket_id: str, error: Exception, phase: str | None = None, retry: bool = False) -> _Crash:
    """Write the traceback of a crash to a file, and write `crashed` (#281). Return the crash.

    The traceback goes to `crash-<seq>.txt` in the run directory, where `<seq>` is the `seq` that the next event gets,
    so it is the `seq` of the `crashed` event (#194). A file that cannot be written does not hide the crash: the event
    then has no `traceback`. The phase is `phase`, or else the phase of the step of the ticket (`_crash_phase`). The
    engine writes no `step-end` and no `run-end`: the step stays open, and a resume goes on with it, as after a kill.

    `retry` is true for a crash of the step that a `--retry` goes on with. That step keeps its old `step-end` with the
    state `failed`, so a plain resume does not go on with it: `--retry` goes on with it again. The event then has
    `retry: true`, and the resume command that the lines of the run give names `--retry`.
    """
    if phase is None:
        phase = _crash_phase(journal.events, ticket_id)
    path = journal.path.parent / f"{CRASH_FILE_PREFIX}{journal.next_seq}.txt"
    trace: str | None = str(path)
    try:
        path.write_text("".join(format_exception(error)))
    except OSError:
        trace = None
    journal.append(
        CRASHED, ticket=ticket_id, phase=phase, error=type(error).__name__, message=str(error),
        **({"traceback": trace} if trace is not None else {}), **({"retry": True} if retry else {}),
    )
    return _Crash(ticket_id, phase, f"{type(error).__name__}: {error}", trace, retry)


def _expected_tip(events: list[dict[str, object]]) -> str | None:
    """Where the journal says the run branch is: the `to_commit` of the newest `run-branch-advance`, or the `base_commit` of `run-start`.

    Return None for the journal of an engine that did not record `base_commit` (1.2.0 and before) and has no advance yet.
    """
    place = step_scans.advance_place(events)
    if place >= 0:
        return str(events[place]["to_commit"])
    base = events[0].get("base_commit") if events else None
    return None if base is None else str(base)


def _check_run_branch(journal: Journal, vcs: VersionControl, repo: Path, run_branch: str, ticket: str) -> str:
    """Halt the run when the run branch is not at the tip that the journal says (#186). Return the tip.

    The run branch moves only when the engine advances it after an ACCEPT. A difference means that a commit landed with
    no verification. The engine writes `run-branch-moved` (the expected tip and the tip that it found) and raises
    `_RunBranchMoved`, which ends the run as an invariant violation does. When the tips agree, return the tip that the
    check read, so the caller does not read it again (#239). A journal with no expected tip has no check.
    """
    expected = _expected_tip(journal.events)
    found = branch_tip(vcs, repo, run_branch)
    if expected is None or found == expected:
        return found
    journal.append("run-branch-moved", ticket=ticket, run_branch=run_branch, expected=expected, found=found)
    raise _RunBranchMoved(run_state.moved_text(run_branch, expected, found))


def _accepted_unrecorded(events: list[dict[str, object]]) -> list[str]:
    """The commits that a verifier accepted after the newest `run-branch-advance`: the engine can have moved the run branch to one.

    The engine moves the run branch first and writes `run-branch-advance` after it, so a stop between the two leaves a tip
    that the journal does not record yet. A resume closes such a step (see `_recover_advance`).
    """
    return step_scans.accepted_commits(step_scans.events_after_advance(events))


def _check_run_branch_at_resume(
    journal: Journal | None, events: list[dict[str, object]], vcs: VersionControl, repo: Path, run_branch: str, built: list[str],
    failed: list[str], skipped: dict[str, str],
) -> None:
    """Refuse to resume a run whose run branch is not where the journal says (#186).

    The tip is also right when it is a commit that a verifier accepted and that the engine moved the run branch to just
    before it stopped, with the new tip holding the tip of the journal. On a difference the engine writes
    `run-branch-moved` and `run-end` with the result `failed`, and raises EngineError. With no `journal` the check writes
    nothing: `check_start` makes it before `delegate run --detach` starts the run process (#240).
    """
    expected = _expected_tip(events)
    if expected is None:
        return
    found = branch_tip(vcs, repo, run_branch)
    if found == expected or (found in _accepted_unrecorded(events) and holds_commit(vcs, repo, expected, found)):
        return
    if journal is not None:
        journal.append("run-branch-moved", ticket=None, run_branch=run_branch, expected=expected, found=found)
        journal.append("run-end", result=FAILED, built=built, failed=failed, skipped=list(skipped))
    raise EngineError(f"run cannot be resumed: {run_state.moved_text(run_branch, expected, found)}; start a new run")


def _check_branches(workflow: Workflow, repo: Path, vcs: VersionControl) -> None:
    """Refuse a new run whose base branch is missing, or whose run branch or ticket branch is taken. Create nothing."""
    with vcs_errors():
        base_exists = vcs.branch_exists(repo, workflow.base_branch)
        run_branch_taken = vcs.branch_exists(repo, workflow.run_branch)
    if not base_exists:
        raise EngineError(f"base-branch {workflow.base_branch!r} is not a branch of the repository {repo}")
    # A taken ticket branch is reported before a taken run branch: an earlier run leaves both, and the ticket names the step.
    for ticket in workflow.tickets:
        branch = ticket_branch(workflow, ticket)
        with vcs_errors():
            taken = vcs.branch_exists(repo, branch)
        if taken:
            raise EngineError(f"branch {branch!r} for ticket {ticket.id!r} exists already; delete it or choose another run-branch")
    if run_branch_taken:
        raise EngineError(
            f"run-branch {workflow.run_branch!r} exists already in the repository {repo}; an earlier run can leave it. "
            "Delete it or choose another run-branch"
        )


def _journal_events(state_dir: Path, run_id: str, observers: Sequence[EventObserver] = ()) -> tuple[Journal, list[dict[str, object]]]:
    """Open the journal of an earlier run for appending. Raise EngineError when the run has none or its journal is not valid."""
    try:
        journal, events = Journal.reopen(state_dir / "runs" / run_id / JOURNAL_NAME, observers=observers)
    except JournalError as error:
        raise EngineError(f"run {run_id!r} cannot be resumed: {error}") from None
    if not events or events[0]["event"] != "run-start":
        raise EngineError(f"run {run_id!r} cannot be resumed: its journal does not start with run-start")
    return journal, events


def resume_events(state_dir: Path, run_id: str) -> list[dict[str, object]]:
    """Read the journal of the run `run_id` for a resume, one time. Raise EngineError when it cannot be read or does not start with `run-start`."""
    try:
        events = read_events(state_dir / "runs" / run_id / JOURNAL_NAME, torn_tail=True)
    except JournalError as error:
        raise EngineError(f"run {run_id!r} cannot be resumed: {error}") from None
    if not events or events[0]["event"] != "run-start":
        raise EngineError(f"run {run_id!r} cannot be resumed: its journal does not start with run-start")
    return events


def plan_review_with_outlook(
    workflow: Workflow, workflow_path: Path, top: Path, vcs: VersionControl, events: list[dict[str, object]], request: ResumeRequest,
    config_paths: Sequence[str],
) -> PlanReview:
    """The review of the plan of `review_plan_change` from the events of the journal, with the outlook of the skipped tickets and of the retry."""
    run_id, retry = request.run_id, request.retry
    with vcs_errors():
        review = review_workflow(
            workflow, workflow_path, events, run_id, request.reviewed, lambda branch: vcs.branch_exists(top, branch), retry,
            request.stack_changed,
        )
    refusal, outlook = retry_outcome(workflow, events, review, request, vcs, top, config_paths)
    skipped = () if refusal is not None else _skipped_outlook(
        workflow, events, run_id, review, retry, request.coordinator_commit is not None, config_paths
    )
    return replace(review, skipped=skipped, retry_refusal=refusal, retry_outlook=outlook)



def _skipped_outlook(
    workflow: Workflow, events: list[dict[str, object]], run_id: str, review: PlanReview, retry: str | None, coordinator: bool,
    config_paths: Sequence[str] = (),
) -> tuple[SkippedTicket, ...]:
    """What a resume does with each ticket that the run skipped, in plan order. Empty when the run skipped no ticket (#260).

    The function restores the progress of the run, applies the plan change and the retry, and applies the skip rule
    again, with the same functions as the resume (`restore_progress`, `retry.retry_plan`, `_apply_plan_change`,
    `_prepare_resume` and `_outlook_of_skipped`). The resume records the same result in its journal (#298). Empty also
    when the resume is refused, so that no step runs.
    """
    order = plan_order(workflow)
    try:
        progress = restore_progress(events, run_id, retrying=retry is not None or review.changed)
        restart = retry is not None and review.check is not None and retry in review.check.restarted
        plan = retry_plan(workflow, progress, run_id, retry, restart, coordinator, config_paths) if retry is not None else None
    except EngineError:
        return ()
    was_skipped = set(progress.was_skipped())
    if review.check is not None:
        _apply_plan_change(review.check, progress)
    _prepare_resume(workflow, order, progress, plan)
    return tuple(SkippedTicket(item.ticket, item.text) for item in _outlook_of_skipped(order, progress, was_skipped))


class _Outlook(NamedTuple):
    """What a resume does with one ticket that the run skipped, as `_outlook_of_skipped` finds it (#260, #298).

    `text` is the text of the dry run. `runs` is True when the step of the ticket runs in the resume, if each step before
    it is built. Else `reason` is the reason of the skip rule, with `blockers` (the blockers that stay not built), or None
    for a ticket that a crash left unreached: the resume does not check it again.
    """

    ticket: str
    text: str
    runs: bool
    reason: str | None
    blockers: list[str]


def _outlook_of_skipped(order: list[Ticket], progress: Progress, was_skipped: set[str]) -> list[_Outlook]:
    """What the resume does with each ticket of `was_skipped`, in plan order, from `progress` after `_prepare_resume`.

    It takes each step that runs before a ticket as built: the skip rule skips the dependants of a step that fails. The dry
    run prints the result (`_skipped_outlook`), and the resume records it before its first step (`_record_outlook`).
    """
    built, failed, skipped = progress.built, progress.failed, progress.skipped
    complete = {*built, *failed, *skipped}
    runs: list[str] = []
    outlook: list[_Outlook] = []
    for ticket in order:
        if ticket.id in skipped:
            # The skip rule did not skip it: the run did not reach it after a crash (see `_prepare_resume`).
            text = f"stays skipped until a retry of the step that crashed: {skipped[ticket.id]}"
            outlook.append(_Outlook(ticket.id, text, False, None, []))
            continue
        if ticket.id in complete:
            continue
        reason = domain.skip_reason(ticket.blocked_by, [*built, *runs], failed)
        if reason is None:
            runs.append(ticket.id)
        if ticket.id not in was_skipped:
            continue
        if reason is not None:
            blockers = domain.unbuilt_blockers(ticket.blocked_by, [*built, *runs])
            outlook.append(_Outlook(ticket.id, f"stays skipped: {reason}", False, reason, blockers))
            continue
        earlier = [blocker for blocker in ticket.blocked_by if blocker in runs]
        text = "runs again" if not ticket.blocked_by else "runs again: each blocker is built"
        if earlier:
            text = f"runs again if {', '.join(earlier)} {'is' if len(earlier) == 1 else 'are'} built earlier in this resume"
        outlook.append(_Outlook(ticket.id, text, True, None, []))
    return outlook


def _record_outlook(
    journal: Journal, order: list[Ticket], progress: Progress, was_skipped: dict[str, str | None], before: dict[str, str]
) -> None:
    """Record in the journal what the resume does with each ticket that the run skipped, before the first step (#298).

    `was_skipped` holds the reason of each ticket that the journal gives as skipped, before the plan change and the retry,
    and None for each ticket that an `unskip` made pending before its step started (`Progress.was_skipped`). Such a
    ticket that stays skipped always gets a new `skip`: else `delegate status` shows it as pending.
    A ticket whose step runs gets `unskip`, with the text of the dry run: the old `skip` no longer applies. A ticket that
    stays skipped with a new reason gets its new `skip` now. `before` gets the reason of each ticket that stays skipped,
    so the loop of the resume writes a `skip` for it only when the reason is different then. `before` loses each ticket
    that gets `unskip`: if a blocker fails later in the resume, the loop writes a new `skip` for it, also with the old
    reason. Thus `delegate status` shows what the resume does, also before it gets to the ticket. The record is the same
    as the section `skipped tickets:` of the dry run of the resume.
    """
    for item in _outlook_of_skipped(order, progress, set(was_skipped)):
        if item.runs:
            journal.append(UNSKIP, ticket=item.ticket, outlook=item.text)
            before.pop(item.ticket, None)
        elif item.reason is not None:
            if item.reason != was_skipped[item.ticket]:
                journal.append("skip", ticket=item.ticket, blockers=item.blockers, reason=item.reason)
            before[item.ticket] = item.reason


def _refuse_plan_change(review: PlanReview, workflow_path: Path, run_id: str) -> None:
    """Raise EngineError, which names each problem, when the rules of a plan change refuse the resume."""
    problems = review.problems()
    if problems and not review.changed:
        raise EngineError(f"run {run_id!r} cannot be resumed: " + "; ".join(problems))
    if problems:
        raise EngineError(f"workflow {workflow_path} changes the plan of run {run_id}, and the rules refuse it: " + "; ".join(problems))


def _check_chain_start(
    workflow: Workflow, order: list[Ticket], built: list[str], plan: RetryPlan, vcs: VersionControl, repo: Path, run_base: str,
    run_id: str,
) -> None:
    """Refuse an `economy` retry whose step started from another commit than the chain gives it now.

    The chain before the step can change after the step started: the
    verification of the chain can add a fix-up commit to the tip, or fail the
    tickets before it. The branch of the step then does not continue the chain.
    """
    ticket = next(ticket for ticket in order if ticket.id == plan.ticket)
    started = str(next(e for e in plan.prior if e["event"] == "step-start")["base_commit"])
    with vcs_errors():
        now = vcs.commit_of(repo, _start_ref(workflow, order, built, ticket, run_base, vcs, repo))
    if started != now:
        raise EngineError(
            f"{cannot_be_retried(plan.ticket, run_id)}: its branch starts at commit {started}, "
            f"but the chain before it now ends at commit {now}; the verification of the chain changed or failed the tickets before it"
        )


def run_context(
    workflow: Workflow, workflow_path: Path, tiers: Tiers, adapter: Adapter, vcs: VersionControl, gates: GateRunner, run_id: str,
    location: StateLocation,
) -> RunContext:
    """The context of the run `run_id` in the repository of `location`: the values that do not change while it runs (#342).

    The roles get the model of each role from the tier column of the adapter. Raise EngineError, which names the tier,
    when the tier table has no model for one of them (see `_roles`). Create nothing.
    """
    roles = _roles(workflow, tiers, adapter.tier_column)
    return RunContext(
        workflow, workflow_path, adapter, vcs, gates, tiers, roles, location.top, location.state_directory(run_id), run_id,
    )


def check_start(
    run: RunContext, location: StateLocation, *, resume: ResumeRequest | None = None, detach: bool = False,
) -> StateLocation:
    """The checks of a run that need no agent: the lock, and the branches or the plan. Create nothing.

    `run_context` checks the tier of each role before, when it makes `run`. Raise EngineError, which names the input at
    fault, when a check fails. A run branch in use is reported first: the
    run branch of a run that works exists. A new run needs its base branch, and a run branch and ticket branches that
    do not exist. A resume (`resume`) needs the journal of the run, and the rules of a plan change (see `review_workflow`), with the later
    tickets in `reviewed`, must accept the workflow. `run_workflow` does these checks before it takes the lock.
    `delegate run --detach` does them before it starts the run process, so a run that cannot start gives its error on
    stderr and exits non-zero, as in the foreground.

    With `detach`, a resume also makes each other check that refuses a resume, a retry or a plan change (see `resume_checks`):
    the run branch, and the retry of `retry` with its `coordinator_commit`. They change nothing: a refusal of the run process
    would be only in its log, and the journal would not show it (#240). The foreground run makes these checks after the lock.

    `location` is the location that the command looked up (see `locate_state`). Return it, so the caller passes it on and
    does not look it up again (#239).
    """
    workflow, repo, vcs = run.workflow, run.repo, run.vcs
    try:
        # The lock of a run that started before #200 is in `<git common dir>/delegate`, so a run there counts too.
        for directory in location.directories:
            check_free(directory, workflow.run_branch)
    except LockError as error:
        raise EngineError(str(error)) from None
    if resume is not None:
        # One read of the journal serves the review and, with `detach`, the other checks of the resume.
        events = resume_events(location.state_directory(resume.run_id), resume.run_id)
        review = plan_review_with_outlook(workflow, run.workflow_path, repo, vcs, events, replace(resume, coordinator_commit=None), ())
        _refuse_plan_change(review, run.workflow_path, resume.run_id)
        if detach:
            resume_checks(workflow, run.workflow_path, vcs, repo, events, resume, None, run.config_paths)
    else:
        _check_branches(workflow, repo, vcs)
    return location


def run_workflow(
    workflow: Workflow, workflow_path: Path, repo: Path, tiers: Tiers, adapter: Adapter, vcs: VersionControl, gates: GateRunner, *,
    resume: str | None = None, retry: str | None = None, break_lock: bool = False,
    on_start: Callable[[str, Path], None] | None = None, run_id: str | None = None, reviewed: tuple[str, ...] = (),
    agents_changed: bool = False, coordinator_commit: str | None = None, location: StateLocation | None = None,
    stack_changed: bool = False, observers: Sequence[EventObserver] = (),
) -> RunResult:
    """Build the tickets of a workflow in dependency order, through `adapter`. Return the result of the run.

    The caller picks the adapter that the workflow names, the version-control
    backend `vcs`, and the gate runner `gates`. The engine knows the ports only,
    never an adapter module. The run branch, the worktrees and the file-watcher
    setting come from `vcs`. The engine runs each gate command through `gates`.

    With `resume`, the run id of an earlier run, go on with that run: build the
    tickets that are not complete, and append to its journal. With `retry` too,
    the id of a ticket whose step failed, also go on with that step (see
    `retry.retry_plan`). Raise EngineError, which names the ticket, when the step
    cannot be retried.

    The run first makes the checks of `check_start`. A refused run creates nothing, so the lock is taken last.
    The run holds the lock of its run branch until it ends: a kernel lock that its process holds (see `lock`).
    Raise EngineError, which names the holder, when another process holds the lock. A lock file that no process
    holds is free. When it names an old holder, for example a run that a kill ended, the run takes it over, and the
    result names that holder in `taken_over`. `break_lock` has no effect, and 2.0.0 removes it (#288).

    `on_start` gets the run id and the path of the journal when the run holds its lock and the journal records its
    start (`run-start`, or `resume` and a plan change), before the first step. A resume that the engine refuses does not
    call it. `delegate run` then puts the id in the title of the terminal and prints its start lines.

    The run decides from the events that its journal holds in memory (#291). A change of the journal file by another
    writer ends the run as an invariant violation does: in a step, the step fails; outside a step, the run writes
    `run-end` with the result `failed`, and raises EngineError, which names the journal and the byte offset.

    Ctrl-C (`KeyboardInterrupt`) stops the run at once. The run releases its lock, appends `interrupted` to its
    journal, and raises RunInterrupted. A resume then goes on as after a crash.

    `run_id` is the id of a new run. The default is a new id from `new_run_id`. `delegate run --detach` gives the id
    that it printed before it started the run process. A resume uses the id `resume`, and ignores `run_id`.

    A resume with a workflow whose tickets differ from the newest plan of the run
    is a plan change (see `review_workflow` and `domain.check_plan_change`). `reviewed`
    holds the ids of the later tickets that the coordinator reviewed, or
    `all`. Raise EngineError, which names each ticket and field at fault, when
    the rules refuse the change. A plan change also goes on with a run that ended.

    `agents_changed` is for a resume: the engine records the hashes of the agent files of the main checkout again, in
    the `resume` event, and goes on with them as the agents of the run (see `delegate.run.agents`). A resume of a
    journal with no record of the agent files records them too, and the result has `agents_note`.

    `stack_changed` is for a resume (#290): the workflow file may change the configuration of a stack. The engine then writes
    one `stack-change` event for each changed stack, before `resume`, and a later resume compares with it. A resume that the
    workflow file changes in no stack, with the flag, is refused.

    `coordinator_commit` goes with `retry`: a commit of the coordinator at the tip of the branch of the ticket that
    changes only hotspot paths (#225). The `retry` event records it with its paths, and the hotspot check of the step
    does not count it (see `retry.check_coordinator_commit`). Raise EngineError, which names the commit, when it is refused.

    `location` is the location that the command looked up (see `locate_state`), or None to look it up here. The run
    looks it up at most one time (#239).

    The run makes its context (see `run_context`) from these values once, and each function of the run takes the context (#342).

    `observers` are the observers of the events of the run (#332). The journal of the run, and the journal that the run reopens
    to record an interrupt, call each of them with the journal and the name of each event after the journal wrote it. The driver
    of `delegate run` gives the observer of the run index (#316, `delegate.run.index.index_observers`): it writes the record of
    the run at `run-start`, `resume`, each `step-end`, `paused`, `halted`, `crashed`, `interrupted`, `setup-problem` and
    `run-end`. A write of the index that fails adds `index-warning` to the journal, and the run goes on. With no observer,
    the run writes nothing besides its journal.
    """
    if coordinator_commit is not None and retry is None:
        raise EngineError("--coordinator-commit needs --retry TICKET")
    run_id = resume if resume is not None else (run_id or new_run_id())
    location = location or locate_state(repo, vcs)
    run = run_context(workflow, workflow_path, tiers, adapter, vcs, gates, run_id, location)
    request = ResumeRequest(run_id, reviewed, retry, coordinator_commit, stack_changed) if resume is not None else None
    check_start(run, location, resume=request)
    state_dir = run.state_dir
    record_state_root(location, state_dir)
    try:
        with run_lock(state_dir, workflow.run_branch, run_id) as broken:
            try:
                result = _execute(run, request, broken, on_start, agents_changed, observers)
            except JournalChanged as changed:
                raise EngineError(_end_changed_journal(changed, run_id)) from None
            except KeyboardInterrupt:
                # The block of the lock ends with this exception, so the lock is released.
                raise RunInterrupted(run_id, _record_interrupt(state_dir, run_id, observers)) from None
            return replace(result, taken_over=broken)
    except LockError as error:
        raise EngineError(str(error)) from None


def _end_changed_journal(changed: JournalChanged, run_id: str) -> str:
    """End a run whose journal another writer changed outside a step (#291). Return the text of the error.

    A change that the journal finds in a step fails the step as an invariant violation does (`_ends_the_run`). A change
    that it finds outside a step, for example after the guard probe or at the clean-up, ends here: the journal
    holds `invariant-violation` already, and the engine writes `run-end` with the result `failed`.
    """
    progress = restore_progress(changed.journal.events, run_id, retrying=True)
    changed.journal.append(
        "run-end", result=FAILED, built=list(progress.built), failed=list(progress.failed), skipped=list(progress.skipped),
    )
    return f"invariant violation: {changed}"


def _record_interrupt(state_dir: Path, run_id: str, observers: Sequence[EventObserver]) -> Path | None:
    """Append `interrupted` to the journal of the run, and return its path. Return None when the run has no journal that can be read.

    The `observers` of the run see the event (#327, #332), as they see each other event.
    """
    path = state_dir / "runs" / run_id / JOURNAL_NAME
    try:
        journal, _ = Journal.reopen(path, observers=observers)
    except JournalError:
        return None
    journal.append("interrupted", run_id=run_id)
    return path


def resume_checks(
    workflow: Workflow, workflow_path: Path, vcs: VersionControl, repo: Path, events: list[dict[str, object]], request: ResumeRequest,
    journal: Journal | None, config_paths: Sequence[str],
) -> tuple[PlanReview, Progress, str, RetryPlan | None]:
    """The checks that refuse the resume `request`, its retry or its plan change, in the order of the engine. Return what they find.

    The result is the review of the plan, the progress of the run, the base of the run, and the plan of the retry (None
    with no `retry`). Raise EngineError when a check refuses. With a `journal`, a run branch that is not where the journal
    says is also written to it (see `_check_run_branch_at_resume`). With `journal` None the checks change nothing:
    `check_start` makes them before `delegate run --detach` starts the run process, so a refusal is not only in its log (#240).
    """
    run_id, retry, coordinator_commit = request.run_id, request.retry, request.coordinator_commit
    order = plan_order(workflow)
    unresumable = run_state.fold(events).unresumable
    if unresumable is not None:
        raise EngineError(f"run {run_id!r} cannot be resumed: {unresumable.reason}; start a new run")
    with vcs_errors():
        review = review_workflow(
            workflow, workflow_path, events, run_id, request.reviewed, lambda branch: vcs.branch_exists(repo, branch), retry,
            request.stack_changed,
        )
    # `check_start` made this review before the lock. It is made again here, because the journal can change before the lock.
    _refuse_plan_change(review, workflow_path, run_id)
    progress = restore_progress(events, run_id, retrying=retry is not None or review.changed)
    with vcs_errors():
        run_branch_exists = vcs.branch_exists(repo, workflow.run_branch)
    if not run_branch_exists:
        raise EngineError(f"run branch {workflow.run_branch!r} of run {run_id!r} is not a branch of the repository {repo}")
    run_base = _run_base(vcs, repo, workflow, events)
    _check_run_branch_at_resume(journal, events, vcs, repo, workflow.run_branch, progress.built, progress.failed, progress.skipped)
    # A step that cannot be retried is refused before the journal changes.
    restart = retry is not None and review.check is not None and retry in review.check.restarted
    plan = (
        retry_plan(workflow, progress, run_id, retry, restart, coordinator_commit is not None, config_paths)
        if retry is not None else None
    )
    if plan is not None and workflow.mode == domain.ECONOMY and not plan.restart:
        # A ticket of a later stack that the stop failed does not count: the retried ticket starts from the chain before it (#209).
        before = [t.id for t in order[: [t.id for t in order].index(plan.ticket)]]
        chain = [*progress.built, *(t for t in plan.revive if t in before)]
        _check_chain_start(workflow, order, chain, plan, vcs, repo, run_base, run_id)
    if plan is not None and coordinator_commit is not None:
        plan = replace(plan, coordinator=check_coordinator_commit(workflow, plan, vcs, repo, run_id, coordinator_commit, config_paths))
    elif plan is not None and plan.phase == RETRY_PHASE_CHAIN:
        check_chain_hotspots(workflow, plan, vcs, repo, run_id, config_paths)
    return review, progress, run_base, plan


def _execute(
    run: RunContext, resume: ResumeRequest | None, broken: LockHolder | None, on_start: Callable[[str, Path], None] | None,
    agents_changed: bool, observers: Sequence[EventObserver],
) -> RunResult:
    """Run the tickets of a workflow, or the rest of them on a resume, and return the result that the command reports.

    A new run (`resume` is None) seals the agents, makes the run branch at the base commit, and writes `run-start`. A resume
    checks the journal and the workflow (`resume_checks`), writes `resume`, and records a `--stack-changed` change, a plan
    change and the outlook of the skipped tickets. A retry (`plan`) revives the tickets that its stop failed and runs the
    retried ticket first, with `_retry_ticket`.

    The loop takes each ticket that is not complete in plan order. A ticket whose blocker failed or was skipped is skipped,
    with the reason of the skip rule (`domain.skip_reason`). Any other ticket runs, and the loop writes `step-end` for it. A
    failed step does not end the run. These ends of the loop leave the step open, so a resume goes on with it: a pause
    (`paused`), a halt of the harness (`halted`) and a crash (`crashed`, #281). An invariant violation or a moved run branch
    ends the run: the loop fails the step and skips each ticket after it.

    After the loop, an `economy` run verifies the chain once (`chain.ChainRun`). The chain pass starts at the newest event
    for which `run_state.starts_chain_verification` is true, so it reads the events after the last `plan-change` or `retry`
    that is not a retry at `chain-fixup`. The chain is not verified when the loop stopped early or no ticket was built.

    A pause, a halt and a crash each return the result with no `run-end`. Else the run ends: the engine cleans up the
    worktrees of a run that built every ticket, unless the workflow keeps them, and writes `run-end` with the result `built`
    or `failed`. A skipped ticket fails the run.
    """
    workflow, workflow_path, adapter, vcs, repo, state_dir, run_id = (
        run.workflow, run.workflow_path, run.adapter, run.vcs, run.repo, run.state_dir, run.run_id
    )
    order = plan_order(workflow)
    agents_note: str | None = None
    plan: RetryPlan | None = None
    # A pause request from before this process is not for it: the run that it asked to pause has stopped.
    (state_dir / "runs" / run_id / PAUSE_REQUEST_NAME).unlink(missing_ok=True)
    if resume is None:
        # The seal comes before the run branch: an adapter that refuses to seal leaves no branch and no journal (#217).
        sealed = _seal_agents(adapter, repo, state_dir / "runs" / run_id, workflow)
        with vcs_errors():
            run_base = vcs.commit_of(repo, workflow.base_branch)
            vcs.create_branch(repo, workflow.run_branch, run_base)
        journal = Journal(state_dir / "runs" / run_id / JOURNAL_NAME, observers=observers)
        journal.append(
            RUN_START, run_id=run_id, workflow=str(workflow_path.resolve()), mode=workflow.mode, adapter=workflow.adapter,
            base_branch=workflow.base_branch, base_commit=run_base, run_branch=workflow.run_branch, tickets=[t.id for t in order],
            plan=plan_record(plan_of(workflow)), schema=SCHEMA_VERSION,
            agents=sealed, configuration=configuration_record(workflow), repo=str(repo.resolve()), host=host_name(),
        )
        progress = Progress()
        was_skipped: dict[str, str | None] = {}
    else:
        journal, events = _journal_events(state_dir, run_id, observers)
        review, progress, run_base, plan = resume_checks(workflow, workflow_path, vcs, repo, events, resume, journal, run.config_paths)
        was_skipped = progress.was_skipped()  # before the plan change and the retry, as in the dry run (`_skipped_outlook`)
        # The seal changes only at the request of the person, or when the journal has no record of seals (#217).
        agent_fields: dict[str, object] = {}
        if agents_changed or recorded_agents(events) is None:
            agent_fields = {"agents": _seal_agents(adapter, repo, state_dir / "runs" / run_id, workflow)}
            if recorded_agents(events) is None:
                agents_note = (
                    f"run {run_id} has no record of sealed agents (its journal is of an earlier version); "
                    "the agents are sealed now, at the resume, from the main checkout"
                )
                agent_fields["agents_note"] = agents_note
        # The checks passed. So the newest record differs from the workflow only where the check accepts it: a stack that only
        # one of them has, or a field that the record does not hold. Or the journal has no record at all (#289). The resume
        # then records the configuration, so the next resume compares with it.
        configuration = configuration_record(workflow)
        # `--stack-changed` (#290): the change of each stack is in the journal before the `resume` event, and the record that the resume compares with.
        changes = [{"event": STACK_CHANGE, **change.event_fields()} for change in review.stack_changes]
        for change in review.stack_changes:
            journal.append(STACK_CHANGE, **change.event_fields())
        configuration_fields = {} if recorded_configuration([*events, *changes]) == configuration else {"configuration": configuration}
        journal.append(
            "resume", run_id=run_id, built=list(progress.built), failed=list(progress.failed),
            skipped=list(progress.skipped), open=progress.open_ticket, **agent_fields, **configuration_fields,
        )
        if workflow.mode == domain.ECONOMY:
            # Before a plan change: the verification of a chain reads only the events after the newest plan change.
            _recover_chain_advance(workflow, vcs, repo, journal, events)
        if review.check is not None:
            _record_plan_change(journal, workflow, review.check, progress)
    if on_start is not None:
        on_start(run_id, journal.path)
    if broken is not None:
        journal.append("lock-broken", run_id=broken.run_id, pid=broken.pid)
    _check_gates_permitted(adapter, journal, workflow)
    _probe_guards(run, journal)
    built, failed, failures, skipped = progress.built, progress.failed, progress.failures, progress.skipped
    retried_reason, before = _prepare_resume(workflow, order, progress, plan)
    for revived in plan.revive if plan is not None else ():
        journal.append("step-end", ticket=revived, state=BUILT, reason=None)
    _record_outlook(journal, order, progress, was_skipped, before)
    complete = {*built, *failed, *skipped}
    ended_early = False
    paused: str | None = None
    stopped_by: _Halted | None = None
    crash: _Crash | None = None
    for position, ticket in enumerate(order):
        if ticket.id in complete:
            continue
        skip = domain.skip_reason(ticket.blocked_by, built, failed)
        if skip:
            skipped[ticket.id] = skip
            if before.get(ticket.id) != skip:
                journal.append("skip", ticket=ticket.id, blockers=domain.unbuilt_blockers(ticket.blocked_by, built), reason=skip)
            continue
        prior = step_scans.step_events(progress.events, ticket.id) if ticket.id == progress.open_ticket else None
        start = _start_ref(workflow, order, built, ticket, run_base, vcs, repo)
        try:
            pause_point(journal, ticket.id, PAUSED_BEFORE_STEP, f"before the step of ticket {ticket.id}")
            if plan is not None and ticket.id == plan.ticket:
                reason = _retry_ticket(_Step(run, journal, ticket), plan, retried_reason, start)
            else:
                reason = _build_ticket(run, journal, ticket, start, prior)
        except Paused as stop:
            # The step stays open, or does not start: a resume goes on with it.
            paused = str(stop)
            break
        except _Halted as halt:
            # The harness failed, not the work. The step stays open: a resume goes on with it.
            stopped_by = halt
            break
        except Exception as error:  # noqa: BLE001 - the journal records it and the command reports it
            if not _ends_the_run(error):
                # A crash: an exception that the engine did not plan for. The journal and git hold the state of the
                # step, as after a kill, so the step stays open and a resume goes on with it (#281).
                crash = _record_crash(journal, ticket.id, error, retry=plan is not None and ticket.id == plan.ticket)
                break
            # A verifier that changed the real worktree ends the run: the state of the worktree can no longer be
            # trusted. A moved run branch ends it too. The journal says why.
            ended_early = True
            reason = _halt_reason(error)
            journal.append("step-end", ticket=ticket.id, state=FAILED, reason=reason)
            failed.append(ticket.id)
            failures[ticket.id] = reason
            for unreached in order[position + 1 :]:
                if unreached.id in complete:
                    continue  # a retry: the ticket ended in the run before
                skipped[unreached.id] = domain.unreached_reason(ticket.id, True)
                journal.append("skip", ticket=unreached.id, blockers=[], reason=skipped[unreached.id])
            break
        journal.append("step-end", ticket=ticket.id, state=FAILED if reason else BUILT, reason=reason)
        if reason:
            failed.append(ticket.id)
            failures[ticket.id] = reason
        else:
            built.append(ticket.id)
    unmapped: list[str] = []
    if workflow.mode == "economy" and built and not ended_early and paused is None and stopped_by is None and crash is None:
        chain_run = chain.ChainRun(run, journal, order, built, failed, failures, _chain_steps())
        # The events of the chain verification name the last ticket of the chain. `verify` can fail tickets, so take it first.
        tip = [t.id for t in order if t.id in built][-1]
        try:
            unmapped = chain_run.verify()
        except Paused as stop:
            paused = str(stop)
        except _Halted as halt:
            stopped_by = halt
        except Exception as error:  # noqa: BLE001 - `verify` raises only a crash; the journal records it
            crash = _record_crash(journal, tip, error, CRASH_PHASE_CHAIN)
    if crash is not None:
        # A crashed run has no `run-end` either: a resume goes on with the open step (#281).
        return RunResult(
            run_id, journal.path, built, failed, failures, skipped, unmapped, crashed=crash.error, crashed_at=crash.where,
            traceback=crash.traceback, crashed_retry=crash.ticket if crash.retry else None, agents_note=agents_note,
        )
    if stopped_by is not None:
        # A halted run has no `run-end` either: a resume goes on with it.
        where = f"the {stopped_by.role} of ticket {stopped_by.ticket}"
        return RunResult(
            run_id, journal.path, built, failed, failures, skipped, unmapped, halted=str(stopped_by), halted_at=where,
            agents_note=agents_note,
        )
    if paused is not None:
        # A paused run has no `run-end`: a resume goes on with it.
        return RunResult(run_id, journal.path, built, failed, failures, skipped, unmapped, paused=paused, agents_note=agents_note)
    # A skipped ticket is work that the run did not build, so the run fails, as `RunResult.ok` says.
    result = BUILT if not failed and not skipped else FAILED
    if result == BUILT and not workflow.keep_worktrees:
        # Before `run-end`, so the newest event of a run that ended stays `run-end`.
        _clean_up(run, order, journal)
    journal.append("run-end", result=result, built=built, failed=failed, skipped=list(skipped))
    # A request after the last step boundary finds no boundary: the run ended, and the request goes.
    (journal.path.parent / PAUSE_REQUEST_NAME).unlink(missing_ok=True)
    return RunResult(run_id, journal.path, built, failed, failures, skipped, unmapped, agents_note=agents_note)


def _prepare_resume(workflow: Workflow, order: list[Ticket], progress: Progress, plan: RetryPlan | None) -> tuple[str, dict[str, str]]:
    """Apply the retry and the skip rule of a resume to `progress`, as the engine does before the first step of a resume.

    Return the reason of the failure of the retried step (empty with no `plan`), and the old reason of each ticket that
    the skip rule skipped and that is now not skipped. The skip rule (`domain.skip_reason`) then decides for each of these
    tickets in plan order. The dry run of a resume uses the same function (see `_skipped_outlook`), so it cannot disagree
    with the resume (#260).
    """
    built, failed, failures, skipped = progress.built, progress.failed, progress.failures, progress.skipped
    retried_reason = ""
    if plan is not None:
        failed.remove(plan.ticket)
        retried_reason = failures.pop(plan.ticket)
        for revived in plan.revive:
            # The stop failed these tickets with the retried ticket, and their branches hold their commits (#209).
            failed.remove(revived)
            del failures[revived]
            built.append(revived)
        if workflow.mode == domain.ECONOMY:
            # The chain goes on after the step: the skip rule applies again to each ticket after it that was skipped.
            for later in order[[t.id for t in order].index(plan.ticket) + 1 :]:
                skipped.pop(later.id, None)
        else:
            # The skip rule applies again to each ticket that the failed step made the engine skip.
            for dependant in domain.skipped_because_of(plan.ticket, [(t.id, t.blocked_by) for t in order], skipped):
                del skipped[dependant]
    # At each resume the skip rule applies again to each ticket that it skipped, with the blockers of now (#245). A
    # ticket whose blockers are now built is not skipped, and its step runs. Another gets a new `skip` if its reason changed.
    before = {blocked: skipped.pop(blocked) for blocked in progress.blocked if blocked in skipped}
    return retried_reason, before


def _clean_up(run: RunContext, order: list[Ticket], journal: Journal) -> None:
    """Remove the worktree and the push-guard hooks of each ticket, and delete each ticket branch that the run branch holds (#167).

    The engine calls this when the run ends `built`. The rules are in
    `cleanup.clean_tickets`, which `delegate clean` uses too. The run branch,
    the journal, the reports and the gate logs stay: they are the evidence of
    the run. The journal records one `cleanup` event with what went, what
    stayed, and why.
    """
    workflow, state_dir, run_id = run.workflow, run.state_dir, run.run_id
    worktrees_dir, hooks_dir = state_dir / "worktrees" / run_id, state_dir / "hooks" / run_id
    trees = [TicketTree(worktrees_dir / ticket.id, ticket_branch(workflow, ticket), hooks_dir / ticket.id) for ticket in order]
    result = clean_tickets(run.vcs, run.repo, trees, workflow.run_branch)
    remove_empty_directories(worktrees_dir, hooks_dir)
    journal.append("cleanup", **result.fields())


def _record_plan_change(journal: Journal, workflow: Workflow, check: domain.PlanCheck, progress: Progress) -> None:
    """Write `plan-change`, and apply the change to the progress of the run.

    A removed ticket leaves the run. A failed ticket that a retry starts again with a new text is in `retry_text`, with its old and its new `text_sha256`. A changed ticket that the run skipped is
    no longer skipped: the skip rule applies to it again, with its new blockers.
    """
    plan = plan_of(workflow)
    journal.append(
        "plan-change", added=check.added, changed=list(check.changed), fields=check.changed, removed=check.removed,
        order=[t.id for t in plan], reviewed=check.later, plan=plan_record(plan),
        **({"retry_text": {t: {"old": old, "new": new} for t, (old, new) in check.restarted.items()}} if check.restarted else {}),
    )
    _apply_plan_change(check, progress)


def _apply_plan_change(check: domain.PlanCheck, progress: Progress) -> None:
    """Apply a plan change to the progress of the run: a removed ticket leaves the skipped tickets, and so does a changed ticket."""
    for ticket_id in [*check.removed, *check.changed]:
        progress.skipped.pop(ticket_id, None)


def _plan_change_note(journal: Journal, workflow: Workflow, ticket_ids: list[str], verifier: bool) -> str | None:
    """The text of the section "Plan change" for the brief of the tickets `ticket_ids`, or None when none needs it.

    A ticket needs it when a `plan-change` of the journal lists it as a later
    ticket. The section names each ticket that such a change added or changed,
    and that comes before the ticket in the plan, with its text.
    """
    events = [event for event in journal.events if event["event"] == "plan-change"]
    if not events:
        return None
    order = [t.id for t in plan_order(workflow)]
    texts = {t.id: t.text for t in workflow.tickets}
    kinds: dict[str, str] = {}
    for event in events:
        for target in ticket_ids:
            if target not in event["reviewed"] or target not in order:  # type: ignore[operator]
                continue
            for kind in ("added", "changed"):
                for ticket_id in event[kind]:  # type: ignore[attr-defined]
                    if ticket_id in order and order.index(ticket_id) < order.index(target):
                        kinds.setdefault(ticket_id, kind)
    if not kinds:
        return None
    if workflow.mode == domain.ECONOMY:
        # A plan change that adds a ticket after a chain whose verification failed goes on only with the retry of its
        # tip, which revives the chain (#270). So the chain before this ticket holds each built ticket before it.
        location = (
            "The engine builds these tickets before this ticket. Each ticket of the chain starts from the last built ticket "
            "before it, so the branch of this ticket holds the work of each ticket before it in the chain, these tickets too. "
            "A ticket that failed is not in the chain, and the branch of this ticket does not hold its work."
        )
    else:
        location = f"The engine builds these tickets before this ticket, and their accepted work is on the run branch `{workflow.run_branch}`."
    return plan_change_text([(t, kinds[t], texts[t]) for t in order if t in kinds], location, verifier)


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
    return branch_tip(vcs, repo, workflow.run_branch)


def _start_ref(
    workflow: Workflow, order: list[Ticket], built: list[str], ticket: Ticket, run_base: str, vcs: VersionControl, repo: Path
) -> str:
    """The branch or the commit that `ticket` starts from.

    It is the branch of its predecessor in a chain (see `domain.chain_predecessor`),
    the run branch when the ticket has blockers or the workflow says so (see
    `domain.starts_from_run_branch`), or else `run_base`, the base of the run. It
    is never the base branch, which can move while the run goes on.

    The clean-up of a run that ended `built` deletes a ticket branch only when the
    run branch holds it (see `_clean_up`). The chain then ends at the tip of the
    run branch, so a ticket that a plan change added starts from the run branch.
    """
    predecessor = domain.chain_predecessor(workflow.mode, [ticket.id for ticket in order], built)
    if predecessor is None:
        if domain.starts_from_run_branch(workflow.mode, workflow.ticket_start, ticket.blocked_by):
            return workflow.run_branch
        return run_base
    branch = ticket_branch(workflow, next(ticket for ticket in order if ticket.id == predecessor))
    with vcs_errors():
        return branch if vcs.branch_exists(repo, branch) else workflow.run_branch


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


def _run_agent(adapter: Adapter, journal: Journal, ticket: str, role: str, round_number: int, request: AdapterRequest) -> AdapterResult:
    """Run the agent through the adapter. An `AdapterError` is a failure of the harness: journal `halted`, and raise `_Halted`.

    The event is `halted` with the ticket, the role, the round and the message. It is not a failure of the step, so
    no `step-end` follows, and the run process ends with no `run-end`. Each other exception goes up as a crash.
    """
    try:
        return adapter.run(request)
    except AdapterError as error:
        journal.append("halted", ticket=ticket, role=role, round=round_number, message=str(error))
        raise _Halted(ticket, role, round_number, str(error)) from None


def _seal_agents(adapter: Adapter, repo: Path, run_dir: Path, workflow: Workflow) -> list[dict[str, object]]:
    """Seal each agent of the workflow from the main checkout into the run directory, and give the record (#217).

    The seal of a verifier gets the `verifier-tools` of the MCP servers of its stack (#257). An adapter that refuses to
    seal (an `AdapterError`) stops the command with an `EngineError`, and the journal gets no event.
    """
    try:
        return seal_agents(adapter, repo, run_dir, agent_names(workflow), verifier_mcp_tools(workflow))
    except AdapterError as error:
        raise EngineError(f"the agents of the run cannot be sealed: {error}") from None


def _check_gates_permitted(adapter: Adapter, journal: Journal, workflow: Workflow) -> None:
    """End the run before its first agent session when the verifier of a stack does not permit a gate of the stack (#296).

    It is the check of the dry run (`unpermitted_gates`), applied to the sealed verifier of the record of seals. It
    starts no session. An adapter whose `checks_setup` is not true gets no check, as in the dry run. A verifier that the
    record holds no seal for is not checked. Each gate that the guard does not permit is a problem. The journal gets one
    `setup-problem` event with the problems, and an `EngineError` gives them to the command. A resume checks again.
    """
    if not getattr(adapter, "checks_setup", False):
        return
    recorded = recorded_agents(journal.events) or []
    problems: list[str] = []
    for stack in workflow.stacks.values():
        sealed, _change = check_agent(recorded, stack.verifier)
        if sealed is not None:
            problems.extend(unpermitted_gates(adapter, stack, sealed))
    if problems:
        journal.append(SETUP_PROBLEM, problems=problems)
        raise EngineError("; ".join(problems))


def _probe_guards(run: RunContext, journal: Journal) -> None:
    """Start the guard probe of each verifier whose seal has no denied probe, and end the run for a result other than `denied` (#219).

    It runs before the first agent session of the run or of the resume. An adapter whose `probes_guard` is not true gets
    no probe. A verifier that the run did not seal gets none either: a session of an agent with no seal does not run from
    a guard of the run. The journal gets `guard-probe` with the agent, the handle, the result, the event stream and each try (#299). A
    result other than `denied` raises an `EngineError` that names the agent, the result, the limits page and the command
    that goes on. A resume probes again, because no denied probe follows the newest seal. A failure of the harness or of
    the repository of the probe is an `EngineError` too, and the journal gets no event for it.
    """
    adapter, vcs, tiers, workflow, run_dir, run_id = run.adapter, run.vcs, run.tiers, run.workflow, run.run_dir, run.run_id
    if not getattr(adapter, "probes_guard", False):
        return
    events = journal.events
    recorded = recorded_agents(events) or []
    done = denied_agents(events)
    verifiers = list(dict.fromkeys(stack.verifier for stack in workflow.stacks.values()))
    pending = [entry for name in verifiers if name not in done for entry in recorded if entry["agent"] == name]
    if not pending:
        return
    try:
        model = tiers.model_for(PROBE_TIER, adapter.tier_column)
    except KeyError:
        raise EngineError(f"the tier table has no {adapter.tier_column!r} column for tier {PROBE_TIER!r}, which the guard probe needs") from None
    except ValueError:
        raise EngineError(f"the tier table has no tier {PROBE_TIER!r}, which the guard probe needs") from None
    for entry in pending:
        agent = str(entry["agent"])
        sealed, change = check_agent(recorded, agent)
        if change is not None:
            raise EngineError(
                f"sealed file {change.path} of agent {agent} is not the file that the run sealed: recorded sha256 {change.recorded}, "
                f"now {change.found or 'missing'}; the guard probe did not run; "
                f"if the change is meant, go on with delegate run --resume {run_id} --agents-changed"
            )
        if sealed is None:
            continue  # the entry comes from the record, so this cannot happen
        stream = stream_path(run_dir, agent)
        try:
            probe = probe_guard(adapter, vcs, sealed, model, stream)
        except (AdapterError, VcsError) as error:
            raise EngineError(f"the guard probe of the verifier {agent!r} could not run: {error}") from None
        tries = [{"result": t.result, "event_stream": str(t.event_stream)} for t in probe.tries]
        journal.append("guard-probe", agent=probe.agent, handle=probe.handle, result=probe.result, event_stream=str(probe.event_stream), tries=tries)
        if probe.result != DENIED:
            raise EngineError(f"{refusal(agent, probe.result, describe_tries(probe))}; the run started no agent session; a resume with delegate run --resume {run_id} probes again")


def _check_agent_file(
    journal: Journal, agent: str, ticket: str, role: str, round_number: int, fields: dict[str, object] | None = None, *,
    retry: str | None = None,
) -> SealedAgent | None:
    """Fail the step before an agent session when a file of the seal of the agent is not as the run recorded it (#217).

    The journal gets `agents-changed` with the ticket, the role, the round, the tree `seal`, the agent, the path of the
    changed file, the recorded hash, and the new hash (`None` for a missing file), and `fields` (the fix-up gives the
    fields that a retry needs to run it again, see `_run_fixup`). Raise `StepFailed`, whose message names the file and
    both hashes. The agent file in the tree of the agent is not read. Give the sealed agent for the request, or None when
    the run recorded no seal for the agent. `retry` is the ticket that the message names for the retry, when it is not `ticket`.
    """
    recorded = recorded_agents(journal.events)
    if recorded is None:
        return None
    sealed, change = check_agent(recorded, agent)
    if change is None:
        return sealed
    journal.append(
        "agents-changed", ticket=ticket, role=role, round=round_number, tree=TREE_SEAL, agent=agent, path=str(change.path),
        recorded_hash=change.recorded, new_hash=change.found, **(fields or {}),
    )
    raise StepFailed(change.message(retry or ticket))


def _lenient_fields(report: SpecialistReport | VerifierReport) -> dict[str, object]:
    """`lenient_parse: true` for a report that the strict parse refused (#197), else no field."""
    return {"lenient_parse": True} if report.lenient_parse else {}


def _report_check(read: Callable[[], object]) -> Callable[[], bool]:
    """The check of a report that an adapter calls: true when `read` gives a report, false when it raises ReportError."""

    def valid() -> bool:
        try:
            read()
        except ReportError:
            return False
        return True

    return valid


@dataclass(frozen=True)
class _ReportBefore:
    """The state of the report file before a specialist session: `None` for an absent file, else `(st_mtime_ns, st_size)`."""

    state: tuple[int, int] | None


def _report_state(report_path: Path) -> tuple[int, int] | None:
    """The state of the report file: None when it is absent or unreadable, else its modification time and size."""
    try:
        status = report_path.stat()
    except OSError:
        return None
    return status.st_mtime_ns, status.st_size


def _report_is_stale(report_path: Path, before: _ReportBefore | None) -> bool:
    """True when the report file is the same file as before the session: the same modification time and size (#278, #286).

    Such a file is the report of an earlier session. The check compares the file with its own earlier state, not with
    the clock: a coarse file clock can give a file a time that is a few milliseconds behind `time.time_ns()`.
    A report that was absent before the session is new. `before` is None for a session that ended before a kill: its
    report counts. A missing or unreadable file is not stale: the read of the report gives that error.
    """
    if before is None or before.state is None:
        return False
    return _report_state(report_path) == before.state


def _read_session_report(report_path: Path, ticket: str, before: _ReportBefore | None) -> SpecialistReport:
    """Read the specialist report of a session that began with the report file in the state `before`. A report that the session did not write is a missing report.

    The engine does not delete or move an older report: it stays as evidence.
    """
    if _report_is_stale(report_path, before):
        raise ReportError(
            f"report missing: the specialist wrote no file at {report_path} in this session; "
            "the file there is the report of an earlier session"
        )
    return read_specialist_report(report_path, ticket)


class _Session(NamedTuple):
    """One agent session of a step: the agent, its model and its seal, the directory where it works, and its files.

    `stream` is the event stream of the session (see `_start_agent`), and `scratch` its scratch directory (see
    `_scratch_directory`). `added_directories` are the directories that the session can read besides `cwd`.
    """

    agent: str
    model: str
    sealed: SealedAgent | None
    cwd: Path
    report_path: Path
    stream: Path
    scratch: Path
    added_directories: tuple[Path, ...] = ()


def _request(step: _Step, session: _Session, prompt: str, report_valid: Callable[[], bool], resume_session: str | None = None) -> AdapterRequest:
    """The request for one agent session of the step: the idle timeout of the run, the check of its report, the MCP servers and the agent timeout of the stack.

    The environment holds the entries of the worktree of the step (see `worktree_environment`). The request adds the
    scratch directory of the session to `added_directories`, and sets `TMPDIR` to it in the environment (#227).
    """
    stack, scratch = step.stack, session.scratch
    return AdapterRequest(
        session.agent, session.model, prompt, session.cwd, session.report_path, session.stream, resume_session,
        idle_timeout=domain.IDLE_TIMEOUT_SECONDS, report_valid=report_valid, mcp_servers=stack.mcp_servers,
        added_directories=(*session.added_directories, scratch), agent_timeout=seconds(stack.agent_timeout), sealed=session.sealed,
        environment={**worktree_environment(step.run.vcs, step.worktree), "TMPDIR": str(scratch)},
    )


def _scratch_directory(run_dir: Path, ticket: str, role: str) -> Path:
    """Make the scratch directory of a session of `role` for `ticket`, `<run dir>/scratch/<ticket>/<role>/`, and give it (#227).

    The sessions of one role share the directory: a continuation and each fix-up round find the files of the session
    before them. The directory can exist already.
    """
    path = run_dir / SCRATCH_DIRECTORY / ticket / role
    path.mkdir(parents=True, exist_ok=True)
    return path


def _verifier_scratch_directory(run_id: str, ticket: str) -> Path:
    """Make the scratch directory of one verifier session, and give it (#294).

    The directory is new and private (`tempfile.mkdtemp`), in the temp directory of the host, with a name that starts
    with `delegate-verifier-<run id>-<ticket>-`. It is outside the directory of the run, which holds the report of the
    specialist, so the brief and the environment of a blind verifier name no path there. The caller removes it when the
    session ends, and `delegate clean` removes one that a kill left.
    """
    return Path(tempfile.mkdtemp(prefix=f"{VERIFIER_SCRATCH_PREFIX}{run_id}-{ticket}-"))


def _verifier_temp_parent(scratch: Path) -> Path | None:
    """The directory where the engine makes the temporary copy of a verifier: None for the temp directory of the host.

    The session of a verifier gets `TMPDIR` set to its scratch directory, so its guard permits a write under the scratch
    directory and under the fixed prefixes (`GUARD_TEMP_PREFIXES`), and nowhere else. The copy, the proof copy and the
    report stay in the temp directory of the host when that directory is under a fixed prefix: an OpenCode verifier can
    write outside its copy only there. On a host whose temp directory is outside them, for example `/home/agent/.tmp`
    (#166), they go in the scratch directory.
    """
    host = tempfile.gettempdir()
    if host.startswith("/private/"):
        host = host[len("/private"):]
    return None if (host + "/").startswith(GUARD_TEMP_PREFIXES) else scratch


def _make_proof_copy(copy: Path) -> tuple[Path, ...]:
    """Make the proof copy of a red proof beside `copy`: the tree of `copy` with no `.git` entry (#153).

    A host can refuse the `cp -R` and the `rm -r` that a verifier needs to make
    the copy itself, so the engine makes it before the verifier starts. Give the
    directories to add to the session of the verifier: the proof copy, or none
    when the copy could not be made. The brief then tells the verifier what to do
    when the proof copy is missing.
    """
    proof = red_proof_copy(copy)
    try:
        # A link stays a link, so a link to a path outside the copy copies no file from there.
        shutil.copytree(copy, proof, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    except OSError:
        shutil.rmtree(proof, ignore_errors=True)
        return ()
    return (proof,)


def _journal_timeout(journal: Journal, result: AdapterResult, stack: Stack, ticket: str, role: str, round_number: int) -> None:
    """Record that the adapter ended the session of the agent at the agent timeout of the stack, if it did, before its result event."""
    if result.ended_timeout:
        journal.append("agent-timeout", ticket=ticket, role=role, round=round_number, seconds=seconds(stack.agent_timeout))


def _ended_reason(result: AdapterResult, who: str, stack: Stack) -> str:
    """The reason that an agent run is no done session: the time limit when the adapter ended it there, else the reason of
    the adapter (for example the text of the failed result of the harness, #271), else its end state and exit status."""
    if result.ended_timeout:
        return f"{who} session ran past the agent-timeout of {minutes_text(stack.agent_timeout)} minutes"
    if result.reason is not None:
        return f"{who} session failed: {result.reason}"
    return f"{who} ended {result.end_state} with exit status {result.exit_status}"


def _totals_fields(adapter: Adapter, stream: Path) -> dict[str, object]:
    """The token totals and the time of the last activity of the stream of an agent that ended, for its result event (#183).

    The engine reads the stream once, here, so the views read the journal and not the stream of an agent that ended. A
    stream that the adapter cannot read gives no field: the result event then has no totals, and a view reads the stream
    as it did before. The run does not fail for a missing total.
    """
    try:
        found = adapter.activity(stream)
    except Exception:  # noqa: BLE001 - the totals are for the views; the result of the agent does not depend on them
        return {}
    last = None if found.last_event is None else found.last_event.isoformat()
    return {"tokens_new": found.tokens_new, "tokens_cache": found.tokens_cache, "last_event": last}


def _journal_idle(journal: Journal, result: AdapterResult, ticket: str, role: str, round_number: int) -> None:
    """Record that the adapter ended the session of the agent at the idle timeout, if it did, before its result event."""
    if result.ended_idle:
        journal.append("idle-timeout", ticket=ticket, role=role, round=round_number, seconds=domain.IDLE_TIMEOUT_SECONDS)


def _session_done(result: AdapterResult, report_valid: Callable[[], bool]) -> bool:
    """Tell whether the engine takes the report of an agent run.

    A session that the adapter ended at the agent timeout (`ended_timeout`) is
    never done: the engine takes it as a session with no valid report, whatever
    its stream and its exit status say. A session that the adapter ended at the idle timeout had a valid report. A
    session whose stream ends in a success (`finished`) counts with exit status
    0, and with any other exit status when its report is valid: a coordinator
    or the adapter can stop an idle session after its work, with SIGTERM.
    """
    if result.ended_timeout:
        return False
    if result.ended_idle:
        return True
    if result.end_state != harness.FINISHED:
        return False
    return result.exit_status == 0 or report_valid()


def _prepare_worktree(vcs: VersionControl, repo: Path, worktree: Path, branch: str, base: str, hooks_dir: Path) -> bool:
    """Make the worktree of a ticket through the port, or reuse the one that is there, and install the push guard in it.

    Return True when the port made a new worktree, and False when it reused one.

    The port keeps the file watcher off for the worktree (`core.fsmonitor=false`
    in its own configuration), whatever the global setting of the user is.

    A worktree that a killed run left is reused as it is, except that a rebase
    which the kill stopped is aborted. A branch without a worktree gets a new
    worktree. Raise EngineError when the worktree holds another branch.
    """
    made = not worktree.is_dir()
    with vcs_errors():
        vcs.ensure_worktree(repo, worktree, branch, base)
    try:
        install_push_guard(vcs, repo, worktree, hooks_dir)
    except GuardError as error:
        raise EngineError(str(error)) from None
    return made


def _run_prepare(step: _Step, tree: str, path: Path, round_number: int) -> None:
    """Run the `prepare` commands of the stack of the step in `path`, in order, through the gate runner (#155).

    `tree` is TREE_WORKTREE or TREE_VERIFY_COPY. A command in the worktree gets the entries of the worktree (#221). A command in either tree
    gets `DELEGATE_REPO`, the path of the main checkout `repo` (#265): the copy of a verifier is a clone with no remote, so a command cannot
    find the main checkout from it. A gate and an agent session do not get the entry. A git tree holds no ignored
    file, so a stack that needs installed dependencies names the commands that
    install them. The engine runs them itself, with the environment of a gate,
    before an agent starts in the tree. The journal records `prepare-start` and
    `prepare-result` for each command, and each command gets its own log file.
    A command that fails is not a red gate: it starts no fix-up round. Raise
    `StepFailed` with the reason `prepare failed: <command>`, and run no later command.

    In the worktree, after the last command, the worktree must be clean: a
    changed or untracked path that is not ignored fails the step with the reason
    `prepare left <paths> in the worktree; ...` (#224), before the specialist
    starts and so before a check for uncommitted changes can take the path for
    work of the specialist.
    """
    gates, vcs, journal, stack, ticket, repo = step.run.gates, step.run.vcs, step.journal, step.stack, step.ticket.id, step.run.repo
    fields: dict[str, object] = {"ticket": ticket, "round": round_number, "tree": tree, "path": str(path)}
    # The verifier copy is a repository of its own, which holds its settings itself.
    environment = {**(worktree_environment(vcs, path) if tree == TREE_WORKTREE else {}), PREPARE_REPO_VARIABLE: str(repo)}
    for command in stack.prepare:
        log = gate_log_path(journal, ticket, f"prepare-{tree}", round_number)
        journal.append("prepare-start", **fields, command=command, prepare_log=str(log))
        result = gates.run(command, path, log=log, environment=environment)
        journal.append("prepare-result", **fields, command=command, exit_status=result.exit_status, prepare_log=str(log))
        if not result.green:
            raise StepFailed(f"prepare failed: {command}")
    if tree == TREE_WORKTREE:
        with vcs_errors():
            left = sorted({change.path for change in vcs.uncommitted_changes(path)})
        if left:
            raise StepFailed(
                f"prepare left {', '.join(left)} in the worktree; add them to .gitignore, or make prepare leave the worktree clean"
            )


def _prepare_ticket_worktree(step: _Step, made: bool) -> None:
    """Run the `prepare` commands of the stack of the step in its worktree when it needs them. Raise `StepFailed` when one fails.

    The worktree needs them when the engine made it now (`made`), or when the
    journal holds no complete prepare of it: a run that stopped or failed
    before the last command of `prepare` was green. A worktree that the engine
    prepared before keeps what the commands made, so they do not run again.
    """
    stack, ticket = step.stack, step.ticket.id
    if not stack.prepare:
        return
    if not made:
        last = None
        for event in step.journal.events:
            if event["event"] in ("prepare-start", "prepare-result") and event.get("ticket") == ticket and event.get("tree") == TREE_WORKTREE:
                last = event
        if last is not None and last["event"] == "prepare-result" and last["exit_status"] == 0 and last["command"] == stack.prepare[-1]:
            return
    _run_prepare(step, TREE_WORKTREE, step.worktree, 0)


def _build_ticket(
    run: RunContext, journal: Journal, ticket: Ticket, start_ref: str, prior: list[dict[str, object]] | None = None,
    retried: str | None = None,
) -> str | None:
    """Build one ticket. Return None when it is built, or the reason it failed.

    The branch of the ticket starts from `start_ref`: the base of the run, the
    run branch, or the branch of the previous ticket of a chain (see `_start_ref`).

    In `assure` mode a ticket is built only when its verifier accepts it.

    `prior` holds the journal events of this ticket when the step was open in a
    run that stopped. The step then goes on in its own worktree.

    `retried` is the reason a step failed, when a retry goes on with it. The step then goes on in its own
    worktree with the events of the failed step in `prior`. The specialist
    continues unless its report was valid and said committed, and it has the
    continuation limit of the mode again. A retry uses it in two cases:

    - In `economy` mode, for each retry that goes on in this function: at the specialist, or at the check of its
      report (see `retry._economy_retry_plan`). A retry that goes on at the chain fix-up does not call this function.
    - In `assure` mode, for a step that the agent check stopped before a specialist session (see
      `retry._stopped_before_specialist`), and for a step whose specialist gave a valid report that says partial or blocked
      and names no hotspot (#284): the plan is the specialist. The continuation brief of such a report gives its
      `blocked_reason`, also when the branch holds no commit. The other retries of `assure` mode go on in
      `_retry_ticket`, with no specialist: at the gates, at a verifier, or, after a stop before a fix-up session, at the fix-up of
      the same round (see `retry._stopped_before_fixup`).
    """
    workflow, vcs, repo = run.workflow, run.vcs, run.repo
    step = _Step(run, journal, ticket)
    tier, model = _specialist_model(run.roles, workflow, ticket)
    stack, branch, worktree, hooks_dir = step.stack, step.branch, step.worktree, run.hooks_dir(ticket.id)
    rebased_onto: str | None = None
    if prior is None:
        with vcs_errors():
            base_commit = vcs.commit_of(repo, start_ref)
        made = _prepare_worktree(vcs, repo, worktree, branch, start_ref, hooks_dir)
        journal.append(
            "step-start", ticket=ticket.id, stack=ticket.stack, branch=branch, worktree=str(worktree),
            base_commit=base_commit, agent=stack.specialist, tier=tier, model=model,
        )
        specialist_done, continuations, verify_rounds = False, 0, 0
        completed = False
    else:
        advance = step_scans.advance_place(prior)
        if advance >= 0:
            # The run branch holds the commit already. Only the step-end is missing.
            landed = str(prior[advance]["to_commit"])
            if not holds_commit(vcs, repo, landed, f"refs/heads/{workflow.run_branch}"):
                raise EngineError(f"run branch {workflow.run_branch} does not hold commit {landed}, which the journal says it advanced to")
            return None
        # Before anything else: a verifier that a stop cut off can have changed the real worktree (#192).
        _check_verifier_stop(journal, vcs, worktree, ticket.id, prior)
        if retried is None and workflow.mode == domain.ASSURE and _recover_advance(workflow, ticket, vcs, repo, journal, prior):
            return None
        base_commit = str(next(e for e in prior if e["event"] == "step-start")["base_commit"])
        made = _prepare_worktree(vcs, repo, worktree, branch, start_ref, hooks_dir)
        if retried is None:
            specialist_done = step_scans.specialist_done(prior)
            continuations = sum(1 for e in prior if e["event"] == "continuation")
        else:
            specialist_done, continuations = specialist_committed(tuple(prior)), 0
        completed = _coordinator_completes(step_scans.step_events(journal.events, ticket.id))
        # A verifier run that ended with no verdict does not count against the fix-up limit; a red-gate fix-up round does.
        verify_rounds = rounds_used(prior)
        rebases = [e for e in prior if e["event"] == "rebase" and e["result"] == "rebased"]
        if rebases:
            rebased_onto = str(rebases[-1]["onto_commit"])
    try:
        _prepare_ticket_worktree(step, made)
    except StepFailed as failed:
        return str(failed)
    # The commits of a ticket are the commits beyond the point where its branch started, or, after a rebase,
    # beyond the commit of the run branch that the branch was rebased onto. So the hotspot check and the
    # continuation brief see the commits of this ticket only.
    since = domain.work_starts_at(base_commit, rebased_onto)
    reason = _build_with_continuations(
        step, since, spawned=specialist_done, continuations=continuations, interrupted=prior is not None,
        stop_reason="the run was interrupted" if retried is None else f"the step failed: {retried}; after that, the coordinator retried the step",
        completed=completed, reported=retried is not None and unfinished_report(step_scans.specialist_reports(prior)) is not None,
    )
    if reason:
        return reason
    if workflow.mode == "assure":
        reason = _rebase_onto_run_branch(step)
        return reason if reason else _verify_ticket(step, verify_rounds)
    return None


def _coordinator_completes(own: Sequence[dict[str, object]]) -> bool:
    """True when a retry at the gates with a commit of the coordinator came after the last spawn of the specialist (#264).

    `own` are the journal events of the step (see `step_scans.step_events`). In `assure` mode such a retry goes on with a step
    whose specialist reported partial or blocked because the work needs a hotspot (see `retry.retry_plan`): the commit of the
    coordinator completes the work, so the report of that session does not fail the step. A new session of the
    specialist writes a new report, which counts as it is.
    """
    spawned = max((index for index, e in enumerate(own) if e["event"] in ("step-start", "continuation")), default=-1)
    return any(
        e["event"] == "retry" and e.get("coordinator_commit") and e["phase"] == RETRY_PHASE_GATES for e in own[spawned + 1 :]
    )


def _recover_advance(workflow: Workflow, ticket: Ticket, vcs: VersionControl, repo: Path, journal: Journal, prior: list[dict[str, object]]) -> bool:
    """Write the `run-branch-advance` that a stop left out, and tell whether the step is closed.

    The engine moves the run branch first and writes `run-branch-advance` after it. A stop between the two leaves an
    open step whose commit is on the run branch already. Git is the record of the work that landed, so the step is
    recovered when the newest verdict of the step is ACCEPT and the run branch holds the commit of the `verify-start`
    of that verdict. The event has `recovered: true`. Return False, and write nothing, in any other case.
    """
    found = step_scans.accepted_verdict(prior)
    if found is None:
        return False
    accepted = found.commit
    run_branch = workflow.run_branch
    if not holds_commit(vcs, repo, accepted, f"refs/heads/{run_branch}"):
        return False
    # The step fast-forwarded from the commit that it rebased onto, or from its own base when it did not rebase.
    rebases = [e for e in prior if e["event"] == "rebase" and e["result"] == "rebased"]
    from_commit = str(rebases[-1]["onto_commit"]) if rebases else str(next(e for e in prior if e["event"] == "step-start")["base_commit"])
    journal.append(
        "run-branch-advance", ticket=ticket.id, run_branch=run_branch, from_commit=from_commit, to_commit=accepted, recovered=True
    )
    return True


def _recover_chain_advance(workflow: Workflow, vcs: VersionControl, repo: Path, journal: Journal, events: list[dict[str, object]]) -> None:
    """Write the `run-branch-advance` of an `economy` chain that a stop left out (#279).

    The engine moves the run branch to the tip of the chain first, and writes `run-branch-advance` after it. A stop
    between the two leaves a chain whose work is on the run branch with no record. The chain is closed when the newest
    verdict after the newest `run-branch-advance` is ACCEPT and the run branch holds the commit of the `verify-start` of
    that verdict: the event then has `recovered: true`, and names the ticket of that verdict, as `_advance_run_branch`
    does. The verification of the chain then finds the work on the run branch, and starts no agent. In any other case,
    and for a journal with no recorded tip, write nothing.
    """
    found = step_scans.accepted_verdict(step_scans.events_after_advance(events))
    from_commit = _expected_tip(events)
    if found is None or from_commit is None:
        return
    accepted = found.commit
    run_branch = workflow.run_branch
    if not holds_commit(vcs, repo, accepted, f"refs/heads/{run_branch}"):
        return
    journal.append(
        "run-branch-advance", ticket=str(found.verdict["ticket"]), run_branch=run_branch, from_commit=from_commit,
        to_commit=accepted, recovered=True,
    )


def _build_with_continuations(
    step: _Step, since: str, *, spawned: bool = False, continuations: int = 0, interrupted: bool = False,
    stop_reason: str = "the run was interrupted", completed: bool = False, reported: bool = False,
) -> str | None:
    """Run the specialist of the step, check its work, and continue it in the same worktree when the work is not done.

    `since` is the commit or branch where the branch of the ticket started: the commits beyond it
    are the work of this ticket. The specialist has the scratch directory `scratch/<ticket>/specialist/` of the run (see
    `_scratch_directory`): each session gets it, and each brief names it.

    A resume sets `spawned` when the journal shows that the specialist finished
    with a valid report, so the first pass does not spawn it again. It sets
    `continuations` to the number the journal holds, so the limit counts across
    the stop. It sets `interrupted` for a step that a stop left open: when the
    branch holds commits already, the first prompt is a continuation brief,
    with `stop_reason` as the reason that the last run stopped. A retry after a
    valid report of the specialist that says partial or blocked also sets
    `reported`: the first prompt is then a continuation brief also when the
    branch holds no commit, so the specialist gets the `blocked_reason` of its
    report (#284). It sets
    `completed` when a commit of the coordinator completes the work of a report
    that says partial or blocked (see `_coordinator_completes`): the first pass
    then checks that report as if it said committed.

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
    run, journal, ticket, stack = step.run, step.journal, step.ticket, step.stack
    workflow, adapter, vcs, repo = run.workflow, run.adapter, run.vcs, run.repo
    worktree, branch = step.worktree, step.branch
    _tier, model = _specialist_model(run.roles, workflow, ticket)
    report_path = step.run_dir / "reports" / f"{ticket.id}.specialist.json"
    scratch = _scratch_directory(step.run_dir, ticket.id, ROLE_SPECIALIST)
    limit = domain.continuation_limit(workflow.mode)
    each_ticket = domain.gates_each_ticket(workflow.mode)
    plan_change = _plan_change_note(journal, workflow, [ticket.id], verifier=False)
    inputs = _specialist_inputs(
        step, report_path, scratch, plan_change=plan_change, notes=stack.specialist_notes, gates_at_chain_end=not each_ticket,
    )
    prompt = specialist_brief(inputs)
    if interrupted and not spawned:
        so_far = _commit_lines(vcs, worktree, since)
        if so_far or reported:
            prompt = continuation_brief(inputs, Continuation(stop_reason, tuple(so_far)), resumed=False)
    resume_session: str | None = None
    leftovers: frozenset[str] = frozenset()  # the paths that red gates left in the worktree (see `_uncommitted_before_gates`)
    report_valid = _report_check(lambda: read_specialist_report(report_path, ticket.id))
    report_before: _ReportBefore | None = None  # the state of the report file before the session; None for a session that ended before a kill
    while True:
        if spawned:
            spawned, result, report_before = False, None, None
        else:
            try:
                sealed = _check_agent_file(journal, stack.specialist, ticket.id, ROLE_SPECIALIST, 0)
            except StepFailed as failed:
                return str(failed)
            stream = _start_agent(journal, ticket.id, ROLE_SPECIALIST, 0, report_path)
            report_before = _ReportBefore(_report_state(report_path))
            session = _Session(stack.specialist, model, sealed, worktree, report_path, stream, scratch)
            result = _run_agent(
                adapter, journal, ticket.id, ROLE_SPECIALIST, 0, _request(step, session, prompt, report_valid, resume_session),
            )
            _journal_idle(journal, result, ticket.id, ROLE_SPECIALIST, 0)
            _journal_timeout(journal, result, stack, ticket.id, ROLE_SPECIALIST, 0)
            journal.append(
                "adapter-result", ticket=ticket.id, exit_status=result.exit_status, end_state=result.end_state,
                session_id=result.session_id, event_stream=str(result.event_stream), **_refused_fields(result),
                **_totals_fields(adapter, result.event_stream),
            )
        gate_output: str | None = None
        gate_logs: list[GateLogCopy] = []
        problems: tuple[str, ...] = ()
        uncommitted_paths: tuple[str, ...] = ()
        if result is not None and not _session_done(result, report_valid):
            trigger = harness.CAPPED if result.end_state == harness.CAPPED else harness.FAILED
            reason = _ended_reason(result, "the specialist", stack)
        else:
            try:
                report = _read_session_report(report_path, ticket.id, report_before)
            except ReportError as error:
                journal.append(
                    "report-validation", ticket=ticket.id, valid=False, path=str(report_path), reason=str(error),
                    status=None, blocked_reason=None,
                )
                # A report that is missing or not valid, on a branch with work, gets a continuation for the report only.
                if not _commit_lines(vcs, repo, since, branch):
                    return str(error)
                missing = not report_path.is_file() or _report_is_stale(report_path, report_before)
                trigger = TRIGGER_REPORT_MISSING if missing else TRIGGER_REPORT_INVALID
                reason, problems = str(error), error.problems
            else:
                journal.append(
                    "report-validation", ticket=ticket.id, valid=True, path=str(report_path), reason=None,
                    status=report.status, blocked_reason=report.blocked_reason, **_lenient_fields(report),
                )
                unfinished = domain.unfinished_reason(report.status, report.blocked_reason, "the specialist")
                if unfinished and not (completed and result is None):
                    return unfinished
                if not _commit_lines(vcs, repo, since, branch):
                    return f"branch {branch} holds no commit beyond {since}, though the report says committed"
                stop = _hotspot_stop(step, since, True, GATE_PHASE_BUILD, 0)
                if stop:
                    return stop
                # A gate runs in the worktree as it is, so a change that is not committed would make its result a fact about
                # something else than the commit. `economy` mode runs no gate here, but its chain gates run on this tree later.
                # The specialist was the last step that can move HEAD, so one read serves the check and the first gate (#239).
                head = head_commit(vcs, worktree) if each_ticket else None
                uncommitted = _uncommitted_before_gates(journal, vcs, worktree, stack.gates if each_ticket else None, leftovers, head)
                if uncommitted:
                    trigger, reason, uncommitted_paths = TRIGGER_UNCOMMITTED, _uncommitted_reason(uncommitted), tuple(uncommitted)
                elif head is None:
                    return None  # `economy` mode: the gates run once, on the tip of the chain (see `chain.ChainRun`)
                else:
                    outcomes = gate_outcomes(step, 0, GATE_PHASE_BUILD, head)
                    red = [outcome for outcome in outcomes if not outcome.green]
                    if not red:
                        return None
                    trigger = "gates-red"
                    # The gates ran in a tree with no change that is not committed, so what the tree holds now is theirs.
                    leftovers = frozenset(_uncommitted_paths(vcs, worktree))
                    reason = "gates red: " + "; ".join(outcome.label for outcome in red)
                    gate_output = "\n".join(f"$ {outcome.command}\n{outcome.output.rstrip(chr(10))}" for outcome in red)
                    gate_logs = copy_gate_logs(red, scratch)
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
            prompt = report_correction_brief(inputs, commits, problems)
        else:
            prompt = continuation_brief(
                inputs, Continuation(reason, tuple(commits), gate_output, tuple(uncommitted_paths), tuple(gate_logs)),
                resumed=resume_session is not None,
            )


def _uncommitted_paths(vcs: VersionControl, worktree: Path) -> list[str]:
    """The paths of the worktree that differ from its commit, in the order of the paths. A path that git ignores is not one."""
    with vcs_errors():
        return [change.path for change in vcs.uncommitted_changes(worktree)]


def _uncommitted_before_gates(
    journal: Journal, vcs: VersionControl, worktree: Path, gates: Sequence[str] | None, leftovers: frozenset[str] = frozenset(),
    head: str | None = None,
) -> list[str]:
    """The paths that a change not committed holds in the worktree, when a gate is about to run in it. Else an empty list.

    A gate result is a fact about the commit that it names, so a gate must not
    run in a worktree that holds something else (#176). `gates` is the list of
    gates of the phase: when each one has a clean green result on the commit
    already, the phase runs no gate, and what the worktree holds does not
    matter. `gates` is None when the caller runs no gate itself but the tree is
    the tree of a later gate, so the answer does not depend on the journal.
    A path in `leftovers` is a file that an earlier gate run of this process
    left in a tree that was clean before it: it is no work of the specialist.
    `head` is the HEAD of the worktree when the caller read it after the last step that can move it, or None.
    """
    if gates is not None:
        commit = head if head is not None else head_commit(vcs, worktree)
        if all(green_result(journal, gate, commit) is not None for gate in gates):
            return []
    return [path for path in _uncommitted_paths(vcs, worktree) if path not in leftovers]


def _uncommitted_reason(paths: Sequence[str]) -> str:
    """The reason that no gate ran: the paths with a change that is not committed."""
    return "the worktree holds changes that are not committed: " + ", ".join(f"`{path}`" for path in paths)


def _hotspot_stop(step: _Step, since: str, merge_base: bool, phase: str, round_number: int) -> str | None:
    """Compare the paths that the specialist changed since `since` in the worktree of the step with the hotspot patterns of the stack.

    Return None when none matches, or the reason the step fails. A match is
    recorded as `hotspot-finding`. The caller stops the step before any gate
    and any verifier runs.

    The change of a commit of the coordinator that a retry of the step recorded (see `coordinator_commits`) does not
    count (#225). Each other commit of the step counts.
    """
    vcs, journal, ticket, worktree = step.run.vcs, step.journal, step.ticket.id, step.worktree
    hotspots = step.stack.all_hotspots(step.run.config_paths)
    excluded = {commit for commit, _ in coordinator_commits(vcs, worktree, step_scans.step_events(journal.events, ticket))}
    if excluded:
        with vcs_errors():
            paths = paths_without(vcs, worktree, since, None, merge_base, excluded)
    else:
        paths = changed_paths(vcs, worktree, since, merge_base=merge_base)
    matches = hotspot_matches(paths, hotspots)
    if not matches:
        return None
    journal.append(
        "hotspot-finding", ticket=ticket, phase=phase, round=round_number,
        matches=[{"path": m.path, "pattern": m.pattern} for m in matches],
    )
    return "hotspot finding: " + "; ".join(f"{m.path} matches the hotspot {m.pattern!r}" for m in matches)


@dataclass(frozen=True)
class _Step:
    """The state that the specialist, the gates, the verifier step and the fix-up rounds of one ticket share.

    `run` is the context of the run (#342), and `journal` its journal.
    """

    run: RunContext
    journal: Journal
    ticket: Ticket
    # A step that verifies the chain of a stack (see `chain.ChainRun`) works on the branch and in the worktree of
    # the ticket at the tip of the chain, `anchor`, and verifies the tickets in `segments`. The `ticket` of such
    # a step is made for the stack: it holds the id of the last ticket of the stack and the text of all of them.
    anchor: Ticket | None = None
    segments: tuple[ChainSegment, ...] | None = None

    @property
    def stack(self) -> Stack:
        return self.run.workflow.stacks[self.ticket.stack]

    @property
    def branch(self) -> str:
        return ticket_branch(self.run.workflow, self.anchor or self.ticket)

    @property
    def worktree(self) -> Path:
        return self.run.worktree((self.anchor or self.ticket).id)

    @property
    def chain_fields(self) -> dict[str, object]:
        """The journal fields that name the stack and the tickets of a chain verification. Empty for one ticket."""
        if self.segments is None:
            return {}
        return {"stack": self.ticket.stack, "tickets": [segment.ticket for segment in self.segments]}

    @property
    def run_dir(self) -> Path:
        return self.run.run_dir


def _rebase_onto_run_branch(step: _Step) -> str | None:
    """Rebase the ticket branch onto the run branch, then run the gates again on the rebased tree.

    A rebase that keeps the commit runs no gate again: the green results of the
    build hold for the same commit, and the journal records `gate-skip` for each
    gate (see `gate_run.run_gate_with_output`).

    Return None when the branch is rebased and the gates are green, or the
    reason the step fails. A rebase that stops is aborted, so the branch and its
    worktree keep the state from before the rebase. A rebase that git refuses to
    start, or one that ended with a failed hook, leaves nothing to abort.
    """
    ticket, run_branch = step.ticket, step.run.workflow.run_branch
    onto = _check_run_branch(step.journal, step.run.vcs, step.run.repo, run_branch, ticket.id)
    before = head_commit(step.run.vcs, step.worktree)
    with vcs_errors():
        rebase = step.run.vcs.rebase_onto(step.worktree, run_branch)
    if rebase.status != REBASED:
        files = list(rebase.conflicts)
        step.journal.append(
            "rebase", ticket=ticket.id, result="conflict" if files else "failed", onto_commit=onto,
            from_commit=before, to_commit=None, files=files,
        )
        if files:
            return f"rebase onto {run_branch} stopped with a conflict in {', '.join(files)}"
        return f"rebase onto {run_branch} failed: {rebase.detail}"
    after = head_commit(step.run.vcs, step.worktree)
    step.journal.append(
        "rebase", ticket=ticket.id, result="rebased", onto_commit=onto, from_commit=before, to_commit=after, files=[]
    )
    if not _commit_lines(step.run.vcs, step.run.repo, run_branch, step.branch):
        return (
            f"branch {step.branch} holds no commit beyond {run_branch} after the rebase; "
            f"{run_branch} holds its work already"
        )
    # The rebase was the last step that can move HEAD, so the first gate uses `after` (#239).
    outcomes = gate_outcomes(step, 0, GATE_PHASE_REBASE, after)
    red = [outcome.label for outcome in outcomes if not outcome.green]
    if red:
        return "gates red: " + "; ".join(red) + f" (after the rebase onto {run_branch})"
    return None


def _verify_ticket(step: _Step, first_round: int = 0) -> str | None:
    """Verify the built branch of a ticket blind. Return None on ACCEPT, or the reason the step fails.

    On ACCEPT the run branch moves to the commit that the verifier saw.
    """
    try:
        return _advance_run_branch(step, _accepted_commit(step, first_round))
    except StepFailed as failed:
        return str(failed)


def _accepted_commit(step: _Step, first_round: int = 0, first_fixup: tuple[str, str] | None = None) -> str:
    """Verify the branch of the step blind, and return the commit that a verifier accepted.

    A REJECT starts a fix-up round, up to `FIXUP_ROUND_LIMIT` of the mode.
    Each round, and the first pass, spawns a fresh verifier. Raise `Rejected`
    after the last round, and `StepFailed` for any other cause.

    Each verifier brief gives the output of the gates that the engine ran green
    on the commit under verification, so the verifier does not run them again
    (see `_engine_gates_text`).

    Red gates after a fix-up start the next fix-up round with the gate output,
    while rounds remain, and no verifier runs before it (see `_fixup_gates`).
    Such a round counts against the limit as each other round does (#277).

    A resume sets `first_round` to the number of rounds that the journal
    holds (see `rounds_used`). The first pass of the resume is a full verification at that round
    number, and the limit counts across the stop. A retry that goes on at the
    scoped verifier gives the rejected commit and the findings text of its
    fix-up round in `first_fixup`: the first pass is then the scoped verifier
    of the fix-up commit that the branch holds.
    """
    mode = step.run.workflow.mode
    used_up = domain.rounds_used_up_reason(mode, first_round)
    if used_up:
        raise StepFailed(used_up)
    rejected, findings_text = first_fixup if first_fixup is not None else ("", None)
    round_number = first_round
    while True:
        # The fix-up and its gates are the last steps that can move the branch before the verifier: one read serves the
        # gates text, the brief and the verifier (#239).
        verified = branch_tip(step.run.vcs, step.run.repo, step.branch)
        gates_text = _engine_gates_text(step, verified)
        fixup_body = (
            None if findings_text is None
            else _checked_fixup_brief(step, round_number, rejected, findings_text, gates_text)
        )
        report = _verify_round(step, round_number, fixup_body, gates_text, verified=verified)
        if domain.is_accepted(report.verdict):
            return verified
        if round_number >= domain.fixup_round_limit(mode):
            raise Rejected(domain.rejection_reason(mode, report.findings), report.findings)
        rejected = verified
        round_number, findings_text = _fixup_round(step, round_number + 1, rejected, report.findings)


def _fixup_round(step: _Step, round_number: int, rejected: str, findings: list[str]) -> tuple[int, str]:
    """Run one fix-up round. Return the round of the scoped verifier, and the findings text for its scoped brief.

    The engine writes the findings to a file and sends the specialist a fix-up
    brief in the same worktree (see `_run_fixup`). The fix must be a new commit
    on top of the rejected commit, which the brief generator checks before any
    gate runs. The engine then runs the gates itself. Red gates start the next
    round (see `_fixup_gates`), so the round of the scoped verifier can be
    later than `round_number`. The caller makes the scoped brief after the
    gates, so the brief holds their green output.
    """
    findings_text = _run_fixup(step, round_number, rejected, _findings_text(findings), fields={"trigger": TRIGGER_REJECT})
    _checked_fixup_brief(step, round_number, rejected, findings_text)
    return _fixup_gates(step, round_number), findings_text


def _engine_gates_text(step: _Step, commit: str) -> str | None:
    """The `## Gates` text of a verifier brief of one ticket: the green gate output of the engine on `commit`, the tip of its branch.

    The verifier reviews the tip of the branch of the step: the rebased commit,
    or the fix-up commit. When the journal holds a green `gate-result` of each
    gate of the stack on that commit, the text gives that output (see
    `green_gates_text`), and the verifier does not run the gates again, as for
    a chain. Return None for a stack with no gates, or when a gate has no green
    result on that commit: the brief then gives the gate commands.
    """
    outputs: list[tuple[str, str]] = []
    for gate in step.stack.gates:
        green = green_result(step.journal, gate, commit)
        if green is None:
            return None
        outputs.append((gate, str(green.get("output_tail", ""))))
    return green_gates_text(commit, outputs) if outputs else None


def _run_fixup(
    step: _Step, round_number: int, rejected: str, findings: str, red_gates: bool = False, fields: dict[str, object] | None = None
) -> str:
    """Write the findings to a file, and run the fix-up specialist in the worktree of the step. Return the findings text.

    `findings` are the findings of a REJECT, or with `red_gates` the output of
    the gates that were red on `rejected`. `fields` go in the `fixup-start`
    event too. Raise `StepFailed` when the fix-up specialist does not end with
    a valid report that says committed.
    """
    ticket = step.ticket
    tier, model = _specialist_model(step.run.roles, step.run.workflow, ticket)
    findings_path = step.run_dir / "findings" / f"{ticket.id}.fixup-{round_number}.md"
    findings_path.parent.mkdir(parents=True, exist_ok=True)
    findings_path.write_text(findings)
    findings_text = findings_path.read_text()
    # The check comes before `fixup-start`: a stop leaves no `fixup-start`, so the stop is no round (#209), and `agents-changed`
    # holds what a retry needs to run the fix-up again.
    sealed = _check_agent_file(
        step.journal, step.stack.specialist, ticket.id, ROLE_FIXUP, round_number,
        {"rejected_commit": rejected, "findings_file": str(findings_path), **(fields or {})},
    )
    step.journal.append(
        "fixup-start", ticket=ticket.id, round=round_number, rejected_commit=rejected, findings_file=str(findings_path),
        agent=step.stack.specialist, tier=tier, model=model, **(fields or {}),
    )
    report_path = step.run_dir / "reports" / f"{ticket.id}.fixup-{round_number}.specialist.json"
    # The gates ran before the fix-up in a tree with no change that is not committed, so what it holds now is theirs.
    leftovers = frozenset(_uncommitted_paths(step.run.vcs, step.worktree))
    scratch = _scratch_directory(step.run_dir, ticket.id, ROLE_FIXUP)
    inputs = _specialist_inputs(step, report_path, scratch, notes=step.stack.specialist_notes)
    prompt = specialist_brief(inputs, rejected, findings_text, red_gates=red_gates)
    stream = _start_agent(step.journal, ticket.id, ROLE_FIXUP, round_number, report_path)
    report_valid = _report_check(lambda: read_specialist_report(report_path, ticket.id))
    session = _Session(step.stack.specialist, model, sealed, step.worktree, report_path, stream, scratch)
    result = _run_agent(step.run.adapter, step.journal, ticket.id, ROLE_FIXUP, round_number, _request(step, session, prompt, report_valid))
    _journal_idle(step.journal, result, ticket.id, ROLE_FIXUP, round_number)
    _journal_timeout(step.journal, result, step.stack, ticket.id, ROLE_FIXUP, round_number)
    step.journal.append(
        "fixup-result", ticket=ticket.id, round=round_number, exit_status=result.exit_status, end_state=result.end_state,
        session_id=result.session_id, event_stream=str(result.event_stream), **_refused_fields(result),
        **_totals_fields(step.run.adapter, result.event_stream),
    )
    if not _session_done(result, report_valid):
        raise StepFailed(_ended_reason(result, "the fix-up specialist", step.stack))
    try:
        report = read_specialist_report(report_path, ticket.id)
    except ReportError as error:
        step.journal.append(
            "fixup-report", ticket=ticket.id, round=round_number, valid=False, path=str(report_path), reason=str(error),
            status=None, blocked_reason=None,
        )
        raise StepFailed(str(error)) from None
    step.journal.append(
        "fixup-report", ticket=ticket.id, round=round_number, valid=True, path=str(report_path), reason=None,
        status=report.status, blocked_reason=report.blocked_reason, **_lenient_fields(report),
    )
    unfinished = domain.unfinished_reason(report.status, report.blocked_reason, "the fix-up specialist")
    if unfinished:
        raise StepFailed(unfinished)
    # The gates run next, and a gate runs in the worktree as it is (#176). A fix-up with no new commit is refused
    # by the brief generator (see `_checked_fixup_brief`), with a message of its own.
    uncommitted = [path for path in _uncommitted_paths(step.run.vcs, step.worktree) if path not in leftovers]
    if uncommitted and head_commit(step.run.vcs, step.worktree) != rejected:
        raise StepFailed(_uncommitted_reason(uncommitted) + f"; no gate ran (after the fix-up of round {round_number})")
    return findings_text


def _findings_text(findings: list[str]) -> str:
    """The findings of a verdict as the fix-up specialist and the scoped verifier get them: one list item for each."""
    return "".join(f"- {finding}\n" for finding in findings)


def _specialist_inputs(
    step: _Step, report_path: Path, scratch: Path, *, plan_change: str | None = None, notes: str | None = None,
    gates_at_chain_end: bool = False,
) -> SpecialistInputs:
    """The inputs of the briefs of the specialist of `step`: the ticket, its worktree, its stack, and the paths of the session (#362)."""
    ticket, run = step.ticket, step.run
    return SpecialistInputs(
        ticket.id, ticket.text, step.worktree, tuple(step.stack.all_hotspots(run.config_paths)), tuple(step.stack.gates),
        report_path, gates_at_chain_end, plan_change, notes, scratch,
    )


def _verifier_inputs(step: _Step, repo: str | Path, gates_text: str | None) -> VerifierInputs:
    """The inputs of the briefs of the verifier of `step`, which read `repo`: the gate commands of its stack and its diff limit (#362)."""
    return VerifierInputs(
        step.run.vcs, repo, gate_commands=tuple(step.stack.gates), gates_text=gates_text, diff_limit=step.stack.verifier_diff_limit,
    )


def _checked_fixup_brief(step: _Step, round_number: int, rejected: str, findings_text: str, gates_text: str | None = None) -> str:
    """Check the fix-up commit of a round, and return the scoped brief for the fresh verifier.

    The paths that the fix-up changed must match no hotspot, and the brief
    generator must accept the fix as a new commit on top of the rejected commit.
    Raise `StepFailed` otherwise. `gates_text` is the text of the gates section
    of the brief when the engine ran the gates green (see `green_gates_text`).
    """
    ticket = step.ticket
    stop = _hotspot_stop(step, rejected, False, GATE_PHASE_FIXUP, round_number)
    if stop:
        raise StepFailed(stop)
    try:
        return fixup_brief(_verifier_inputs(step, step.run.repo, gates_text), step.branch, rejected, findings_text)
    except BriefError as error:
        step.journal.append("fixup-refused", ticket=ticket.id, round=round_number, reason=str(error))
        raise StepFailed(f"the fix-up of round {round_number} is refused: {error}") from None


def _fixup_gates(step: _Step, round_number: int) -> int:
    """Run the gates after the fix-up of `round_number`, until they are green. Return the round of the last fix-up.

    Red gates start the next fix-up round while rounds remain, as on the tip of an `economy` chain (#277). The findings
    of that round are the names and the output of the red gates on the fix-up commit, and its rejected commit is that
    fix-up commit. No verifier runs before it. Raise `StepFailed`, whose reason names the red gates, when the gates are
    red after the last fix-up round of the mode.
    """
    mode = step.run.workflow.mode
    while True:
        tip = head_commit(step.run.vcs, step.worktree)
        outcomes = gate_outcomes(step, round_number, GATE_PHASE_FIXUP, tip)
        red = [outcome for outcome in outcomes if not outcome.green]
        if not red:
            return round_number
        if round_number >= domain.fixup_round_limit(mode):
            raise StepFailed(domain.fixup_gates_reason(mode, [outcome.label for outcome in red]))
        round_number += 1
        copies = copy_gate_logs(red, _scratch_directory(step.run_dir, step.ticket.id, ROLE_FIXUP))
        findings_text = _run_fixup(
            step, round_number, tip, red_gates_text(tip, [(outcome.command, outcome.output) for outcome in red], copies, "the fix-up commit"),
            True, {"trigger": TRIGGER_GATES_RED},
        )
        _checked_fixup_brief(step, round_number, tip, findings_text)


def _journal_violation(journal: Journal, ticket: str, round_number: int, violation: InvariantViolation) -> None:
    """Write `invariant-violation` for the change that `violation` names."""
    journal.append(
        "invariant-violation", ticket=ticket, round=round_number, worktree=str(violation.worktree),
        head_before=violation.head_before, head_after=violation.head_after, changes=violation.changes,
    )


def _check_verifier_stop(journal: Journal, vcs: VersionControl, worktree: Path, ticket: str, events: list[dict[str, object]]) -> None:
    """Halt a resume when a verifier that a stop cut off changed the real worktree (#192).

    `events` are the journal events of the step. Do nothing unless the newest `verify-start` has no `verify-result` after
    it, and records a snapshot. The session then did not end, so the engine did not make its check after the session.
    Compare the worktree with the snapshot of the `verify-start`. On a difference write `invariant-violation` and raise
    `InvariantViolation`, as the check after a session does. A journal of an earlier version has no snapshot: do nothing.
    """
    starts = [index for index, e in enumerate(events) if e["event"] == "verify-start"]
    if not starts or not worktree.is_dir():
        return
    start = events[starts[-1]]
    record = start.get("snapshot")
    if record is None or any(e["event"] == "verify-result" for e in events[starts[-1] + 1 :]):
        return
    try:
        check_worktree_unchanged(vcs, WorktreeSnapshot.from_record(worktree, record))  # type: ignore[arg-type]
    except InvariantViolation as violation:
        _journal_violation(journal, ticket, int(start["round"]), violation)  # type: ignore[call-overload]
        raise


def _verify_round(
    step: _Step, round_number: int, fixup_body: str | None, gates_text: str | None = None, fixups: tuple[str, str] | None = None,
    earlier_fixups: tuple[str, str] | None = None, *, verified: str | None = None,
) -> VerifierReport:
    """Run one fresh verifier in a new temporary copy and return its valid verdict report.

    With no `fixup_body` the pass is full. A fix-up round gets the fix-up brief in `fixup_body`.
    The verifier gets the task or the findings, and the diff or the delta, and
    the gates. It never gets the report of the specialist.

    `gates_text` gives the output of the gates that the engine ran green on the
    tip, so the verifier does not run them. A fix-up brief in `fixup_body`
    holds the same text. For a chain, `fixups` gives the
    fix-up commits of red gates that the full pass must see, and `earlier_fixups`
    gives the fix-up commits of the stacks verified before (see `chain_brief`).

    `verified` is the tip of the branch of the step, when the caller read it after the last step that can move the
    branch. With None the round reads it.
    """
    ticket = step.ticket
    where = f"stack {ticket.stack}" if step.segments is not None else f"ticket {ticket.id}"
    pause_point(step.journal, ticket.id, PAUSED_BEFORE_VERIFIER, f"before the verifier of {where}", round=round_number, **step.chain_fields)
    tier, model = step.run.roles["verifier"]
    mode = "full" if fixup_body is None else "fix-up"
    if verified is None:
        verified = branch_tip(step.run.vcs, step.run.repo, step.branch)
    evidence = step.run_dir / "verifier" / ticket.id
    if round_number:
        evidence = evidence / f"fixup-{round_number}"
    own_scratch = _verifier_scratch_directory(step.run_dir.name, ticket.id)
    try:
        scratch = Path(tempfile.mkdtemp(prefix=VERIFY_SCRATCH_PREFIX, dir=_verifier_temp_parent(own_scratch)))
    except OSError:
        shutil.rmtree(own_scratch, ignore_errors=True)
        raise
    try:
        copy = scratch / VERIFY_COPY_NAME
        report_path = scratch / "verdict.json"
        try:
            with vcs_errors():
                step.run.vcs.make_verifier_copy(step.run.repo, copy, step.branch, step.run.workflow.run_branch)
            # Each verifier brief holds the verifier notes of the stack after the gates, before the working copy.
            notes = notes_section(step.stack.verifier_notes)
            # Each commit of the coordinator on the branch under verification is named, so the verifier checks it too (#225).
            coordinator = _coordinator_of_step(step)
            inputs = _verifier_inputs(step, copy, gates_text)
            if step.segments is not None:
                # A chain: the brief holds the tickets of the stack, each with its own diff, and the finding labels.
                first_pass = (
                    chain_brief(inputs, step.segments, fixups, earlier_fixups)
                    + plan_change_section(_plan_change_note(step.journal, step.run.workflow, [s.ticket for s in step.segments], verifier=True))
                    if fixup_body is None else fixup_body + finding_labels_section()
                )
                prompt = (
                    first_pass + coordinator_section(coordinator) + notes
                    + verifier_run_sections(copy, report_path, mode, engine_gates=gates_text is not None, scratch=own_scratch)
                )
            elif fixup_body is None:
                prompt = verifier_run_brief(
                    inputs, step.branch, step.run.workflow.run_branch, ticket.text,
                    VerifierSession(report_path, own_scratch, step.stack.verifier_notes, tuple((c, tuple(p)) for c, p in coordinator)),
                    plan_change=_plan_change_note(step.journal, step.run.workflow, [ticket.id], verifier=True),
                )
            else:
                prompt = (
                    fixup_body + coordinator_section(coordinator) + notes
                    + verifier_run_sections(copy, report_path, mode, engine_gates=gates_text is not None, scratch=own_scratch)
                )
        except (EngineError, BriefError) as error:
            raise StepFailed(f"the verifier could not start: {error}") from None
        # The proof copy comes after `prepare`, so it holds what the commands made in the copy.
        _run_prepare(step, TREE_VERIFY_COPY, copy, round_number)
        # In a chain, only the retry of the ticket at the tip goes on after the stop (#261), so the message names it.
        sealed = _check_agent_file(
            step.journal, step.stack.verifier, ticket.id, ROLE_VERIFIER, round_number,
            retry=step.anchor.id if step.anchor is not None else None,
        )
        added = _make_proof_copy(copy)
        # The snapshot goes in `verify-start`, so a resume after a stop in the session can still compare the worktree (#192).
        before = snapshot_worktree(step.run.vcs, step.worktree)
        step.journal.append(
            "verify-start", ticket=ticket.id, round=round_number, agent=step.stack.verifier, tier=tier, model=model,
            commit=verified, copy=str(copy), scratch=str(own_scratch), snapshot=before.to_record(), **step.chain_fields,
        )
        # The stream goes in the evidence directory from the start, outside the copy, so it stays when the copy goes.
        stream = _start_agent(step.journal, ticket.id, ROLE_VERIFIER, round_number, evidence / report_path.name)
        # The check reads the report where the verifier writes it, in the scratch directory; `report_path` moves later.
        in_scratch = report_path
        report_valid = _report_check(lambda: read_verifier_report(in_scratch, mode))
        session = _Session(step.stack.verifier, model, sealed, copy, report_path, stream, own_scratch, added)
        result = _run_agent(step.run.adapter, step.journal, ticket.id, ROLE_VERIFIER, round_number, _request(step, session, prompt, report_valid))
        # The check runs at once, before the evidence is moved; the journal and the halt come after it.
        violation: InvariantViolation | None = None
        try:
            check_worktree_unchanged(step.run.vcs, before)
        except InvariantViolation as found:
            violation = found
        # The copy and the second copy of a red proof go; the verdict report, the event stream and the other files
        # of the verifier stay as evidence of the run.
        evidence.mkdir(parents=True, exist_ok=True)
        for entry in scratch.iterdir():
            if entry not in (copy, red_proof_copy(copy)):
                shutil.move(str(entry), evidence / entry.name)
    finally:
        step.run.vcs.stop_file_watcher(scratch / VERIFY_COPY_NAME)
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.rmtree(own_scratch, ignore_errors=True)
    report_path = evidence / report_path.name
    stream = result.event_stream
    if stream.is_relative_to(scratch):
        stream = evidence / stream.relative_to(scratch)
    _journal_idle(step.journal, result, ticket.id, ROLE_VERIFIER, round_number)
    _journal_timeout(step.journal, result, step.stack, ticket.id, ROLE_VERIFIER, round_number)
    step.journal.append(
        "verify-result", ticket=ticket.id, round=round_number, exit_status=result.exit_status, end_state=result.end_state,
        session_id=result.session_id, event_stream=str(stream), **_refused_fields(result),
        **_totals_fields(step.run.adapter, stream),
    )
    if violation is not None:
        _journal_violation(step.journal, ticket.id, round_number, violation)
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
        raise StepFailed(_ended_reason(result, "the verifier", step.stack))
    try:
        report = read_verifier_report(report_path, mode)
    except ReportError as error:
        step.journal.append(
            "verify-report", ticket=ticket.id, round=round_number, valid=False, path=str(report_path), reason=str(error),
            **source,
        )
        raise StepFailed(str(error)) from None
    step.journal.append(
        "verify-report", ticket=ticket.id, round=round_number, valid=True, path=str(report_path), reason=None, **source,
        **_lenient_fields(report),
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


def _coordinator_of_step(step: _Step) -> list[tuple[str, tuple[str, ...]]]:
    """Each commit of the coordinator, with its paths, that the branch of the step holds and the run branch does not (#225).

    The commits are those of the ticket whose branch the step verifies: the ticket of the step, or for a chain the
    ticket at the tip of the chain. A chain step of a stack that does not hold that ticket names none.
    """
    owner = step.anchor or step.ticket
    if step.segments is not None and owner.id not in {segment.ticket for segment in step.segments}:
        return []
    own = step_scans.step_events(step.journal.events, owner.id)
    branch, run_branch = f"refs/heads/{step.branch}", f"refs/heads/{step.run.workflow.run_branch}"
    return [
        (commit, paths) for commit, paths in coordinator_commits(step.run.vcs, step.run.repo, own)
        if holds_commit(step.run.vcs, step.run.repo, commit, branch) and not holds_commit(step.run.vcs, step.run.repo, commit, run_branch)
    ]


def _advance_run_branch(step: _Step, verified: str) -> str | None:
    """Move the run branch to the commit that the verifier accepted, by fast-forward only.

    Return None when it moved, or the reason the step fails.
    """
    run_branch = step.run.workflow.run_branch
    run_tip = _check_run_branch(step.journal, step.run.vcs, step.run.repo, run_branch, step.ticket.id)
    with vcs_errors():
        # The port moves the branch only while it is at `run_tip`, the tip that the check read.
        moved = step.run.vcs.fast_forward_branch(step.run.repo, run_branch, verified, run_tip)
    if not moved:
        return (
            f"run branch {run_branch} cannot fast-forward to {step.branch}: "
            f"it holds a commit that {step.branch} does not hold"
        )
    step.journal.append(
        "run-branch-advance", ticket=step.ticket.id, run_branch=run_branch, from_commit=run_tip, to_commit=verified
    )
    return None


def _retry_ticket(step: _Step, plan: RetryPlan, failure: str, start_ref: str) -> str | None:
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
    - The fix-up phase (the agent check stopped the step before the fix-up session) runs that fix-up again, with the
      gates. On the tip of the run branch, the scoped verifier starts. If the run branch moved, the engine rebases and
      starts a full verifier at the round of the plan.

    In `economy` mode the step goes on as an open step does after a stop, at
    the specialist or at the check of its report (see `_build_ticket`). The phase `chain-fixup` builds nothing: it writes
    `retry` and returns, and the chain verification runs the stopped fix-up again (see `chain.ChainRun`).

    The phase `specialist` of `assure` mode goes on in `_build_ticket`, as in `economy` mode: the specialist continues
    in its worktree (see `retry._stopped_before_specialist`, and #284 in `retry.retry_plan`).

    The phase `fixup` of `assure` mode runs the stopped fix-up again (see `retry._stopped_before_fixup`), with the same rejected
    commit and the same findings, then goes on to the gates and the scoped verifier of that round.

    A plan with `restart` starts the ticket again from its base (see `_restart_step`).

    A plan with a commit of the coordinator (#225) records the commit and its paths in `retry`. In `assure` mode a
    retry at the verifier or at the scoped verifier then goes on at the gates, because the tip of the branch is a new
    commit: the gates run on it before a verifier starts. The phase `chain` builds nothing, as the phase `chain-fixup`
    does not: the verification of the chain starts again after the `retry` event, on the new tip. After a report of the
    specialist that says partial or blocked (#264), the gates of round 0 go on in `_build_ticket`, which then checks that
    report as if it said committed (see `_coordinator_completes`). After such a report of a fix-up specialist, the gates
    of the fix-up and its scoped verifier go on here, as after a report that says committed.
    """
    workflow, ticket, vcs = step.run.workflow, step.ticket, step.run.vcs
    coordinator = plan.coordinator_fields
    if plan.restart:
        return _restart_step(step, failure, start_ref)
    if plan.phase in (RETRY_PHASE_CHAIN_FIXUP, RETRY_PHASE_CHAIN):
        # No work of this step is left: the chain verification runs the stopped fix-up again, or starts again (see `chain.ChainRun`).
        step.journal.append(
            "retry", ticket=ticket.id, phase=plan.phase, round=plan.round, reason=failure, revived=list(plan.revive), **coordinator
        )
        return None
    if workflow.mode == domain.ECONOMY or plan.phase == RETRY_PHASE_SPECIALIST:
        step.journal.append("retry", ticket=ticket.id, phase=plan.phase, round=plan.round, reason=failure, **coordinator)
        return _build_ticket(step.run, step.journal, ticket, start_ref, list(plan.prior), retried=failure)
    if plan.phase == RETRY_PHASE_GATES and plan.rejected is None:
        step.journal.append("retry", ticket=ticket.id, phase=RETRY_PHASE_GATES, round=plan.round, reason=failure, **coordinator)
        return _build_ticket(step.run, step.journal, ticket, start_ref, list(plan.prior))
    branch_ref = f"refs/heads/{step.branch}"
    on_tip = holds_commit(vcs, step.run.repo, branch_tip(vcs, step.run.repo, workflow.run_branch), branch_ref)
    fixup_held = plan.rejected is not None and holds_commit(vcs, step.run.repo, plan.rejected, branch_ref)
    goes_on = plan.phase if on_tip and (plan.rejected is None or fixup_held) else RETRY_PHASE_GATES
    if plan.phase == RETRY_PHASE_FIXUP and fixup_held:
        goes_on = RETRY_PHASE_FIXUP  # the fix-up of the round runs first, whether the branch holds the run branch tip or not
    if plan.coordinator is not None and goes_on in (RETRY_PHASE_VERIFIER, RETRY_PHASE_SCOPED_VERIFIER):
        goes_on = RETRY_PHASE_GATES  # the commit of the coordinator is a new tip: the gates run on it before a verifier
    step.journal.append("retry", ticket=ticket.id, phase=goes_on, round=plan.round, reason=failure, **coordinator)
    made = _prepare_worktree(vcs, step.run.repo, step.worktree, step.branch, start_ref, step.run.hooks_dir(ticket.id))
    try:
        _prepare_ticket_worktree(step, made)
        if plan.phase == RETRY_PHASE_FIXUP:
            # The branch holds no fix-up commit: the stop came before the fix-up session. Run that fix-up again.
            if plan.rejected is None or not fixup_held:
                return f"the branch {step.branch} no longer holds the rejected commit of round {plan.round}, so its fix-up cannot run again"
            # On the rejected commit, with the findings of the REJECT: not a new round. Red gates after it can start the next round.
            round_number, findings_text = _fixup_round(step, plan.round, plan.rejected, list(plan.findings))
            if on_tip:
                return _advance_run_branch(step, _accepted_commit(step, round_number, (plan.rejected, findings_text)))
            # The run branch moved after the stop: rebase the branch with its fix-up, then a full verifier, as for any other phase.
            reason = _rebase_onto_run_branch(step)
            if reason:
                return reason
            return _advance_run_branch(step, _accepted_commit(step, round_number))
        if plan.rejected is not None and fixup_held:
            findings_text = _findings_text(list(plan.findings))
            _checked_fixup_brief(step, plan.round, plan.rejected, findings_text)
            if on_tip:
                # Red gates of the fix-up start the next round while rounds remain (#277).
                round_number = _fixup_gates(step, plan.round) if goes_on == RETRY_PHASE_GATES else plan.round
                return _advance_run_branch(step, _accepted_commit(step, round_number, (plan.rejected, findings_text)))
        elif goes_on == RETRY_PHASE_VERIFIER:
            return _advance_run_branch(step, _accepted_commit(step, plan.round))
        reason = _rebase_onto_run_branch(step)
        if reason:
            return reason
        return _advance_run_branch(step, _accepted_commit(step, plan.round))
    except StepFailed as failed:
        return str(failed)


def _restart_step(step: _Step, failure: str, start_ref: str) -> str | None:
    """Start the ticket of a retry that changed its text again from its base (#204). Return None when it is built, or the reason it failed.

    The engine keeps the old branch under the name `<branch>-failed-<n>`, with
    the first `n` that is free, so the coordinator can read the old work. It
    removes the old worktree, and the old reports and evidence of the ticket
    go to the directories `failed-<n>`, so a report of the old step cannot pass
    for a report of the new one. The branch and the worktree of the ticket are
    then made again at `start_ref`, the base of the step as the first
    `step-start` took it, and the engine builds the ticket as a new step: a new
    specialist with the new text, the gates and the verifier for the mode. The
    rounds of the old step do not count (see `step_scans.step_events`). The `retry` event
    has `restart` and `failed_branch`, and a new `step-start` follows it.
    """
    ticket, vcs, repo = step.ticket, step.run.vcs, step.run.repo
    branch = step.branch
    with vcs_errors():
        number = 1
        while vcs.branch_exists(repo, f"{branch}-failed-{number}"):
            number += 1
        failed_branch = f"{branch}-failed-{number}"
        step.journal.append(
            "retry", ticket=ticket.id, phase=RETRY_PHASE_SPECIALIST, round=0, reason=failure, restart=True, failed_branch=failed_branch
        )
        if step.worktree.is_dir():
            vcs.remove_worktree(repo, step.worktree)
        shutil.rmtree(step.run.hooks_dir(ticket.id), ignore_errors=True)
        vcs.prune_worktrees(repo)
        if vcs.branch_exists(repo, branch):
            # The old branch gets its new name first, so no commit is ever without a branch.
            vcs.create_branch(repo, failed_branch, branch)
            vcs.discard_branch(repo, branch)
    _archive_old_step(step.run_dir, ticket.id, number)
    return _build_ticket(step.run, step.journal, ticket, start_ref)


def _archive_old_step(run_dir: Path, ticket_id: str, number: int) -> None:
    """Move the reports, the findings and the verifier evidence of the old step of a ticket to directories named `failed-<n>`."""
    for kind, patterns in (("reports", (f"{ticket_id}.specialist.json", f"{ticket_id}.fixup-*.specialist.json")), ("findings", (f"{ticket_id}.fixup-*.md",))):
        for pattern in patterns:
            for path in sorted((run_dir / kind).glob(pattern)):
                target = run_dir / kind / f"failed-{number}"
                target.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), target / path.name)
    evidence = run_dir / "verifier" / ticket_id
    if evidence.is_dir():
        shutil.move(str(evidence), run_dir / "verifier" / f"{ticket_id}-failed-{number}")


def _chain_steps() -> chain.ChainSteps:
    """The steps of the engine that the verification of an `economy` chain drives (see `chain.ChainSteps`)."""
    return chain.ChainSteps(
        make_step=_Step, run_gate=run_gate_with_output, copy_gate_logs=copy_gate_logs, scratch_directory=_scratch_directory,
        red_gates_text=red_gates_text, run_fixup=_run_fixup, checked_fixup_brief=_checked_fixup_brief, findings_text=_findings_text,
        verify_round=_verify_round, check_run_branch=_check_run_branch, check_verifier_stop=_check_verifier_stop,
        advance_run_branch=_advance_run_branch, ends_the_run=_ends_the_run, halt_reason=_halt_reason,
    )


def _refused_fields(result: AdapterResult) -> dict[str, object]:
    """The `refused` field of a result event: each tool that a permission mode refused, with the mode. Empty when none."""
    if not result.refused:
        return {}
    return {"refused": [{"tool": tool, "mode": mode} for tool, mode in result.refused]}

