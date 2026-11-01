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
commit). After `FIXUP_ROUND_LIMIT` rounds the step fails and
the step fails. The run branch moves only on ACCEPT.

The engine checks that. The tip that the journal says is the `to_commit` of the newest `run-branch-advance`, or the
`base_commit` of `run-start`. Before each rebase onto the run branch, before each fast-forward, before the chain
verification and at each resume, the engine compares it with the real tip (see `_check_run_branch`). A difference
writes `run-branch-moved` and halts the run as an invariant violation does: the run cannot be resumed.

The engine records the HEAD and the status of the worktree of the ticket before
and after each verifier run (see `guards.py`). A difference is an invariant
violation: the journal records it with the changes, and the run halts. Before
each verifier run, the engine also makes the proof copy beside the temporary
copy: the tree of the copy with no `.git` entry, where the verifier breaks a
check for a red proof. The engine removes both copies after every verifier run.

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
only closes it. In `assure` mode, a step whose newest verdict is ACCEPT, and whose accepted commit the run branch
holds, is closed the same way: the process stopped after it moved the run branch and before it wrote
`run-branch-advance`, so the resume writes that event with `recovered: true` (see `_recover_advance`). Any other open step restarts in its own worktree, which the
engine makes again if it is gone. A specialist whose report the journal shows as
valid is not spawned again. A verifier run that ended with no verdict does not
count against the fix-up limit: the resume runs that round again.

A resume can change the plan of the run: add tickets after the built part, and
change or remove tickets that have not started. The rules are in
`domain.check_plan_change`, and `_review` applies them to the newest plan of the
run, from the journal. A refused change changes nothing. An accepted change
writes `plan-change`, also goes on with a run that ended, and gives the brief of
each later ticket the section "Plan change" (see `_plan_change_note`).

A resume with `retry` goes on with one failed step, also in a run that ended
(see `_retry_plan`). It uses the worktree and the commits of the step again,
and goes on at the first phase that did not complete. In `assure` mode it
spawns no specialist, and that phase is the gates, the verifier, or the scoped
verifier. When the run branch moved after the step failed, the retry rebases,
runs the gates, and verifies the whole branch. In `economy` mode the phase is
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

A run that ends with a failed step or a skipped ticket ends with the result
`failed`.

A run can pause. `delegate pause` writes a request file in the directory of the
run (see `pause.py`), and the engine reads it at each step boundary: before a
step starts, before a gate runs, and before a verifier starts (see
`_pause_point`). It stops there and does not cut off an agent. The journal
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

import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
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
from . import domain
from .agents import agent_names, check_agent, denied_agents, recorded_agents, seal_agents
from .brief import (
    BriefError, ChainSegment, chain_brief, continuation_brief, finding_labels_section, fixup_brief, green_gates_text,
    notes_section, plan_change_section, plan_change_text, red_proof_copy, report_correction_brief, specialist_brief,
    verifier_run_brief, verifier_run_sections,
)
from .cleanup import TicketTree, clean_tickets, remove_empty_directories
from .events import RUN_START, SCHEMA_VERSION
from .guards import (
    GuardError, InvariantViolation, changed_paths, check_worktree_unchanged, hotspot_matches, install_push_guard,
    snapshot_worktree, WorktreeSnapshot,
)
from .journal import Journal, JournalError, abandoned_end, plan_ids, read_events
from .lock import LockError, LockHolder, check_free, run_lock
from .probe import PROBE_TIER, DENIED, probe_guard, refusal, stream_path
from .reports import ReportError, SpecialistReport, VerifierReport, map_findings, read_specialist_report, read_verifier_report, verdict_in_text
from .workflow import Stack, Ticket, Workflow, plan_order

# The journal keeps this many characters of the end of a gate's output.
GATE_OUTPUT_TAIL_CHARS: int = 4000

# The directory of the run that holds the log file of each gate run, in one directory for each ticket.
GATE_LOG_DIRECTORY: str = "gates"

# The file of the run directory that holds the traceback of a crash: this prefix, the `seq` of the `step-end`, `.txt`.
CRASH_FILE_PREFIX: str = "crash-"

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

# The `tree` of a `prepare-start` and a `prepare-result`: the worktree of a ticket, or the temporary copy of a verifier (#155).
TREE_WORKTREE: str = "worktree"
TREE_VERIFY_COPY: str = "verify-copy"
# The tree of an `agents-changed` event: the seal of the agent, outside each tree (#217).
TREE_SEAL: str = "seal"

# The `trigger` of a `fixup-start` of a chain: red gates on the tip of the chain, or a REJECT.
TRIGGER_GATES_RED: str = "gates-red"
TRIGGER_REJECT: str = "reject"

# The `trigger` of a `continuation` whose report check failed on a branch with work: it asks only for a corrected report.
# The report file is there but not valid, or there is no report file.
TRIGGER_REPORT_INVALID: str = "report-invalid"
TRIGGER_REPORT_MISSING: str = "report-missing"

# The `trigger` of a `continuation` whose worktree holds a change that is not committed when a gate must run (#176).
TRIGGER_UNCOMMITTED: str = "uncommitted"

# The `phase` of a `retry` event: the first phase of the failed step that did not complete.
# `specialist` is only in `economy` mode, where the specialist of the step continues.
RETRY_SPECIALIST: str = "specialist"
RETRY_GATES: str = "gates"
RETRY_VERIFIER: str = "verifier"
RETRY_SCOPED_VERIFIER: str = "scoped-verifier"
# The agent check stopped a fix-up session (#209): `assure` mode runs the fix-up of the round again, `economy` mode the chain fix-up.
RETRY_FIXUP: str = "fixup"
RETRY_CHAIN_FIXUP: str = "chain-fixup"

# The file in the directory of a run that asks the run to pause at its next step boundary (see `delegate.run.pause`).
PAUSE_REQUEST_NAME: str = "pause-request"

# The `before` of a `paused` event: the work that the pause did not start.
PAUSED_BEFORE_STEP: str = "step"
PAUSED_BEFORE_GATE: str = "gate"
PAUSED_BEFORE_VERIFIER: str = "verifier"


class EngineError(Exception):
    """The run cannot start or go on. The message names the input at fault."""


class RunInterrupted(Exception):
    """Ctrl-C (SIGINT) stopped the run. The lock of the run branch is released.

    `journal` is the path of the journal, which holds `interrupted` as its last event, so a resume goes on. It is None
    when the run stopped before it wrote its journal: then there is nothing to resume.
    """

    def __init__(self, run_id: str, journal: Path | None) -> None:
        super().__init__(f"run {run_id} was interrupted")
        self.run_id, self.journal = run_id, journal


class _StepFailed(Exception):
    """A fix-up round or a verifier run cannot go on. The message is the reason the step fails."""


class _Paused(Exception):
    """A pause request stopped the run at a step boundary. The journal holds `paused`. The message tells where."""


class _Halted(Exception):
    """The harness failed: `Adapter.run` raised an `AdapterError`. The journal holds `halted`. The message is the error."""

    def __init__(self, ticket: str, role: str, round_number: int, message: str) -> None:
        super().__init__(message)
        self.ticket, self.role, self.round_number = ticket, role, round_number


class _RunBranchMoved(Exception):
    """The run branch is not where the journal says. The journal holds `run-branch-moved`. The message is the reason the run halts."""


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
    # Where a pause request stopped the run, for example `before the verifier of ticket a`, or None when it did not pause.
    paused: str | None = None
    # The message of the `AdapterError` that halted the run, or None when it did not halt. `halted_at` says where.
    halted: str | None = None
    halted_at: str | None = None
    # The note of a resume that recorded the hashes of the agent files because its journal holds no record, or None (#205).
    agents_note: str | None = None

    @property
    def ok(self) -> bool:
        # A skipped ticket is work that the run did not build, so a run with one is not a success.
        return not self.failed and not self.skipped


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


def _worktree_environment(vcs: VersionControl, worktree: Path) -> dict[str, str]:
    """The environment entries that carry the settings of the worktree (the push guard, the file watcher off) to a process of its ticket (#221)."""
    with _vcs_errors():
        return vcs.worktree_environment(worktree)


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


def _halt_reason(error: Exception) -> str:
    """The reason a step fails when `error` ends it: an invariant violation and a moved run branch halt the run, any other error is a crash."""
    if isinstance(error, InvariantViolation):
        return f"invariant violation: {error}"
    if isinstance(error, _RunBranchMoved):
        return f"run branch moved: {error}"
    return f"crashed: {type(error).__name__}: {error}"


def _crash_fields(journal: Journal, error: Exception) -> dict[str, object]:
    """The `traceback` field of the `step-end` that records a crash, or no field for any other failure.

    The traceback goes to `crash-<seq>.txt` in the run directory, where `<seq>` is the `seq` that the next event gets,
    so it is the `seq` of the `step-end` (#194). A file that cannot be written does not hide the crash: the event then
    has no `traceback`.
    """
    if isinstance(error, (InvariantViolation, _RunBranchMoved)):
        return {}
    trace = journal.path.parent / f"{CRASH_FILE_PREFIX}{journal.next_seq}.txt"
    try:
        trace.write_text("".join(format_exception(error)))
    except OSError:
        return {}
    return {"traceback": str(trace)}


def _expected_tip(events: list[dict[str, object]]) -> str | None:
    """Where the journal says the run branch is: the `to_commit` of the newest `run-branch-advance`, or the `base_commit` of `run-start`.

    Return None for the journal of an engine that did not record `base_commit` (1.2.0 and before) and has no advance yet.
    """
    for event in reversed(events):
        if event["event"] == "run-branch-advance":
            return str(event["to_commit"])
    base = events[0].get("base_commit") if events else None
    return None if base is None else str(base)


def _moved_text(run_branch: str, expected: str, found: str) -> str:
    return (
        f"the run branch {run_branch} is at {found}, but the journal says it is at {expected}; "
        "a commit that the engine did not advance to is on the run branch"
    )


def _check_run_branch(journal: Journal, vcs: VersionControl, repo: Path, run_branch: str, ticket: str) -> None:
    """Halt the run when the run branch is not at the tip that the journal says (#186).

    The run branch moves only when the engine advances it after an ACCEPT. A difference means that a commit landed with
    no verification. The engine writes `run-branch-moved` (the expected tip and the tip that it found) and raises
    `_RunBranchMoved`, which halts the run as an invariant violation does. Do nothing when the tips agree.
    """
    expected = _expected_tip(read_events(journal.path))
    if expected is None:
        return
    found = _tip(vcs, repo, run_branch)
    if found == expected:
        return
    journal.append("run-branch-moved", ticket=ticket, run_branch=run_branch, expected=expected, found=found)
    raise _RunBranchMoved(_moved_text(run_branch, expected, found))


def _accepted_unrecorded(events: list[dict[str, object]]) -> list[str]:
    """The commits that a verifier accepted after the newest `run-branch-advance`: the engine can have moved the run branch to one.

    The engine moves the run branch first and writes `run-branch-advance` after it, so a stop between the two leaves a tip
    that the journal does not record yet. A resume closes such a step (see `_recover_advance`).
    """
    last = max((index for index, e in enumerate(events) if e["event"] == "run-branch-advance"), default=-1)
    accepted: list[str] = []
    commit: str | None = None
    for event in events[last + 1 :]:
        if event["event"] == "verify-start":
            commit = str(event["commit"])
        elif event["event"] == "verdict" and commit is not None and domain.is_accepted(str(event["verdict"])):
            accepted.append(commit)
    return accepted


def _check_run_branch_at_resume(
    journal: Journal, events: list[dict[str, object]], vcs: VersionControl, repo: Path, run_branch: str, built: list[str],
    failed: list[str], skipped: dict[str, str],
) -> None:
    """Refuse to resume a run whose run branch is not where the journal says (#186).

    The tip is also right when it is a commit that a verifier accepted and that the engine moved the run branch to just
    before it stopped, with the new tip holding the tip of the journal. On a difference the engine writes
    `run-branch-moved` and `run-end` with the result `failed`, and raises EngineError.
    """
    expected = _expected_tip(events)
    if expected is None:
        return
    found = _tip(vcs, repo, run_branch)
    if found == expected or (found in _accepted_unrecorded(events) and _holds(vcs, repo, expected, found)):
        return
    journal.append("run-branch-moved", ticket=None, run_branch=run_branch, expected=expected, found=found)
    journal.append("run-end", result=FAILED, built=built, failed=failed, skipped=list(skipped))
    raise EngineError(f"run cannot be resumed: {_moved_text(run_branch, expected, found)}; a halted run cannot be resumed: start a new run")


def _check_branches(workflow: Workflow, repo: Path, vcs: VersionControl) -> None:
    """Refuse a new run whose base branch is missing, or whose run branch or ticket branch is taken. Create nothing."""
    with _vcs_errors():
        base_exists = vcs.branch_exists(repo, workflow.base_branch)
        run_branch_taken = vcs.branch_exists(repo, workflow.run_branch)
    if not base_exists:
        raise EngineError(f"base-branch {workflow.base_branch!r} is not a branch of the repository {repo}")
    # A taken ticket branch is reported before a taken run branch: an earlier run leaves both, and the ticket names the step.
    for ticket in workflow.tickets:
        branch = _ticket_branch(workflow, ticket)
        with _vcs_errors():
            taken = vcs.branch_exists(repo, branch)
        if taken:
            raise EngineError(f"branch {branch!r} for ticket {ticket.id!r} exists already; delete it or choose another run-branch")
    if run_branch_taken:
        raise EngineError(
            f"run-branch {workflow.run_branch!r} exists already in the repository {repo}; an earlier run can leave it. "
            "Delete it or choose another run-branch"
        )


def _ticket_branch(workflow: Workflow, ticket: Ticket) -> str:
    return f"{workflow.run_branch}-{ticket.id}"


# The environment variable of a host that sets the state root of every repository. The default root is outside the git
# directory too (#200), because Claude Code in `dontAsk` mode refuses `Edit` and `Write` on a path with a `.git`
# component, also with an allow rule (#97), and the Vite dev server refuses to serve one.
STATE_ROOT_VARIABLE: str = "DELEGATE_STATE_ROOT"


class StateRootError(EngineError):
    """`DELEGATE_STATE_ROOT` has a value that the engine cannot use."""


# The file in `<git common dir>/delegate` that holds the absolute path of the state directory, one line, when the
# engine keeps its state elsewhere (#199). A command in a shell without `DELEGATE_STATE_ROOT` finds the state with it.
STATE_ROOT_FILE: str = "state-root"

# The pairs of directories that a warning named already, so a command that finds the state directory more than once
# prints one warning line.
_WARNED: set[tuple[Path, Path]] = set()


def _recorded_root(shared: Path) -> Path | None:
    """The state directory that `<git common dir>/delegate/state-root` names, or None when the file does not exist."""
    path = shared / "delegate" / STATE_ROOT_FILE
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StateRootError(f"the file {path} cannot be read: {error}") from None
    recorded = text.strip()
    if not recorded or not Path(recorded).is_absolute():
        raise StateRootError(f"the file {path} holds {recorded!r}, which is not an absolute path; remove the file or write the path of the state directory")
    return Path(recorded)


def default_state_root() -> Path:
    """The state root of a host that sets no `DELEGATE_STATE_ROOT`: `$XDG_STATE_HOME/delegate`, or `~/.local/state/delegate`.

    An `XDG_STATE_HOME` that is empty or not an absolute path counts as not set, as the XDG specification says.
    """
    base = os.environ.get("XDG_STATE_HOME", "")
    if base and Path(base).is_absolute():
        return Path(base) / "delegate"
    return Path.home() / ".local" / "state" / "delegate"


def _repository_directory(shared: Path, root: Path) -> Path:
    """`<root>/<name>-<key>`, the state directory of the repository whose git common directory is `shared`.

    `<name>` is the name of the directory that holds `shared` (`.git` is in the top level of the main checkout), or its
    own name when it is not called `.git` (a bare repository or a separate git directory), and `<key>` is the first 12
    hex digits of the SHA-256 of its absolute path.
    """
    name = shared.parent.name if shared.name == ".git" else shared.name
    key = hashlib.sha256(str(shared).encode()).hexdigest()[:12]
    return root / f"{name}-{key}"


def state_directory(repo: Path, vcs: VersionControl, run_id: str | None = None) -> tuple[Path, Path]:
    """The top level of the repository and the directory where the engine keeps its state.

    By default the state is outside the git directory (#200), so no path that
    the engine gives to an agent or a gate has a `.git` component. The
    directory is the first of these. When the host sets `DELEGATE_STATE_ROOT`,
    it is `<root>/<name>-<key>`. Else it is the path in
    `<git common dir>/delegate/state-root`, when that file exists. Else it is
    `<root>/<name>-<key>` with the root `default_state_root()`. `<name>` and
    `<key>` come from the git common directory, which every worktree of a
    repository shares: `<name>` is the name of the directory that holds it
    (`.git` is in the top level of the main checkout), or its own name when it
    is not called `.git` (a bare repository or a separate git directory), and
    `<key>` is the first 12 hex digits of the SHA-256 of its absolute path. So
    one root serves every repository of a host, each repository gets its own
    directory, and every worktree of a repository finds the same one.

    A run that started under `<git common dir>/delegate` before #200 stays
    there. With `run_id`, the id of a run that is in that directory and not in
    the directory above, the result is `<git common dir>/delegate`.

    When the variable is set, and `state-root` names another directory, the
    variable wins and one warning line on stderr names both directories.

    Raise EngineError when `repo` is not inside a repository, and
    StateRootError when the root, or the path in `state-root`, is not an
    absolute path.
    """
    top, directories = _state_directories(repo, vcs)
    if run_id is not None and not (directories[0] / "runs" / run_id).is_dir():
        for other in directories[1:]:
            if (other / "runs" / run_id).is_dir():
                return top, other
    return top, directories[0]


def state_directories(repo: Path, vcs: VersionControl) -> tuple[Path, list[Path]]:
    """The top level of the repository and each state directory that can hold a run, the one of `state_directory` first.

    The second directory is `<git common dir>/delegate`, where the runs that started before #200 are. It is in the list
    only when it holds a directory `runs`. Raise the errors of `state_directory`.
    """
    top, directories = _state_directories(repo, vcs)
    return top, [directories[0], *(other for other in directories[1:] if (other / "runs").is_dir())]


def _state_directories(repo: Path, vcs: VersionControl) -> tuple[Path, list[Path]]:
    """The top level, the state directory of a new run, and `<git common dir>/delegate` when it is another directory."""
    with _vcs_errors():
        top = vcs.repository_root(repo)
        shared = vcs.shared_data_directory(top)
    legacy = shared / "delegate"
    recorded = _recorded_root(shared)
    root = os.environ.get(STATE_ROOT_VARIABLE)
    if not root:
        chosen = recorded if recorded is not None else _repository_directory(shared, default_state_root())
    else:
        if not Path(root).is_absolute():
            raise StateRootError(f"{STATE_ROOT_VARIABLE} is {root!r}, which is not an absolute path")
        chosen = _repository_directory(shared, Path(root))
        if recorded is not None and recorded != chosen and (chosen, recorded) not in _WARNED:
            _WARNED.add((chosen, recorded))
            print(
                f"delegate: {STATE_ROOT_VARIABLE} names the state directory {chosen}, but {legacy / STATE_ROOT_FILE} names {recorded}; "
                f"using {chosen}",
                file=sys.stderr,
            )
    return top, [chosen] if chosen == legacy else [chosen, legacy]


def record_state_root(repo: Path, vcs: VersionControl, state_dir: Path) -> None:
    """Write the path of `state_dir` to `<git common dir>/delegate/state-root` when the state is not in `<git common dir>/delegate`.

    The file is the one line that a command needs to find the state without `DELEGATE_STATE_ROOT`. It is written
    to a private file first, and a rename puts it in place, so a command never reads half a path.
    """
    with _vcs_errors():
        shared_delegate = vcs.shared_data_directory(vcs.repository_root(repo)) / "delegate"
    if state_dir == shared_delegate:
        return
    path = shared_delegate / STATE_ROOT_FILE
    line = f"{state_dir}\n"
    try:
        if path.exists() and path.read_text() == line:
            return
        shared_delegate.mkdir(parents=True, exist_ok=True)
        private = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        private.write_text(line)
        os.replace(private, path)
    except OSError as error:
        raise EngineError(f"the engine cannot write {path}: {error}") from None


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
    _, state_dir = state_directory(repo, vcs, run_id)
    try:
        events = read_events(state_dir / "runs" / run_id / "journal.jsonl", torn_tail=True)
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


def restore_progress(events: list[dict[str, object]], run_id: str, retrying: bool = False) -> _Progress:
    """Rebuild the progress of a run from its journal. Raise EngineError when the run has ended, unless `retrying`.

    A `run-end` before the newest `plan-change` does not end the run: a plan change goes on with a run that ended.
    """
    progress = _Progress(events=events)
    started: str | None = None
    last_change = max((index for index, event in enumerate(events) if event["event"] == "plan-change"), default=-1)
    for index, event in enumerate(events):
        kind, ticket = event["event"], str(event.get("ticket"))
        if kind == "run-end" and not retrying and index > last_change:
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
                # A null reason gives a short text, not the text None.
                progress.failures[ticket] = str(event.get("reason") or "the step failed")
            if started == ticket:
                started = None
        elif kind == "skip":
            progress.skipped[ticket] = str(event.get("reason") or "the journal gives no reason")
    progress.open_ticket = started
    return progress


def _check_same_run(workflow: Workflow, workflow_path: Path, run_start: dict[str, object], run_id: str) -> None:
    """Refuse a resume when the workflow is not the one that the run started from. The message names the field.

    The tickets can differ: the rules of a plan change decide (see `_review`).
    """
    now: dict[str, object] = {
        "mode": workflow.mode, "adapter": workflow.adapter, "base_branch": workflow.base_branch, "run_branch": workflow.run_branch,
    }
    for name, value in now.items():
        if run_start[name] != value:
            raise EngineError(f"workflow {workflow_path} differs from run {run_id}: {name} was {run_start[name]!r}, is now {value!r}")


def _plan_of(workflow: Workflow) -> list[domain.PlanTicket]:
    """The plan of a workflow: each ticket in plan order, with its stack, its blockers and the SHA-256 of its text."""
    return [
        domain.PlanTicket(t.id, t.stack, tuple(t.blocked_by), hashlib.sha256(t.text.encode()).hexdigest())
        for t in plan_order(workflow)
    ]


def _plan_record(plan: list[domain.PlanTicket]) -> list[dict[str, object]]:
    """The plan as the journal records it, in the field `plan` of `run-start` and of `plan-change`."""
    return [{"id": t.id, "stack": t.stack, "blocked_by": list(t.blocked_by), "text_sha256": t.text_sha256} for t in plan]


def _recorded_plan(events: list[dict[str, object]]) -> list[domain.PlanTicket] | None:
    """The newest plan of a run: the plan of its newest `plan-change`, or else of `run-start`.

    None when the journal records no plan: an earlier version of the engine wrote only the ticket ids.
    """
    records = [event["plan"] for event in events if event["event"] == "plan-change"] or [events[0].get("plan")]
    if records[-1] is None:
        return None
    return [
        domain.PlanTicket(str(r["id"]), str(r["stack"]), tuple(str(b) for b in r["blocked_by"]), str(r["text_sha256"]))  # type: ignore[index, union-attr]
        for r in records[-1]  # type: ignore[union-attr]
    ]


@dataclass(frozen=True)
class PlanReview:
    """What a resume does with the plan of a run: the old and the new order, and the verdict of each rule of a plan change.

    `check` is None when the tickets of the workflow are the newest plan of the
    run. `missing` holds the later tickets that `--reviewed` does not cover.
    `texts` holds the text of each added or changed ticket. `other` holds the
    problems that are not a rule of the plan: an unknown id in `--reviewed`,
    the branch of a new ticket that exists already, and a run that an
    invariant violation halted.
    """

    run_id: str
    mode: str
    old_order: list[str]
    new_order: list[str]
    check: domain.PlanCheck | None
    missing: list[str] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)
    other: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.check is not None

    def _rule_8(self) -> list[str]:
        if not self.missing:
            return []
        names = ", ".join(repr(ticket) for ticket in self.missing)
        return [
            f"the plan change needs a review of the later tickets {names}: read each one against the added and changed tickets, "
            f"change its text or its blocked-by where a precondition changed, then give --reviewed {','.join(self.check.later)} "  # type: ignore[union-attr]
            "or --reviewed all"
        ]

    def problems(self) -> list[str]:
        """Each message that refuses the resume, in the order of the rules."""
        if self.check is None:
            return list(self.other)
        problems = [message for rule in sorted(self.check.problems) for message in self.check.problems[rule]]
        return problems + self.other + self._rule_8()

    def verdicts(self) -> list[tuple[int, str, str]]:
        """The verdict of each rule of a plan change: its number, `ok` or `refused`, and the detail.

        The problems in `other` are not rules of the plan: `problems` gives them.
        """
        check = self.check
        assert check is not None
        later = ", ".join(check.later)
        notes = {
            1: f"the built part is {', '.join(check.fixed) or 'empty'}",
            2: f"added: {', '.join(check.added) or 'none'}",
            3: "changed: " + (", ".join(f"{t} ({', '.join(f)})" for t, f in check.changed.items()) or "none"),
            4: "no new ticket comes before a ticket that has started",
            5: "each new ticket gets its own gates and verifier"
            + (": the verification of the chain covers each built ticket whose work the run branch does not hold"
               if self.mode == domain.ECONOMY else ""),
            6: "the resume writes plan-change",
            7: f"removed: {', '.join(check.removed) or 'none'}",
            8: f"reviewed: {later}" if later else "no later ticket",
            9: f"the briefs of {later} get the section" if later else "no brief gets the section",
            10: "text of the retried ticket changes: " + ", ".join(check.restarted) if check.restarted else "no retried ticket changes its text",
        }
        found = {rule: list(messages) for rule, messages in check.problems.items()}
        if self.missing:
            found[8] = [f"needs --reviewed for {', '.join(self.missing)}"]
        return [
            (rule, "refused" if rule in found else "ok", "; ".join(found[rule]) if rule in found else notes[rule])
            for rule in domain.PLAN_RULES
        ]


def _violation_text(violation: dict[str, object]) -> str:
    """The text that refuses a resume of a run that an invariant violation halted: the ticket, the worktree and each change."""
    changes = "; ".join(str(change) for change in violation["changes"])  # type: ignore[union-attr]
    return (
        f"an invariant violation halted the run at ticket {violation['ticket']!r}: "
        f"the verifier changed the real worktree {violation['worktree']}: {changes}; "
        "a halted run cannot be resumed: start a new run"
    )


def _review(
    workflow: Workflow, workflow_path: Path, events: list[dict[str, object]], run_id: str, reviewed: tuple[str, ...],
    branch_exists: Callable[[str], bool], retry: str | None = None,
) -> PlanReview:
    """Apply the rules of a plan change to the workflow and the journal of a run. Change nothing.

    The workflow is compared with the newest plan of the run, not with
    `run-start`. Raise EngineError when a field other than the tickets differs
    (see `_check_same_run`), when the plan changed and the journal records
    no plan to compare with, or when the owner abandoned the run (#201).

    `retry` is the ticket of `--retry`. When its newest `step-end` is `failed`,
    its text can change (rule 10): the retry starts it again.
    """
    abandoned = abandoned_end(events)
    if abandoned is not None:
        reason = f" ({abandoned['reason']})" if abandoned.get("reason") else ""
        raise EngineError(f"run {run_id!r} cannot be resumed: it ended abandoned{reason}; start a new run")
    _check_same_run(workflow, workflow_path, events[0], run_id)
    old_order, new_plan = plan_ids(events), _plan_of(workflow)
    new_order = [t.id for t in new_plan]
    violations = [event for event in events if event["event"] == "invariant-violation"]
    halted = [_violation_text(violations[-1])] if violations else []
    old = _recorded_plan(events)
    if old is None:
        if old_order != new_order:
            raise EngineError(
                f"workflow {workflow_path} differs from run {run_id}: tickets was {old_order!r}, is now {new_order!r}; "
                "the journal of the run records no plan, because an earlier version of delegate started it, "
                "so a plan change cannot be checked: start a new run"
            )
        return PlanReview(run_id, workflow.mode, old_order, new_order, None, other=halted)
    if old == new_plan:
        return PlanReview(run_id, workflow.mode, old_order, new_order, None, other=halted)
    started = {str(event["ticket"]) for event in events if event["event"] == "step-start"}
    ended = [str(event["state"]) for event in events if event["event"] == "step-end" and event["ticket"] == retry]
    check = domain.check_plan_change(old, new_plan, started, retry if ended and ended[-1] == FAILED else None)
    other = [
        f"--reviewed names {ticket!r}, which is not a ticket of the workflow"
        for ticket in reviewed if ticket != domain.REVIEWED_ALL and ticket not in new_order
    ]
    other.extend(halted)
    for ticket in workflow.tickets:
        branch = _ticket_branch(workflow, ticket)
        if ticket.id in check.added and branch_exists(branch):
            other.append(f"branch {branch!r} for the new ticket {ticket.id!r} exists already; delete it or give the ticket another id")
    texts = {t.id: t.text for t in workflow.tickets if t.id in check.added or t.id in check.changed}
    return PlanReview(run_id, workflow.mode, old_order, new_order, check, domain.unreviewed(check.later, reviewed), texts, other)


def review_plan_change(
    workflow: Workflow, workflow_path: Path, repo: Path, run_id: str, vcs: VersionControl, reviewed: tuple[str, ...] = (),
    retry: str | None = None,
) -> PlanReview:
    """Apply the rules of a plan change to the workflow and the run `run_id`, as a resume does, and change nothing.

    `delegate run --resume <run-id> --dry-run` prints the result. `retry` is the
    ticket of `--retry`: the text of a failed ticket that it starts again can
    change (rule 10). Raise
    EngineError when the run is unknown, or when the workflow differs from the
    run in a field other than the tickets.
    """
    top, state_dir = state_directory(repo, vcs, run_id)
    try:
        events = read_events(state_dir / "runs" / run_id / "journal.jsonl", torn_tail=True)
    except JournalError as error:
        raise EngineError(f"run {run_id!r} cannot be resumed: {error}") from None
    if not events or events[0]["event"] != "run-start":
        raise EngineError(f"run {run_id!r} cannot be resumed: its journal does not start with run-start")
    with _vcs_errors():
        return _review(workflow, workflow_path, events, run_id, reviewed, lambda branch: vcs.branch_exists(top, branch), retry)


def _refuse_plan_change(review: PlanReview, workflow_path: Path, run_id: str) -> None:
    """Raise EngineError, which names each problem, when the rules of a plan change refuse the resume."""
    problems = review.problems()
    if problems and not review.changed:
        raise EngineError(f"run {run_id!r} cannot be resumed: " + "; ".join(problems))
    if problems:
        raise EngineError(f"workflow {workflow_path} changes the plan of run {run_id}, and the rules refuse it: " + "; ".join(problems))


@dataclass(frozen=True)
class _RetryPlan:
    """Where a failed step goes on: the phase and the verifier round, from the journal events of the step (`prior`).

    `rejected` and `findings` are set when the branch holds a fix-up commit: the
    commit that the last verdict rejected, and the findings of that verdict. A plan of the phase `fixup` sets them too,
    although the branch holds no fix-up commit yet.

    `revive` holds the other tickets that the verification of the chain failed with the same stop, for the phase
    `chain-fixup`: the retry builds them again, because their branches hold their commits (see `_chain_stop`).
    """

    ticket: str
    phase: str
    round: int
    prior: tuple[dict[str, object], ...]
    rejected: str | None = None
    findings: tuple[str, ...] = ()
    restart: bool = False
    revive: tuple[str, ...] = ()


def _ticket_events(events: Sequence[dict[str, object]], ticket_id: str) -> list[dict[str, object]]:
    """The journal events of the newest step of `ticket_id`: its events from its last `step-start`.

    A retry with a new text starts the ticket again (see `_restart_step`), so the journal of such a ticket holds
    more than one step. The rounds, the verdicts and the reports of the steps before the last one are history.
    """
    own = [event for event in events if event.get("ticket") == ticket_id]
    starts = [index for index, event in enumerate(own) if event["event"] == "step-start"]
    return own[starts[-1] :] if starts else own


def _retry_plan(workflow: Workflow, progress: _Progress, run_id: str, ticket_id: str, restart: bool = False) -> _RetryPlan:
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

    A retry of `assure` mode spawns no specialist, with two exceptions that the agent check (#205) makes. A step that it
    stopped before a specialist session (`_stopped_before_specialist`) has the plan: the specialist. A step that it stopped
    before the fix-up session of a round (`_stopped_before_fixup`) has the plan: the fix-up of that round, again, on the same
    rejected commit with the same findings, and it is not a new round. So a step whose specialist,
    or whose fix-up specialist, gave no valid report that says committed is
    refused. So is a step that the verifier accepted, or rejected after the
    last fix-up round. For `economy` mode, see `_economy_retry_plan`.

    A run that an invariant violation halted is refused in each mode: the state
    of the worktree that the verifier changed cannot be trusted.

    With `restart` the retry changes the text of the ticket, and starts it again
    from its base (see `_restart_step`). It spawns a specialist, so no phase of the
    failed step matters: the plan is the specialist at round 0, and the refusals
    that depend on the phase of the step do not apply.
    """
    refused = _refused(ticket_id, run_id)
    violations = [e for e in progress.events if e["event"] == "invariant-violation"]
    if violations:
        raise EngineError(f"{refused}: {_violation_text(violations[-1])}")
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
    prior = tuple(_ticket_events(progress.events, ticket_id))
    if workflow.mode == domain.ECONOMY:
        return _economy_retry_plan(workflow, progress, refused, ticket_id, prior, restart)
    if restart:
        return _RetryPlan(ticket_id, RETRY_SPECIALIST, 0, prior, restart=True)
    if _stopped_before_specialist(prior):
        return _RetryPlan(ticket_id, RETRY_SPECIALIST, 0, prior)
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
    if _stopped_before_fixup(prior, round_number):
        return _RetryPlan(ticket_id, RETRY_FIXUP, round_number, prior, rejected, findings)
    reports = [e for e in prior if e["event"] == "fixup-report" and e["round"] == round_number]
    if not reports or not reports[-1]["valid"] or reports[-1]["status"] != domain.COMMITTED:
        raise EngineError(
            f"{refused}: the fix-up specialist of round {round_number} gave no valid report that says committed; "
            "--retry goes on only at the gates, the verifier or the scoped verifier"
        )
    return _RetryPlan(ticket_id, RETRY_GATES, round_number, prior, rejected, findings)


def _stopped_before_specialist(prior: tuple[dict[str, object], ...]) -> bool:
    """True when the agent check (#205) stopped the step before a specialist session, and no agent started after it.

    The step then holds no work of that session, so a retry of `assure` mode can spawn the specialist. It is the only
    case where a retry of `assure` mode spawns one, with a new text of the ticket excepted (see `_restart_step`).
    """
    stops = [index for index, e in enumerate(prior) if e["event"] == "agents-changed"]
    if not stops or prior[stops[-1]]["role"] != ROLE_SPECIALIST:
        return False
    return not any(e["event"] == "agent-start" for e in prior[stops[-1] + 1 :])


def _stopped_before_fixup(prior: tuple[dict[str, object], ...], round_number: int) -> bool:
    """True when the agent check (#205) stopped the step before the fix-up session of `round_number`, and no agent started after it.

    The step then holds no work of that session: the branch holds the rejected commit, and the journal holds no
    `fixup-start` of the round (see `_run_fixup`). A retry of `assure` mode can run the fix-up of the round again.
    """
    stops = [index for index, e in enumerate(prior) if e["event"] == "agents-changed"]
    if not stops or prior[stops[-1]]["role"] != ROLE_FIXUP or prior[stops[-1]]["round"] != round_number:
        return False
    return not any(e["event"] == "agent-start" for e in prior[stops[-1] + 1 :])


def _chain_stop(prior: tuple[dict[str, object], ...]) -> dict[str, object] | None:
    """The `agents-changed` event of a stop before the chain fix-up of an `economy` step, or None (#209).

    The stop is the newest `agents-changed` of the step, for the role `fix-up`, and no agent started after it. The
    chain verification wrote it with the ticket that ends the stack (see `_ChainRun`), and failed the tickets of the stack.
    """
    stops = [index for index, e in enumerate(prior) if e["event"] == "agents-changed"]
    if not stops or prior[stops[-1]]["role"] != ROLE_FIXUP:
        return None
    if any(e["event"] == "agent-start" for e in prior[stops[-1] + 1 :]):
        return None
    return prior[stops[-1]]


def _refused(ticket_id: str, run_id: str) -> str:
    return f"ticket {ticket_id!r} of run {run_id!r} cannot be retried"


def _specialist_committed(prior: tuple[dict[str, object], ...]) -> bool:
    """True when the journal shows a valid specialist report that says committed, after the last spawn of the specialist."""
    validations = [e for e in prior if e["event"] == "report-validation"]
    return _specialist_done(list(prior)) and validations[-1]["status"] == domain.COMMITTED


def _economy_retry_plan(
    workflow: Workflow, progress: _Progress, refused: str, ticket_id: str, prior: tuple[dict[str, object], ...], restart: bool = False
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

    With `restart` the step starts again from the chain start of now, so the
    phase is the specialist, and the check of the chain start does not apply
    (see `_restart_step`).

    A step that the agent check stopped before the chain fix-up session is the exception to the refusal for the
    verification of the chain: the phase is `chain-fixup` (see `_chain_fixup_plan`).
    """
    if not any(e["event"] == "step-start" for e in prior):
        raise EngineError(f"{refused}: its step did not start; --retry goes on only with a step that started")
    stop = None if restart else _chain_stop(prior)
    if stop is not None:
        return _chain_fixup_plan(workflow, progress, ticket_id, prior, stop)
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
    if restart:
        return _RetryPlan(ticket_id, RETRY_SPECIALIST, 0, prior, restart=True)
    phase = RETRY_GATES if _specialist_committed(prior) else RETRY_SPECIALIST
    return _RetryPlan(ticket_id, phase, 0, prior)


def _chain_fixup_plan(
    workflow: Workflow, progress: _Progress, ticket_id: str, prior: tuple[dict[str, object], ...], stop: dict[str, object]
) -> _RetryPlan:
    """The plan of a retry after the agent check stopped the chain verification before a fix-up session (#209).

    The ticket is the one that ends the stack, the ticket of `stop`. The stop failed each ticket of the stack, and the tickets
    of the stacks after it, which the chain did not verify. Those tickets, which the journal failed after the stop, go in
    `revive`: their branches hold their commits, and the chain verification checks them again with the ticket. The round is
    the round of the stopped fix-up.
    """
    stop_index = max(index for index, e in enumerate(progress.events) if e is stop or e == stop)
    ended = {
        str(e["ticket"]): (index, e["state"]) for index, e in enumerate(progress.events) if e["event"] == "step-end"
    }
    revive = tuple(
        ticket.id for ticket in plan_order(workflow)
        if ticket.id != ticket_id and ticket.id in progress.failed and ended[ticket.id][0] > stop_index
    )
    return _RetryPlan(ticket_id, RETRY_CHAIN_FIXUP, int(stop["round"]), prior, revive=revive)  # type: ignore[call-overload]


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
        now = vcs.commit_of(repo, _start_ref(workflow, order, built, ticket, run_base, vcs, repo))
    if started != now:
        raise EngineError(
            f"{_refused(plan.ticket, run_id)}: its branch starts at commit {started}, "
            f"but the chain before it now ends at commit {now}; the verification of the chain changed or failed the tickets before it"
        )


def check_start(
    workflow: Workflow, workflow_path: Path, repo: Path, tiers: Tiers, adapter: Adapter, vcs: VersionControl, run_id: str, *,
    resume: bool = False, break_lock: bool = False, reviewed: tuple[str, ...] = (), retry: str | None = None,
) -> None:
    """The checks of a run that need no agent: the tier of each role, the lock, and the branches or the plan. Create nothing.

    Raise EngineError, which names the input at fault, when a check fails. A run branch in use is reported first: the
    run branch of a run that works exists. A new run needs its base branch, and a run branch and ticket branches that
    do not exist. A resume needs the journal of the run, and the rules of a plan change (see `_review`), with the later
    tickets in `reviewed`, must accept the workflow. `run_workflow` does these checks before it takes the lock.
    `delegate run --detach` does them before it starts the run process, so a run that cannot start gives its error on
    stderr and exits non-zero, as in the foreground.
    """
    repo, state_dir = state_directory(repo, vcs, run_id)
    for role in ("specialist", "verifier"):
        _role_model(workflow, tiers, adapter.tier_column, role)
    try:
        # The lock of a run that started before #200 is in `<git common dir>/delegate`, so a run there counts too.
        for directory in _state_directories(repo, vcs)[1]:
            check_free(directory, workflow.run_branch, run_id, break_stale=break_lock)
    except LockError as error:
        raise EngineError(str(error)) from None
    if resume:
        _refuse_plan_change(review_plan_change(workflow, workflow_path, repo, run_id, vcs, reviewed, retry), workflow_path, run_id)
    else:
        _check_branches(workflow, repo, vcs)


def run_workflow(
    workflow: Workflow, workflow_path: Path, repo: Path, tiers: Tiers, adapter: Adapter, vcs: VersionControl, gates: GateRunner, *,
    resume: str | None = None, retry: str | None = None, break_lock: bool = False,
    on_start: Callable[[str, Path], None] | None = None, run_id: str | None = None, reviewed: tuple[str, ...] = (),
    agents_changed: bool = False,
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

    The run first makes the checks of `check_start`. A refused run creates nothing, so the lock is taken last.
    The run holds the lock of its run branch until it ends. Raise EngineError,
    which names the holder, when another run holds the lock. A resume takes over
    a lock of its own run whose process no longer exists, and the result names
    that holder in `taken_over`. `break_lock` also removes a lock of another run
    whose process no longer exists. No lock whose process is alive is removed.

    `on_start` gets the run id and the path of the journal when the run holds its lock and the journal records its
    start (`run-start`, or `resume` and a plan change), before the first step. A resume that the engine refuses does not
    call it. `delegate run` then puts the id in the title of the terminal and prints its start lines.

    Ctrl-C (`KeyboardInterrupt`) stops the run at once. The run releases its lock, appends `interrupted` to its
    journal, and raises RunInterrupted. A resume then goes on as after a crash.

    `run_id` is the id of a new run. The default is a new id from `new_run_id`. `delegate run --detach` gives the id
    that it printed before it started the run process. A resume uses the id `resume`, and ignores `run_id`.

    A resume with a workflow whose tickets differ from the newest plan of the run
    is a plan change (see `_review` and `domain.check_plan_change`). `reviewed`
    holds the ids of the later tickets that the coordinator reviewed, or
    `all`. Raise EngineError, which names each ticket and field at fault, when
    the rules refuse the change. A plan change also goes on with a run that ended.

    `agents_changed` is for a resume: the engine records the hashes of the agent files of the main checkout again, in
    the `resume` event, and goes on with them as the agents of the run (see `delegate.run.agents`). A resume of a
    journal with no record of the agent files records them too, and the result has `agents_note`.
    """
    run_id = resume if resume is not None else (run_id or new_run_id())
    check_start(
        workflow, workflow_path, repo, tiers, adapter, vcs, run_id, resume=resume is not None, break_lock=break_lock, reviewed=reviewed,
        retry=retry,
    )
    repo, state_dir = state_directory(repo, vcs, run_id)
    record_state_root(repo, vcs, state_dir)
    roles = {role: _role_model(workflow, tiers, adapter.tier_column, role) for role in ("specialist", "verifier")}
    try:
        with run_lock(state_dir, workflow.run_branch, run_id, break_stale=break_lock) as broken:
            try:
                result = _execute(
                    workflow, workflow_path, adapter, vcs, gates, roles, repo, state_dir, run_id, resume is not None, broken, retry,
                    reviewed, on_start, agents_changed, tiers,
                )
            except KeyboardInterrupt:
                # The block of the lock ends with this exception, so the lock is released.
                raise RunInterrupted(run_id, _record_interrupt(state_dir, run_id)) from None
            return result if break_lock else replace(result, taken_over=broken)
    except LockError as error:
        raise EngineError(str(error)) from None


def _record_interrupt(state_dir: Path, run_id: str) -> Path | None:
    """Append `interrupted` to the journal of the run, and return its path. Return None when the run has no journal that can be read."""
    path = state_dir / "runs" / run_id / "journal.jsonl"
    try:
        journal, _ = Journal.reopen(path)
    except JournalError:
        return None
    journal.append("interrupted", run_id=run_id)
    return path


def _execute(
    workflow: Workflow, workflow_path: Path, adapter: Adapter, vcs: VersionControl, gates: GateRunner,
    roles: dict[str, tuple[str, str]], repo: Path, state_dir: Path, run_id: str, resuming: bool, broken: LockHolder | None,
    retry: str | None = None, reviewed: tuple[str, ...] = (), on_start: Callable[[str, Path], None] | None = None,
    agents_changed: bool = False, tiers: Tiers | None = None,
) -> RunResult:
    order = plan_order(workflow)
    agents_note: str | None = None
    plan: _RetryPlan | None = None
    # A pause request from before this process is not for it: the run that it asked to pause has stopped.
    (state_dir / "runs" / run_id / PAUSE_REQUEST_NAME).unlink(missing_ok=True)
    if not resuming:
        # The seal comes before the run branch: an adapter that refuses to seal leaves no branch and no journal (#217).
        sealed = _seal_agents(adapter, repo, state_dir / "runs" / run_id, workflow)
        with _vcs_errors():
            run_base = vcs.commit_of(repo, workflow.base_branch)
            vcs.create_branch(repo, workflow.run_branch, run_base)
        journal = Journal(state_dir / "runs" / run_id / "journal.jsonl")
        journal.append(
            RUN_START, run_id=run_id, workflow=str(workflow_path.resolve()), mode=workflow.mode, adapter=workflow.adapter,
            base_branch=workflow.base_branch, base_commit=run_base, run_branch=workflow.run_branch, tickets=[t.id for t in order],
            plan=_plan_record(_plan_of(workflow)), schema=SCHEMA_VERSION,
            agents=sealed,
        )
        progress = _Progress()
    else:
        journal, events = _journal_events(state_dir, run_id)
        moved = [e for e in events if e["event"] == "run-branch-moved"]
        if moved:
            raise EngineError(
                f"run {run_id!r} cannot be resumed: {_moved_text(workflow.run_branch, str(moved[-1]['expected']), str(moved[-1]['found']))}; "
                "a halted run cannot be resumed: start a new run"
            )
        with _vcs_errors():
            review = _review(workflow, workflow_path, events, run_id, reviewed, lambda branch: vcs.branch_exists(repo, branch), retry)
        # `check_start` made this review before the lock. It is made again here, because the journal can change before the lock.
        _refuse_plan_change(review, workflow_path, run_id)
        progress = restore_progress(events, run_id, retrying=retry is not None or review.changed)
        with _vcs_errors():
            run_branch_exists = vcs.branch_exists(repo, workflow.run_branch)
        if not run_branch_exists:
            raise EngineError(f"run branch {workflow.run_branch!r} of run {run_id!r} is not a branch of the repository {repo}")
        run_base = _run_base(vcs, repo, workflow, events)
        _check_run_branch_at_resume(journal, events, vcs, repo, workflow.run_branch, progress.built, progress.failed, progress.skipped)
        # A step that cannot be retried is refused before the journal changes.
        restart = retry is not None and review.check is not None and retry in review.check.restarted
        plan = _retry_plan(workflow, progress, run_id, retry, restart) if retry is not None else None
        if plan is not None and workflow.mode == domain.ECONOMY and not plan.restart:
            # A ticket of a later stack that the stop failed does not count: the retried ticket starts from the chain before it (#209).
            before = [t.id for t in order[: [t.id for t in order].index(plan.ticket)]]
            chain = [*progress.built, *(t for t in plan.revive if t in before)]
            _check_chain_start(workflow, order, chain, plan, vcs, repo, run_base, run_id)
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
        journal.append(
            "resume", run_id=run_id, built=list(progress.built), failed=list(progress.failed),
            skipped=list(progress.skipped), open=progress.open_ticket, **agent_fields,
        )
        if review.check is not None:
            _record_plan_change(journal, workflow, review.check, progress)
    if on_start is not None:
        on_start(run_id, journal.path)
    if broken is not None:
        journal.append("lock-broken", run_id=broken.run_id, pid=broken.pid)
    if tiers is not None:
        _probe_guards(adapter, vcs, tiers, journal, workflow, state_dir / "runs" / run_id, run_id)
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
            journal.append("step-end", ticket=revived, state=BUILT, reason=None)
        if workflow.mode == domain.ECONOMY:
            # The chain goes on after the step: the skip rule applies again to each ticket after it that was skipped.
            for later in order[[t.id for t in order].index(plan.ticket) + 1 :]:
                skipped.pop(later.id, None)
        else:
            # The skip rule applies again to each ticket that the failed step made the engine skip.
            for dependant in domain.skipped_because_of(plan.ticket, [(t.id, t.blocked_by) for t in order], skipped):
                del skipped[dependant]
    complete = {*built, *failed, *skipped}
    ended_early = False
    paused: str | None = None
    stopped_by: _Halted | None = None
    for position, ticket in enumerate(order):
        if ticket.id in complete:
            continue
        skip = domain.skip_reason(ticket.blocked_by, built, failed)
        if skip:
            skipped[ticket.id] = skip
            journal.append("skip", ticket=ticket.id, blockers=domain.unbuilt_blockers(ticket.blocked_by, built), reason=skip)
            continue
        prior = _ticket_events(progress.events, ticket.id) if ticket.id == progress.open_ticket else None
        start = _start_ref(workflow, order, built, ticket, run_base, vcs, repo)
        try:
            _pause_point(journal, ticket.id, PAUSED_BEFORE_STEP, f"before the step of ticket {ticket.id}")
            if plan is not None and ticket.id == plan.ticket:
                reason = _retry_ticket(
                    _Step(workflow, ticket, adapter, vcs, gates, roles, repo, state_dir, run_id, journal), plan, retried_reason, start
                )
            else:
                reason = _build_ticket(workflow, ticket, adapter, vcs, gates, roles, repo, state_dir, run_id, journal, start, prior)
        except _Paused as stop:
            # The step stays open, or does not start: a resume goes on with it.
            paused = str(stop)
            break
        except _Halted as halt:
            # The harness failed, not the work. The step stays open: a resume goes on with it.
            stopped_by = halt
            break
        except Exception as error:  # noqa: BLE001 - the journal records it and the command reports it
            # An exception that the engine did not plan for leaves the state of the
            # step unknown, so the run ends here. The journal says why. A verifier
            # that changed the real worktree also ends the run: the state of the
            # worktree can no longer be trusted.
            halted, ended_early = isinstance(error, (InvariantViolation, _RunBranchMoved)), True
            reason = _halt_reason(error)
            journal.append("step-end", ticket=ticket.id, state=FAILED, reason=reason, **_crash_fields(journal, error))
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
    if workflow.mode == "economy" and built and not ended_early and paused is None and stopped_by is None:
        chain = _ChainRun(workflow, order, adapter, vcs, gates, roles, repo, state_dir, run_id, journal, built, failed, failures)
        try:
            unmapped = chain.verify()
        except _Paused as stop:
            paused = str(stop)
        except _Halted as halt:
            stopped_by = halt
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
        _clean_up(workflow, order, vcs, repo, state_dir, run_id, journal)
    journal.append("run-end", result=result, built=built, failed=failed, skipped=list(skipped))
    # A request after the last step boundary finds no boundary: the run ended, and the request goes.
    (journal.path.parent / PAUSE_REQUEST_NAME).unlink(missing_ok=True)
    return RunResult(run_id, journal.path, built, failed, failures, skipped, unmapped, agents_note=agents_note)


def _clean_up(
    workflow: Workflow, order: list[Ticket], vcs: VersionControl, repo: Path, state_dir: Path, run_id: str, journal: Journal
) -> None:
    """Remove the worktree and the push-guard hooks of each ticket, and delete each ticket branch that the run branch holds (#167).

    The engine calls this when the run ends `built`. The rules are in
    `cleanup.clean_tickets`, which `delegate clean` uses too. The run branch,
    the journal, the reports and the gate logs stay: they are the evidence of
    the run. The journal records one `cleanup` event with what went, what
    stayed, and why.
    """
    worktrees_dir, hooks_dir = state_dir / "worktrees" / run_id, state_dir / "hooks" / run_id
    trees = [TicketTree(worktrees_dir / ticket.id, _ticket_branch(workflow, ticket), hooks_dir / ticket.id) for ticket in order]
    result = clean_tickets(vcs, repo, trees, workflow.run_branch)
    remove_empty_directories(worktrees_dir, hooks_dir)
    journal.append("cleanup", **result.fields())


def _pause_point(journal: Journal, ticket: str, before: str, where: str, **fields: object) -> None:
    """A step boundary: stop the run here when the directory of the run holds a pause request. Do nothing when it holds none.

    Write `pause-requested` with the time of the request, and `paused` with the
    ticket, the work that does not start (`before`, one of the `PAUSED_BEFORE_`
    values) and `fields`. Remove the request, and raise `_Paused` with `where`,
    the text that tells where the run stopped. The engine calls it only between
    two pieces of work, so a pause never cuts off an agent or a gate.
    """
    request = journal.path.parent / PAUSE_REQUEST_NAME
    try:
        text = request.read_text()
    except FileNotFoundError:
        return
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    requested_at = data.get("time") if isinstance(data, dict) else None
    journal.append("pause-requested", ticket=ticket, requested_at=requested_at)
    journal.append("paused", ticket=ticket, before=before, **fields)
    request.unlink(missing_ok=True)
    raise _Paused(where)


def _record_plan_change(journal: Journal, workflow: Workflow, check: domain.PlanCheck, progress: _Progress) -> None:
    """Write `plan-change`, and apply the change to the progress of the run.

    A removed ticket leaves the run. A failed ticket that a retry starts again with a new text is in `retry_text`, with its old and its new `text_sha256`. A changed ticket that the run skipped is
    no longer skipped: the skip rule applies to it again, with its new blockers.
    """
    plan = _plan_of(workflow)
    journal.append(
        "plan-change", added=check.added, changed=list(check.changed), fields=check.changed, removed=check.removed,
        order=[t.id for t in plan], reviewed=check.later, plan=_plan_record(plan),
        **({"retry_text": {t: {"old": old, "new": new} for t, (old, new) in check.restarted.items()}} if check.restarted else {}),
    )
    for ticket_id in [*check.removed, *check.changed]:
        progress.skipped.pop(ticket_id, None)


def _plan_change_note(journal: Journal, workflow: Workflow, ticket_ids: list[str], verifier: bool) -> str | None:
    """The text of the section "Plan change" for the brief of the tickets `ticket_ids`, or None when none needs it.

    A ticket needs it when a `plan-change` of the journal lists it as a later
    ticket. The section names each ticket that such a change added or changed,
    and that comes before the ticket in the plan, with its text.
    """
    events = [event for event in read_events(journal.path) if event["event"] == "plan-change"]
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
        location = (
            "The engine builds these tickets before this ticket. Each ticket of the chain starts from the ticket before it, "
            "so the branch of this ticket holds their work."
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
    return _tip(vcs, repo, workflow.run_branch)


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
    branch = _ticket_branch(workflow, next(ticket for ticket in order if ticket.id == predecessor))
    with _vcs_errors():
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

    An adapter that refuses to seal (an `AdapterError`) stops the command with an `EngineError`, and the journal gets no event.
    """
    try:
        return seal_agents(adapter, repo, run_dir, agent_names(workflow))
    except AdapterError as error:
        raise EngineError(f"the agents of the run cannot be sealed: {error}") from None


def _probe_guards(
    adapter: Adapter, vcs: VersionControl, tiers: Tiers, journal: Journal, workflow: Workflow, run_dir: Path, run_id: str
) -> None:
    """Start the guard probe of each verifier whose seal has no denied probe, and end the run for a result other than `denied` (#219).

    It runs before the first agent session of the run or of the resume. An adapter whose `probes_guard` is not true gets
    no probe. A verifier that the run did not seal gets none either: a session of an agent with no seal does not run from
    a guard of the run. The journal gets `guard-probe` with the agent, the handle, the result and the event stream. A
    result other than `denied` raises an `EngineError` that names the agent, the result, the limits page and the command
    that goes on. A resume probes again, because no denied probe follows the newest seal. A failure of the harness or of
    the repository of the probe is an `EngineError` too, and the journal gets no event for it.
    """
    if not getattr(adapter, "probes_guard", False):
        return
    events = read_events(journal.path)
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
        journal.append("guard-probe", agent=probe.agent, handle=probe.handle, result=probe.result, event_stream=str(probe.event_stream))
        if probe.result != DENIED:
            raise EngineError(f"{refusal(agent, probe.result)}; the run started no agent session; a resume with delegate run --resume {run_id} probes again")


def _check_agent_file(
    journal: Journal, agent: str, ticket: str, role: str, round_number: int, fields: dict[str, object] | None = None,
) -> SealedAgent | None:
    """Fail the step before an agent session when a file of the seal of the agent is not as the run recorded it (#217).

    The journal gets `agents-changed` with the ticket, the role, the round, the tree `seal`, the agent, the path of the
    changed file, the recorded hash, and the new hash (`None` for a missing file), and `fields` (the fix-up gives the
    fields that a retry needs to run it again, see `_run_fixup`). Raise `_StepFailed`, whose message names the file and
    both hashes. The agent file in the tree of the agent is not read. Give the sealed agent for the request, or None when
    the run recorded no seal for the agent.
    """
    recorded = recorded_agents(read_events(journal.path))
    if recorded is None:
        return None
    sealed, change = check_agent(recorded, agent)
    if change is None:
        return sealed
    journal.append(
        "agents-changed", ticket=ticket, role=role, round=round_number, tree=TREE_SEAL, agent=agent, path=str(change.path),
        recorded_hash=change.recorded, new_hash=change.found, **(fields or {}),
    )
    raise _StepFailed(change.message(ticket))


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


def _request(
    agent: str, stack: Stack, model: str, prompt: str, cwd: Path, report_path: Path, stream: Path,
    report_valid: Callable[[], bool], resume_session: str | None = None, added_directories: tuple[Path, ...] = (),
    sealed: SealedAgent | None = None, environment: Mapping[str, str] | None = None,
) -> AdapterRequest:
    """The request for one agent run of the stack: the idle timeout of the run, the check of its report, the MCP servers and the agent timeout of the stack.

    `environment` holds the entries of the worktree of the ticket (see `_worktree_environment`).
    """
    return AdapterRequest(
        agent, model, prompt, cwd, report_path, stream, resume_session,
        idle_timeout=domain.IDLE_TIMEOUT_SECONDS, report_valid=report_valid, mcp_servers=stack.mcp_servers,
        added_directories=added_directories, agent_timeout=_seconds(stack.agent_timeout), sealed=sealed,
        environment=dict(environment or {}),
    )


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


def _seconds(minutes: float | None) -> float | None:
    """A time limit of a stack, given in minutes, in seconds. None stays None: no limit."""
    return None if minutes is None else minutes * 60


def _minutes_text(minutes: float | None) -> str:
    """A time limit in minutes as the reasons and the notes write it: `10` for 10.0, `0.05` for 0.05."""
    return f"{minutes:g}"


def _journal_timeout(journal: Journal, result: AdapterResult, stack: Stack, ticket: str, role: str, round_number: int) -> None:
    """Record that the adapter ended the session of the agent at the agent timeout of the stack, if it did, before its result event."""
    if result.ended_timeout:
        journal.append("agent-timeout", ticket=ticket, role=role, round=round_number, seconds=_seconds(stack.agent_timeout))


def _ended_reason(result: AdapterResult, who: str, stack: Stack) -> str:
    """The reason that an agent run is no done session: the time limit when the adapter ended it there, else its end state and exit status."""
    if result.ended_timeout:
        return f"{who} session ran past the agent-timeout of {_minutes_text(stack.agent_timeout)} minutes"
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


def _specialist_done(prior: list[dict[str, object]]) -> bool:
    """True when the journal shows a valid specialist report after the last time the specialist was spawned."""
    spawned = valid = -1
    for index, event in enumerate(prior):
        if event["event"] in ("step-start", "continuation"):
            spawned = index
        elif event["event"] == "report-validation" and event["valid"]:
            valid = index
    return valid > spawned


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
    with _vcs_errors():
        vcs.ensure_worktree(repo, worktree, branch, base)
    try:
        install_push_guard(vcs, repo, worktree, hooks_dir)
    except GuardError as error:
        raise EngineError(str(error)) from None
    return made


def _run_prepare(
    gates: GateRunner, vcs: VersionControl, journal: Journal, stack: Stack, ticket: str, tree: str, path: Path, round_number: int
) -> None:
    """Run the `prepare` commands of `stack` in `path`, in order, through the gate runner (#155).

    `tree` is TREE_WORKTREE or TREE_VERIFY_COPY. A command in the worktree gets the entries of the worktree (#221). A git tree holds no ignored
    file, so a stack that needs installed dependencies names the commands that
    install them. The engine runs them itself, with the environment of a gate,
    before an agent starts in the tree. The journal records `prepare-start` and
    `prepare-result` for each command, and each command gets its own log file.
    A command that fails is not a red gate: it starts no fix-up round. Raise
    `_StepFailed` with the reason `prepare failed: <command>`, and run no later command.

    In the worktree, after the last command, the worktree must be clean: a
    changed or untracked path that is not ignored fails the step with the reason
    `prepare left <paths> in the worktree; ...` (#224), before the specialist
    starts and so before a check for uncommitted changes can take the path for
    work of the specialist.
    """
    fields: dict[str, object] = {"ticket": ticket, "round": round_number, "tree": tree, "path": str(path)}
    # The verifier copy is a repository of its own, which holds its settings itself.
    environment = _worktree_environment(vcs, path) if tree == TREE_WORKTREE else None
    for command in stack.prepare:
        log = _gate_log_path(journal, ticket, f"prepare-{tree}", round_number)
        journal.append("prepare-start", **fields, command=command, prepare_log=str(log))
        result = gates.run(command, path, log=log, environment=environment)
        journal.append("prepare-result", **fields, command=command, exit_status=result.exit_status, prepare_log=str(log))
        if not result.green:
            raise _StepFailed(f"prepare failed: {command}")
    if tree == TREE_WORKTREE:
        with _vcs_errors():
            left = sorted({change.path for change in vcs.uncommitted_changes(path)})
        if left:
            raise _StepFailed(
                f"prepare left {', '.join(left)} in the worktree; add them to .gitignore, or make prepare leave the worktree clean"
            )


def _prepare_ticket_worktree(gates: GateRunner, vcs: VersionControl, journal: Journal, stack: Stack, ticket: str, worktree: Path, made: bool) -> None:
    """Run the `prepare` commands of `stack` in the worktree of `ticket` when it needs them. Raise `_StepFailed` when one fails.

    The worktree needs them when the engine made it now (`made`), or when the
    journal holds no complete prepare of it: a run that stopped or failed
    before the last command of `prepare` was green. A worktree that the engine
    prepared before keeps what the commands made, so they do not run again.
    """
    if not stack.prepare:
        return
    if not made:
        last = None
        for event in read_events(journal.path):
            if event["event"] in ("prepare-start", "prepare-result") and event.get("ticket") == ticket and event.get("tree") == TREE_WORKTREE:
                last = event
        if last is not None and last["event"] == "prepare-result" and last["exit_status"] == 0 and last["command"] == stack.prepare[-1]:
            return
    _run_prepare(gates, vcs, journal, stack, ticket, TREE_WORKTREE, worktree, 0)


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

    `retried` is the reason a step failed, when a retry goes on with it. The step then goes on in its own
    worktree with the events of the failed step in `prior`. The specialist
    continues unless its report was valid and said committed, and it has the
    continuation limit of the mode again. A retry uses it in two cases:

    - In `economy` mode, for each retry that goes on in this function: at the specialist, or at the check of its
      report (see `_economy_retry_plan`). A retry that goes on at the chain fix-up does not call this function.
    - In `assure` mode, only for a step that the agent check stopped before a specialist session (see
      `_stopped_before_specialist`): the plan is the specialist. The other retries of `assure` mode go on in
      `_retry_ticket`, with no specialist: at the gates, at a verifier, or, after a stop before a fix-up session, at the fix-up of
      the same round (see `_stopped_before_fixup`).
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
        made = _prepare_worktree(vcs, repo, worktree, branch, start_ref, hooks_dir)
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
        # Before anything else: a verifier that a stop cut off can have changed the real worktree (#192).
        _check_verifier_stop(journal, vcs, worktree, ticket.id, prior)
        if retried is None and workflow.mode == domain.ASSURE and _recover_advance(workflow, ticket, vcs, repo, journal, prior):
            return None
        base_commit = str(next(e for e in prior if e["event"] == "step-start")["base_commit"])
        made = _prepare_worktree(vcs, repo, worktree, branch, start_ref, hooks_dir)
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
    try:
        _prepare_ticket_worktree(gates, vcs, journal, stack, ticket.id, worktree, made)
    except _StepFailed as failed:
        return str(failed)
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


def _recover_advance(workflow: Workflow, ticket: Ticket, vcs: VersionControl, repo: Path, journal: Journal, prior: list[dict[str, object]]) -> bool:
    """Write the `run-branch-advance` that a stop left out, and tell whether the step is closed.

    The engine moves the run branch first and writes `run-branch-advance` after it. A stop between the two leaves an
    open step whose commit is on the run branch already. Git is the record of the work that landed, so the step is
    recovered when the newest verdict of the step is ACCEPT and the run branch holds the commit of the `verify-start`
    of that verdict. The event has `recovered: true`. Return False, and write nothing, in any other case.
    """
    verdicts = [index for index, e in enumerate(prior) if e["event"] == "verdict"]
    if not verdicts or not domain.is_accepted(str(prior[verdicts[-1]]["verdict"])):
        return False
    starts = [e for e in prior[: verdicts[-1]] if e["event"] == "verify-start"]
    if not starts:
        return False
    accepted = str(starts[-1]["commit"])
    run_branch = workflow.run_branch
    if not _holds(vcs, repo, accepted, f"refs/heads/{run_branch}"):
        return False
    # The step fast-forwarded from the commit that it rebased onto, or from its own base when it did not rebase.
    rebases = [e for e in prior if e["event"] == "rebase" and e["result"] == "rebased"]
    from_commit = str(rebases[-1]["onto_commit"]) if rebases else str(next(e for e in prior if e["event"] == "step-start")["base_commit"])
    journal.append(
        "run-branch-advance", ticket=ticket.id, run_branch=run_branch, from_commit=from_commit, to_commit=accepted, recovered=True
    )
    return True


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
    plan_change = _plan_change_note(journal, workflow, [ticket.id], verifier=False)
    prompt = specialist_brief(
        ticket.id, ticket.text, worktree, stack.all_hotspots, stack.gates, report_path, gates_at_chain_end=not each_ticket,
        plan_change=plan_change, notes=stack.specialist_notes,
    )
    if interrupted and not spawned:
        so_far = _commit_lines(vcs, worktree, since)
        if so_far:
            prompt = continuation_brief(
                ticket.id, ticket.text, worktree, stack.all_hotspots, stack.gates, report_path, so_far,
                stop_reason, resumed=False, gates_at_chain_end=not each_ticket, plan_change=plan_change,
                notes=stack.specialist_notes,
            )
    resume_session: str | None = None
    leftovers: frozenset[str] = frozenset()  # the paths that red gates left in the worktree (see `_uncommitted_before_gates`)
    report_valid = _report_check(lambda: read_specialist_report(report_path, ticket.id))
    while True:
        if spawned:
            spawned, result = False, None
        else:
            try:
                sealed = _check_agent_file(journal, stack.specialist, ticket.id, ROLE_SPECIALIST, 0)
            except _StepFailed as failed:
                return str(failed)
            stream = _start_agent(journal, ticket.id, ROLE_SPECIALIST, 0, report_path)
            result = _run_agent(
                adapter, journal, ticket.id, ROLE_SPECIALIST, 0,
                _request(
                    stack.specialist, stack, model, prompt, worktree, report_path, stream, report_valid, resume_session, sealed=sealed,
                    environment=_worktree_environment(vcs, worktree),
                ),
            )
            _journal_idle(journal, result, ticket.id, ROLE_SPECIALIST, 0)
            _journal_timeout(journal, result, stack, ticket.id, ROLE_SPECIALIST, 0)
            journal.append(
                "adapter-result", ticket=ticket.id, exit_status=result.exit_status, end_state=result.end_state,
                session_id=result.session_id, event_stream=str(result.event_stream), **_refused_fields(result),
                **_totals_fields(adapter, result.event_stream),
            )
        gate_output: str | None = None
        problems: tuple[str, ...] = ()
        uncommitted_paths: tuple[str, ...] = ()
        if result is not None and not _session_done(result, report_valid):
            trigger = harness.CAPPED if result.end_state == harness.CAPPED else harness.FAILED
            reason = _ended_reason(result, "the specialist", stack)
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
                    status=report.status, blocked_reason=report.blocked_reason, **_lenient_fields(report),
                )
                unfinished = domain.unfinished_reason(report.status, report.blocked_reason, "the specialist")
                if unfinished:
                    return unfinished
                if not _commit_lines(vcs, repo, since, branch):
                    return f"branch {branch} holds no commit beyond {since}, though the report says committed"
                stop = _hotspot_stop(vcs, journal, ticket.id, stack, worktree, since, True, PHASE_BUILD, 0)
                if stop:
                    return stop
                # A gate runs in the worktree as it is, so a change that is not committed would make its result a fact about
                # something else than the commit. `economy` mode runs no gate here, but its chain gates run on this tree later.
                uncommitted = _uncommitted_before_gates(journal, vcs, worktree, stack.gates if each_ticket else None, leftovers)
                if uncommitted:
                    trigger, reason, uncommitted_paths = TRIGGER_UNCOMMITTED, _uncommitted_reason(uncommitted), tuple(uncommitted)
                elif not each_ticket:
                    return None  # `economy` mode: the gates run once, on the tip of the chain (see `_ChainRun`)
                else:
                    outcomes = [
                        _run_gate_with_output(gates, vcs, gate, ticket.id, worktree, journal, 0, PHASE_BUILD, stack.gate_timeout, stack.gate_names.get(gate))
                        for gate in stack.gates
                    ]
                    red = [outcome for outcome in outcomes if not outcome.green]
                    if not red:
                        return None
                    trigger = "gates-red"
                    # The gates ran in a tree with no change that is not committed, so what the tree holds now is theirs.
                    leftovers = frozenset(_uncommitted_paths(vcs, worktree))
                    reason = "gates red: " + "; ".join(outcome.label for outcome in red)
                    gate_output = "\n".join(f"$ {outcome.command}\n{outcome.output.rstrip(chr(10))}" for outcome in red)
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
                ticket.id, ticket.text, worktree, stack.all_hotspots, stack.gates, report_path, commits, reason,
                resumed=resume_session is not None, gate_output=gate_output, gates_at_chain_end=not each_ticket,
                plan_change=plan_change, notes=stack.specialist_notes, uncommitted=uncommitted_paths,
            )


def _uncommitted_paths(vcs: VersionControl, worktree: Path) -> list[str]:
    """The paths of the worktree that differ from its commit, in the order of the paths. A path that git ignores is not one."""
    with _vcs_errors():
        return [change.path for change in vcs.uncommitted_changes(worktree)]


def _uncommitted_before_gates(
    journal: Journal, vcs: VersionControl, worktree: Path, gates: Sequence[str] | None, leftovers: frozenset[str] = frozenset()
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
    """
    if gates is not None:
        commit = _head(vcs, worktree)
        if all(_green_result(journal, gate, commit) is not None for gate in gates):
            return []
    return [path for path in _uncommitted_paths(vcs, worktree) if path not in leftovers]


def _uncommitted_reason(paths: Sequence[str]) -> str:
    """The reason that no gate ran: the paths with a change that is not committed."""
    return "the worktree holds changes that are not committed: " + ", ".join(f"`{path}`" for path in paths)


def _hotspot_stop(
    vcs: VersionControl, journal: Journal, ticket: str, stack: Stack, worktree: Path, since: str, merge_base: bool, phase: str, round_number: int
) -> str | None:
    """Compare the paths that the specialist changed since `since` with the hotspot patterns of the stack.

    Return None when none matches, or the reason the step fails. A match is
    recorded as `hotspot-finding`. The caller stops the step before any gate
    and any verifier runs.
    """
    matches = hotspot_matches(changed_paths(vcs, worktree, since, merge_base=merge_base), stack.all_hotspots)
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
    _check_run_branch(step.journal, step.vcs, step.repo, run_branch, ticket.id)
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
        label for gate in step.stack.gates
        if (label := _run_gate(
            step.gates, step.vcs, gate, ticket.id, step.worktree, step.journal, 0, PHASE_REBASE, step.stack.gate_timeout,
            step.stack.gate_names.get(gate),
        ))
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


def _accepted_commit(step: _Step, first_round: int = 0, first_fixup: tuple[str, str] | None = None) -> str:
    """Verify the branch of the step blind, and return the commit that a verifier accepted.

    A REJECT starts a fix-up round, up to `FIXUP_ROUND_LIMIT` of the mode.
    Each round, and the first pass, spawns a fresh verifier. Raise `_Rejected`
    after the last round, and `_StepFailed` for any other cause.

    Each verifier brief gives the output of the gates that the engine ran green
    on the commit under verification, so the verifier does not run them again
    (see `_engine_gates_text`).

    A resume sets `first_round` to the number of verdicts that the journal
    holds. The first pass of the resume is a full verification at that round
    number, and the limit counts across the stop. A retry that goes on at the
    scoped verifier gives the rejected commit and the findings text of its
    fix-up round in `first_fixup`: the first pass is then the scoped verifier
    of the fix-up commit that the branch holds.
    """
    mode = step.workflow.mode
    used_up = domain.rounds_used_up_reason(mode, first_round)
    if used_up:
        raise _StepFailed(used_up)
    rejected, findings_text = first_fixup if first_fixup is not None else ("", None)
    report: VerifierReport | None = None
    for round_number in domain.verification_rounds(mode, first_round):
        if report is not None:
            findings_text = _fixup_round(step, round_number, rejected, report.findings)
        gates_text = _engine_gates_text(step)
        fixup_body = (
            None if findings_text is None
            else _checked_fixup_brief(step, round_number, rejected, findings_text, gates_text)
        )
        verified = _tip(step.vcs, step.repo, step.branch)
        report = _verify_round(step, round_number, fixup_body, gates_text)
        if domain.is_accepted(report.verdict):
            return verified
        rejected = verified
    assert report is not None
    raise _Rejected(domain.rejection_reason(mode, report.findings), report.findings)


def _fixup_round(step: _Step, round_number: int, rejected: str, findings: list[str]) -> str:
    """Run one fix-up round and return the findings text for the scoped brief of the fresh verifier.

    The engine writes the findings to a file and sends the specialist a fix-up
    brief in the same worktree (see `_run_fixup`). The fix must be a new commit
    on top of the rejected commit, which the brief generator checks before any
    gate runs. The engine then runs the gates itself. The caller makes the
    scoped brief after the gates, so the brief holds their green output.
    """
    findings_text = _run_fixup(step, round_number, rejected, _findings_text(findings))
    _checked_fixup_brief(step, round_number, rejected, findings_text)
    _fixup_gates(step, round_number)
    return findings_text


def _engine_gates_text(step: _Step) -> str | None:
    """The `## Gates` text of a verifier brief of one ticket: the green gate output of the engine on the tip of its branch.

    The verifier reviews the tip of the branch of the step: the rebased commit,
    or the fix-up commit. When the journal holds a green `gate-result` of each
    gate of the stack on that commit, the text gives that output (see
    `green_gates_text`), and the verifier does not run the gates again, as for
    a chain. Return None for a stack with no gates, or when a gate has no green
    result on that commit: the brief then gives the gate commands.
    """
    commit = _tip(step.vcs, step.repo, step.branch)
    outputs: list[tuple[str, str]] = []
    for gate in step.stack.gates:
        green = _green_result(step.journal, gate, commit)
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
    event too. Raise `_StepFailed` when the fix-up specialist does not end with
    a valid report that says committed.
    """
    ticket = step.ticket
    tier, model = step.roles["specialist"]
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
    leftovers = frozenset(_uncommitted_paths(step.vcs, step.worktree))
    prompt = specialist_brief(
        ticket.id, ticket.text, step.worktree, step.stack.all_hotspots, step.stack.gates, report_path, rejected, findings_text,
        red_gates=red_gates, notes=step.stack.specialist_notes,
    )
    stream = _start_agent(step.journal, ticket.id, ROLE_FIXUP, round_number, report_path)
    report_valid = _report_check(lambda: read_specialist_report(report_path, ticket.id))
    result = _run_agent(
        step.adapter, step.journal, ticket.id, ROLE_FIXUP, round_number,
        _request(
            step.stack.specialist, step.stack, model, prompt, step.worktree, report_path, stream, report_valid, sealed=sealed,
            environment=_worktree_environment(step.vcs, step.worktree),
        ),
    )
    _journal_idle(step.journal, result, ticket.id, ROLE_FIXUP, round_number)
    _journal_timeout(step.journal, result, step.stack, ticket.id, ROLE_FIXUP, round_number)
    step.journal.append(
        "fixup-result", ticket=ticket.id, round=round_number, exit_status=result.exit_status, end_state=result.end_state,
        session_id=result.session_id, event_stream=str(result.event_stream), **_refused_fields(result),
        **_totals_fields(step.adapter, result.event_stream),
    )
    if not _session_done(result, report_valid):
        raise _StepFailed(_ended_reason(result, "the fix-up specialist", step.stack))
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
        status=report.status, blocked_reason=report.blocked_reason, **_lenient_fields(report),
    )
    unfinished = domain.unfinished_reason(report.status, report.blocked_reason, "the fix-up specialist")
    if unfinished:
        raise _StepFailed(unfinished)
    # The gates run next, and a gate runs in the worktree as it is (#176). A fix-up with no new commit is refused
    # by the brief generator (see `_checked_fixup_brief`), with a message of its own.
    uncommitted = [path for path in _uncommitted_paths(step.vcs, step.worktree) if path not in leftovers]
    if uncommitted and _head(step.vcs, step.worktree) != rejected:
        raise _StepFailed(_uncommitted_reason(uncommitted) + f"; no gate ran (after the fix-up of round {round_number})")
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
        label for gate in step.stack.gates
        if (label := _run_gate(
            step.gates, step.vcs, gate, step.ticket.id, step.worktree, step.journal, round_number, PHASE_FIXUP, step.stack.gate_timeout,
            step.stack.gate_names.get(gate),
        ))
    ]
    if red:
        raise _StepFailed("gates red: " + "; ".join(red))


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
    earlier_fixups: tuple[str, str] | None = None,
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
    """
    ticket = step.ticket
    where = f"stack {ticket.stack}" if step.segments is not None else f"ticket {ticket.id}"
    _pause_point(step.journal, ticket.id, PAUSED_BEFORE_VERIFIER, f"before the verifier of {where}", round=round_number, **step.chain_fields)
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
            # Each verifier brief holds the verifier notes of the stack after the gates, before the working copy.
            notes = notes_section(step.stack.verifier_notes)
            if step.segments is not None:
                # A chain: the brief holds the tickets of the stack, each with its own diff, and the finding labels.
                first_pass = (
                    chain_brief(step.vcs, copy, step.segments, step.stack.gates, gates_text, fixups, earlier_fixups)
                    + plan_change_section(_plan_change_note(step.journal, step.workflow, [s.ticket for s in step.segments], verifier=True))
                    if fixup_body is None else fixup_body + finding_labels_section()
                )
                prompt = first_pass + notes + verifier_run_sections(copy, report_path, mode, engine_gates=gates_text is not None)
            elif fixup_body is None:
                prompt = verifier_run_brief(
                    step.vcs, copy, step.branch, step.workflow.run_branch, ticket.text, step.stack.gates, report_path,
                    plan_change=_plan_change_note(step.journal, step.workflow, [ticket.id], verifier=True),
                    notes=step.stack.verifier_notes, gates_text=gates_text,
                )
            else:
                prompt = fixup_body + notes + verifier_run_sections(copy, report_path, mode, engine_gates=gates_text is not None)
        except (EngineError, BriefError) as error:
            raise _StepFailed(f"the verifier could not start: {error}") from None
        # The proof copy comes after `prepare`, so it holds what the commands made in the copy.
        _run_prepare(step.gates, step.vcs, step.journal, step.stack, ticket.id, TREE_VERIFY_COPY, copy, round_number)
        sealed = _check_agent_file(step.journal, step.stack.verifier, ticket.id, ROLE_VERIFIER, round_number)
        added = _make_proof_copy(copy)
        # The snapshot goes in `verify-start`, so a resume after a stop in the session can still compare the worktree (#192).
        before = snapshot_worktree(step.vcs, step.worktree)
        step.journal.append(
            "verify-start", ticket=ticket.id, round=round_number, agent=step.stack.verifier, tier=tier, model=model,
            commit=verified, copy=str(copy), snapshot=before.to_record(), **step.chain_fields,
        )
        # The stream goes in the evidence directory from the start, outside the copy, so it stays when the copy goes.
        stream = _start_agent(step.journal, ticket.id, ROLE_VERIFIER, round_number, evidence / report_path.name)
        # The check reads the report where the verifier writes it, in the scratch directory; `report_path` moves later.
        in_scratch = report_path
        report_valid = _report_check(lambda: read_verifier_report(in_scratch, mode))
        result = _run_agent(
            step.adapter, step.journal, ticket.id, ROLE_VERIFIER, round_number,
            _request(
                step.stack.verifier, step.stack, model, prompt, copy, report_path, stream, report_valid, added_directories=added,
                sealed=sealed, environment=_worktree_environment(step.vcs, step.worktree),
            ),
        )
        # The check runs at once, before the evidence is moved; the journal and the halt come after it.
        violation: InvariantViolation | None = None
        try:
            check_worktree_unchanged(step.vcs, before)
        except InvariantViolation as found:
            violation = found
        # The copy and the second copy of a red proof go; the verdict report, the event stream and the other files
        # of the verifier stay as evidence of the run.
        evidence.mkdir(parents=True, exist_ok=True)
        for entry in scratch.iterdir():
            if entry not in (copy, red_proof_copy(copy)):
                shutil.move(str(entry), evidence / entry.name)
    finally:
        step.vcs.stop_file_watcher(scratch / "copy")
        shutil.rmtree(scratch, ignore_errors=True)
    report_path = evidence / report_path.name
    stream = result.event_stream
    if stream.is_relative_to(scratch):
        stream = evidence / stream.relative_to(scratch)
    _journal_idle(step.journal, result, ticket.id, ROLE_VERIFIER, round_number)
    _journal_timeout(step.journal, result, step.stack, ticket.id, ROLE_VERIFIER, round_number)
    step.journal.append(
        "verify-result", ticket=ticket.id, round=round_number, exit_status=result.exit_status, end_state=result.end_state,
        session_id=result.session_id, event_stream=str(stream), **_refused_fields(result),
        **_totals_fields(step.adapter, stream),
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
        raise _StepFailed(_ended_reason(result, "the verifier", step.stack))
    try:
        report = read_verifier_report(report_path, mode)
    except ReportError as error:
        step.journal.append(
            "verify-report", ticket=ticket.id, round=round_number, valid=False, path=str(report_path), reason=str(error),
            **source,
        )
        raise _StepFailed(str(error)) from None
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


def _advance_run_branch(step: _Step, verified: str) -> str | None:
    """Move the run branch to the commit that the verifier accepted, by fast-forward only.

    Return None when it moved, or the reason the step fails.
    """
    run_branch = step.workflow.run_branch
    _check_run_branch(step.journal, step.vcs, step.repo, run_branch, step.ticket.id)
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
    - The fix-up phase (the agent check stopped the step before the fix-up session) runs that fix-up again, with the
      gates. On the tip of the run branch, the scoped verifier starts. If the run branch moved, the engine rebases and
      starts a full verifier at the round of the plan.

    In `economy` mode the step goes on as an open step does after a stop, at
    the specialist or at the check of its report (see `_build_ticket`). The phase `chain-fixup` builds nothing: it writes
    `retry` and returns, and the chain verification runs the stopped fix-up again (see `_ChainRun`).

    The phase `fixup` of `assure` mode runs the stopped fix-up again (see `_stopped_before_fixup`), with the same rejected
    commit and the same findings, then goes on to the gates and the scoped verifier of that round.

    A plan with `restart` starts the ticket again from its base (see `_restart_step`).
    """
    workflow, ticket, vcs = step.workflow, step.ticket, step.vcs
    if plan.restart:
        return _restart_step(step, failure, start_ref)
    if plan.phase == RETRY_CHAIN_FIXUP:
        # No work of this step is left: the chain verification runs the stopped fix-up again (see `_ChainRun`).
        step.journal.append(
            "retry", ticket=ticket.id, phase=plan.phase, round=plan.round, reason=failure, revived=list(plan.revive)
        )
        return None
    if workflow.mode == domain.ECONOMY or plan.phase == RETRY_SPECIALIST:
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
    if plan.phase == RETRY_FIXUP and fixup_held:
        goes_on = RETRY_FIXUP  # the fix-up of the round runs first, whether the branch holds the run branch tip or not
    step.journal.append("retry", ticket=ticket.id, phase=goes_on, round=plan.round, reason=failure)
    made = _prepare_worktree(vcs, step.repo, step.worktree, step.branch, start_ref, step.state_dir / "hooks" / step.run_id / ticket.id)
    try:
        _prepare_ticket_worktree(step.gates, step.vcs, step.journal, step.stack, ticket.id, step.worktree, made)
        if plan.phase == RETRY_FIXUP:
            # The branch holds no fix-up commit: the stop came before the fix-up session. Run that fix-up again.
            if plan.rejected is None or not fixup_held:
                return f"the branch {step.branch} no longer holds the rejected commit of round {plan.round}, so its fix-up cannot run again"
            # On the rejected commit, with the findings of the REJECT: not a new round.
            findings_text = _fixup_round(step, plan.round, plan.rejected, list(plan.findings))
            if on_tip:
                return _advance_run_branch(step, _accepted_commit(step, plan.round, (plan.rejected, findings_text)))
            # The run branch moved after the stop: rebase the branch with its fix-up, then a full verifier, as for any other phase.
            reason = _rebase_onto_run_branch(step)
            if reason:
                return reason
            return _advance_run_branch(step, _accepted_commit(step, plan.round))
        if plan.rejected is not None and fixup_held:
            findings_text = _findings_text(list(plan.findings))
            _checked_fixup_brief(step, plan.round, plan.rejected, findings_text)
            if on_tip:
                if plan.phase == RETRY_GATES:
                    _fixup_gates(step, plan.round)
                return _advance_run_branch(step, _accepted_commit(step, plan.round, (plan.rejected, findings_text)))
        elif goes_on == RETRY_VERIFIER:
            return _advance_run_branch(step, _accepted_commit(step, plan.round))
        reason = _rebase_onto_run_branch(step)
        if reason:
            return reason
        return _advance_run_branch(step, _accepted_commit(step, plan.round))
    except _StepFailed as failed:
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
    rounds of the old step do not count (see `_ticket_events`). The `retry` event
    has `restart` and `failed_branch`, and a new `step-start` follows it.
    """
    ticket, vcs, repo = step.ticket, step.vcs, step.repo
    branch = step.branch
    with _vcs_errors():
        number = 1
        while vcs.branch_exists(repo, f"{branch}-failed-{number}"):
            number += 1
        failed_branch = f"{branch}-failed-{number}"
        step.journal.append(
            "retry", ticket=ticket.id, phase=RETRY_SPECIALIST, round=0, reason=failure, restart=True, failed_branch=failed_branch
        )
        if step.worktree.is_dir():
            vcs.remove_worktree(repo, step.worktree)
        shutil.rmtree(step.state_dir / "hooks" / step.run_id / ticket.id, ignore_errors=True)
        vcs.prune_worktrees(repo)
        if vcs.branch_exists(repo, branch):
            # The old branch gets its new name first, so no commit is ever without a branch.
            vcs.create_branch(repo, failed_branch, branch)
            vcs.discard_branch(repo, branch)
    _archive_old_step(step.run_dir, ticket.id, number)
    return _build_ticket(
        step.workflow, ticket, step.adapter, vcs, step.gates, step.roles, repo, step.state_dir, step.run_id, step.journal, start_ref
    )


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


class _ChainRedo(NamedTuple):
    """The chain fix-up that the agent check stopped, for a retry to run it again (#209).

    `round`, `commit` (the commit that the fix-up starts from) and `findings_text` are those of the stopped fix-up, and
    `red_gates` tells that red gates started it. `rejected` and `findings` are the state of the verification that the
    stop cut off: the commit that the last verifier rejected, and its findings, or None and no finding.
    """

    round: int
    commit: str
    findings_text: str
    red_gates: bool
    rejected: str | None
    findings: list[str]


def _chain_redo(events: list[dict[str, object]], ticket: str, stack: str) -> _ChainRedo | None:
    """The fix-up of `stack` that the agent check stopped before its session, when a retry at the chain fix-up follows, else None.

    `events` are the journal events since the last retry that is not a retry at the chain fix-up. `ticket` ends the stack.
    The stop is the newest `agents-changed` of that ticket, for the role `fix-up`, with no `agent-start` after it, and a
    `retry` with the phase `chain-fixup` after it (see `_chain_stop`). The event holds the commit, the findings file and the
    trigger of the stopped fix-up. The state of the verifier comes from the last `verdict` of the stack.
    """
    own = [(index, e) for index, e in enumerate(events) if e.get("ticket") == ticket]
    stops = [(index, e) for index, e in own if e["event"] == "agents-changed" and e["role"] == ROLE_FIXUP]
    if not stops:
        return None
    index, stop = stops[-1]
    after = [e for _, e in own if _ > index]
    if any(e["event"] == "agent-start" for e in after) or not any(
        e["event"] == "retry" and e.get("phase") == RETRY_CHAIN_FIXUP for e in after
    ):
        return None
    verdicts = [i for i, e in enumerate(events[:index]) if e["event"] == "verdict" and e.get("stack") == stack]
    rejected: str | None = None
    findings: list[str] = []
    if verdicts and not domain.is_accepted(str(events[verdicts[-1]]["verdict"])):
        starts = [e for e in events[: verdicts[-1]] if e["event"] == "verify-start" and e.get("stack") == stack]
        rejected, findings = str(starts[-1]["commit"]), [str(f) for f in events[verdicts[-1]]["findings"]]  # type: ignore[union-attr]
    return _ChainRedo(
        int(stop["round"]), str(stop["rejected_commit"]), Path(str(stop["findings_file"])).read_text(),  # type: ignore[call-overload]
        stop["trigger"] == TRIGGER_GATES_RED, rejected, findings,
    )


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
    `retry` or `plan-change`. So after a retry or a plan change, the chain
    verification of the run before it does not count, and the tickets that it
    accepted are not verified again. A ticket that a plan change added after
    the chain verification gets its own gates and verifier.
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
            # The clean-up of a run that ended `built` deletes only a ticket branch that the run branch holds (see `_clean_up`).
            self.chain = [
                ticket for ticket in self.chain
                if self.vcs.branch_exists(self.repo, _ticket_branch(self.workflow, ticket))
                and not _holds(self.vcs, self.repo, f"refs/heads/{_ticket_branch(self.workflow, ticket)}", run_branch)
            ]
            if not self.chain:
                return []  # the run branch holds the work of every built ticket
            return self._verify_stacks()
        except (_Paused, _Halted):
            raise  # a pause and a halt are no failure of the chain: `_execute` ends the run process with no `run-end`
        except Exception as error:  # noqa: BLE001 - the journal records it and the command reports it
            reason = _halt_reason(error)
            self._fail({ticket.id: reason for ticket in self.chain if ticket.id in self.built}, error)
            return []

    def _fail(self, reasons: dict[str, str], error: Exception | None = None) -> None:
        """Fail the built tickets in `reasons`. The newest `step-end` of a ticket wins, so the journal keeps both.

        With the `error` that ended the verification, each `step-end` of a crash names its own file of the traceback.
        """
        for ticket_id, reason in reasons.items():
            fields = {} if error is None else _crash_fields(self.journal, error)
            self.journal.append("step-end", ticket=ticket_id, state=FAILED, reason=reason, **fields)
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
        _check_run_branch(self.journal, self.vcs, self.repo, self.workflow.run_branch, self.chain[-1].id)
        every_event = read_events(self.journal.path)
        # A retry at the chain fix-up (#209) goes on with the verification: its rounds and its verdicts still count.
        retries = [
            index for index, e in enumerate(every_event)
            if e["event"] == "plan-change" or (e["event"] == "retry" and e.get("phase") != RETRY_CHAIN_FIXUP)
        ]
        # The verification of the chain since the last retry or plan change.
        events = every_event[retries[-1] + 1 :] if retries else every_event
        if any(e["event"] == "run-branch-advance" for e in events):
            return []  # a resume after the run branch moved: nothing is left to verify
        tip_ticket = self.chain[-1]
        _check_verifier_stop(
            self.journal, self.vcs, self.state_dir / "worktrees" / self.run_id / tip_ticket.id, tip_ticket.id, events
        )
        segments = self._segments(every_event, events)
        # The chain tip before the first fix-up of any stack. The fix-ups from here to the start of a stack are the
        # fix-ups of the stacks before it, and its full verifier sees those that change a file of its tickets (#93).
        chain_start = segments[tip_ticket.id].tip
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
                self._verify_stack(
                    step, [e for e in events if e["event"] == "fixup-start" and e.get("stack") == name], chain_start,
                    _chain_redo(events, tickets[-1].id, name),
                )
            except _StepFailed as failed:
                return self._reject(failed, tickets, stacks[position + 1 :])
        assert last is not None
        reason = _advance_run_branch(last, _tip(self.vcs, self.repo, last.branch))
        if reason:
            self._fail({ticket.id: reason for ticket in self.chain})
        return []

    def _verify_stack(
        self, step: _Step, fixup_starts: list[dict[str, object]], chain_start: str, redo: _ChainRedo | None = None
    ) -> None:
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

        The full verifier also sees the fix-up commits of the stacks before this
        stack, from `chain_start` to the start of this stack, when they change a
        file of a ticket of this stack (see `chain_brief`).

        A retry after the agent check stopped a fix-up session gives that fix-up in `redo` (#209). The stop wrote no
        `fixup-start`, so the round is not in `fixup_starts`. The verification first runs that fix-up again, on the same
        commit with the same findings, as the round that the stop cut off, and then goes on as after any fix-up.
        """
        mode = self.workflow.mode
        limit = domain.fixup_round_limit(mode)
        round_number = len(fixup_starts)
        used_up = domain.rounds_used_up_reason(mode, round_number)
        if used_up:
            raise _StepFailed(used_up)
        # The full verifier also sees each fix-up commit beyond this commit: the tip before the first fix-up of the stack.
        start = str(fixup_starts[0]["rejected_commit"]) if fixup_starts else _tip(self.vcs, self.repo, step.branch)
        earlier = (chain_start, start) if chain_start != start else None
        rejected: str | None = None  # the commit that the last verifier rejected
        findings: list[str] = []
        if redo is not None:
            rejected, findings, round_number = redo.rejected, redo.findings, redo.round
            self._fixup(step, round_number, redo.commit, redo.findings_text, redo.red_gates)
        while True:
            tip = _tip(self.vcs, self.repo, step.branch)
            outcomes = [
                _run_gate_with_output(
                    step.gates, step.vcs, gate, step.ticket.id, step.worktree, step.journal, round_number, PHASE_CHAIN, step.stack.gate_timeout,
                    step.stack.gate_names.get(gate),
                )
                for gate in step.stack.gates
            ]
            red = [outcome for outcome in outcomes if not outcome.green]
            if red:
                if round_number >= limit:
                    raise _StepFailed(domain.chain_gates_reason(mode, [outcome.label for outcome in red]))
                round_number += 1
                self._fixup(step, round_number, tip, _red_gates_text(tip, [(outcome.command, outcome.output) for outcome in red]), True)
                continue
            gates_text = green_gates_text(tip, [(outcome.command, outcome.output) for outcome in outcomes])
            if rejected is None:
                report = _verify_round(step, round_number, None, gates_text, (start, tip) if start != tip else None, earlier)
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


class _GateRun(NamedTuple):
    """The outcome of one gate for the engine: whether it is green, its output, and the name that a reason gives it.

    `label` is the name of the gate, or its command when it has no name, and a gate that the runner ended at its time limit has `(timed out)` after it.
    """

    command: str
    green: bool
    output: str
    label: str


def _run_gate(
    gates: GateRunner, vcs: VersionControl, command: str, ticket: str, worktree: Path, journal: Journal, round_number: int, phase: str,
    gate_timeout: float | None = None, name: str | None = None,
) -> str | None:
    """Run one gate command in the worktree through `gates`, and record the result. Return None when it is green, else its label.

    `round_number` is 0 for the first build and the fix-up round for a later run.
    `phase` is one of PHASE_BUILD, PHASE_REBASE and PHASE_FIXUP.
    `gate_timeout` is the time limit of the gate in minutes, or None (see `_run_gate_with_output`).
    `name` is the name that the workflow gives to the gate, or None (see `_run_gate_with_output`).
    """
    outcome = _run_gate_with_output(gates, vcs, command, ticket, worktree, journal, round_number, phase, gate_timeout, name)
    return None if outcome.green else outcome.label


def _run_gate_with_output(
    gates: GateRunner, vcs: VersionControl, command: str, ticket: str, worktree: Path, journal: Journal, round_number: int, phase: str,
    gate_timeout: float | None = None, name: str | None = None,
) -> _GateRun:
    """Run one gate command as `_run_gate` does. Return its outcome: whether it is green, and the tail of its output.

    `phase` can also be PHASE_CHAIN: the gates on the tip of an `economy` chain.

    `gate_timeout` is the time limit of the gate in minutes (#179). A gate that runs longer gets its process group ended by
    the runner. The result then has `timed_out`, and it is red. The engine writes `gate-result` with `green` false and
    `timed_out` true, and ends the output with a line that names the limit, so the specialist and a reader see the cause.
    The step goes on as for any red gate.

    A gate runs at most once green on one commit. When the journal holds a green
    `gate-result` of the same command on the commit that the worktree has
    checked out, the engine does not run the gate again: it writes `gate-skip`
    with that commit and the `seq` of the green result, and the gate counts as
    green. A rebase that keeps the commit, and a resume after a stop, so cost no
    second gate run. The output of a skipped gate is the output of that green result.

    `name` is the name that the workflow gives to the gate (#223), or None. A gate with a name has `name` in its
    `gate-start`, `gate-result` and `gate-skip`, and in the `paused` event before it, and the reason of a red gate gives
    the name in place of the command. A gate with no name has no such field.

    Before a gate runs, the engine makes an empty log file for it and writes
    `gate-start` with the path of the file. The gate runner appends the output
    to the file while the gate runs, so `delegate status` and `delegate watch`
    see the activity of a long gate.
    """
    commit = _head(vcs, worktree)
    named: dict[str, object] = {} if name is None else {"name": name}
    shown = name or command
    green = _green_result(journal, command, commit)
    if green is not None:
        green_seq = int(green["seq"])  # type: ignore[call-overload]
        journal.append(
            "gate-skip", ticket=ticket, round=round_number, phase=phase, command=command, commit=commit, green_seq=green_seq,
            reason=f"the journal holds a green result of this gate on commit {commit} (seq {green_seq})", **named,
        )
        return _GateRun(command, True, str(green.get("output_tail", "")), shown)
    _pause_point(journal, ticket, PAUSED_BEFORE_GATE, f"before the gate `{shown}` of ticket {ticket}", command=command, phase=phase, **named)
    log = _gate_log_path(journal, ticket, phase, round_number)
    journal.append("gate-start", ticket=ticket, round=round_number, phase=phase, command=command, gate_log=str(log), **named)
    result = gates.run(command, worktree, log=log, timeout=_seconds(gate_timeout), environment=_worktree_environment(vcs, worktree))
    output = result.output[-GATE_OUTPUT_TAIL_CHARS:]
    if result.timed_out:
        output += f"\n[delegate: the gate ran past the gate-timeout of {_minutes_text(gate_timeout)} minutes, and the engine ended it]\n"
    journal.append(
        "gate-result", ticket=ticket, round=round_number, phase=phase, command=command, commit=commit,
        exit_status=result.exit_status, green=result.green, timed_out=result.timed_out, output_tail=output, clean=True, **named,
    )
    return _GateRun(command, result.green, output, f"{shown} (timed out)" if result.timed_out else shown)


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
    """The first green `gate-result` of `command` on `commit` in the journal, or None when it holds none.

    Only a result with `clean: true` counts: the worktree had no change that was
    not committed when the gate ran (#176). A result of an older journal has no
    such field, so it skips no gate.
    """
    for event in read_events(journal.path):
        if (
            event["event"] == "gate-result" and event["green"] and event.get("clean") is True
            and event["command"] == command and event.get("commit") == commit
        ):
            return event
    return None
