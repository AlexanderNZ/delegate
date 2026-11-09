"""The journal schema: the name and the fields of each event, and the version of the schema (#189).

The journal of a run is a public interface: tools outside the kit read it. This module is the one place that names
each event and its fields. `docs/reference/run.md` holds the same names in its journal table, and
`tests/test_run_events.py` checks that the two agree in both directions.

Every line of a journal also has `seq`, `time` and `event`. The `TypedDict` of an event holds only its other fields.
A field with `NotRequired` is not in every line of the event: the reference says when it is.

`SCHEMA_VERSION` is the version of the schema. `run-start` writes it as `schema`. A new optional field does not change
it. A field that is removed or changed does. A journal with no `schema` in `run-start` is version 0. Version 2 changes the
field `agents` of `run-start` (#217): the record of a seal replaces the record of an agent file.

The readers of the journal still read the fields by their string key. They do not use this module yet.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

SCHEMA_VERSION: int = 2

# The version of a journal with no `schema` in `run-start`: a journal that an earlier version of the kit wrote.
UNVERSIONED: int = 0

# The names of the events.
RUN_START: str = "run-start"
STEP_START: str = "step-start"
AGENT_START: str = "agent-start"
ADAPTER_RESULT: str = "adapter-result"
IDLE_TIMEOUT: str = "idle-timeout"
AGENT_TIMEOUT: str = "agent-timeout"
REPORT_VALIDATION: str = "report-validation"
HOTSPOT_FINDING: str = "hotspot-finding"
GATE_START: str = "gate-start"
GATE_RESULT: str = "gate-result"
GATE_SKIP: str = "gate-skip"
PREPARE_START: str = "prepare-start"
PREPARE_RESULT: str = "prepare-result"
CONTINUATION: str = "continuation"
CONTINUATION_LIMIT: str = "continuation-limit"
RESUME: str = "resume"
AGENTS_CHANGED: str = "agents-changed"
GUARD_PROBE: str = "guard-probe"
PLAN_CHANGE: str = "plan-change"
RETRY: str = "retry"
LOCK_BROKEN: str = "lock-broken"
PAUSE_REQUESTED: str = "pause-requested"
PAUSED: str = "paused"
HALTED: str = "halted"
CRASHED: str = "crashed"
INTERRUPTED: str = "interrupted"
SKIP: str = "skip"
REBASE: str = "rebase"
VERIFY_START: str = "verify-start"
VERIFY_RESULT: str = "verify-result"
INVARIANT_VIOLATION: str = "invariant-violation"
VERIFY_REPORT: str = "verify-report"
VERDICT: str = "verdict"
FIXUP_START: str = "fixup-start"
FIXUP_RESULT: str = "fixup-result"
FIXUP_REPORT: str = "fixup-report"
FIXUP_REFUSED: str = "fixup-refused"
RUN_BRANCH_ADVANCE: str = "run-branch-advance"
RUN_BRANCH_MOVED: str = "run-branch-moved"
STEP_END: str = "step-end"
CLEANUP: str = "cleanup"
JOURNAL_REPAIRED: str = "journal-repaired"
RUN_END: str = "run-end"


class PlanTicket(TypedDict):
    """One ticket of a plan, in `run-start` and in `plan-change`."""

    id: str
    stack: str
    blocked_by: list[str]
    text_sha256: str
    tier: NotRequired[str]


class Refusal(TypedDict):
    """A tool that a permission mode of the harness refused."""

    tool: str
    mode: str


class HotspotMatch(TypedDict):
    path: str
    pattern: str


class KeptWorktree(TypedDict):
    worktree: str
    reason: str


class KeptBranch(TypedDict):
    branch: str
    reason: str


class SealedFileRecord(TypedDict):
    """One file of a seal: its path, and the SHA-256 of its content."""

    path: str
    sha256: str


class SealedAgentRecord(TypedDict):
    """One sealed agent of a record (#217): the agent, the source path of its agent file, the seal directory, the handle, and the files of the seal."""

    agent: str
    # None when the adapter names no source file.
    source: str | None
    seal: str
    handle: str
    files: list[SealedFileRecord]


class RunStart(TypedDict):
    run_id: str
    workflow: str
    mode: str
    adapter: str
    base_branch: str
    base_commit: str
    run_branch: str
    tickets: list[str]
    plan: list[PlanTicket]
    # Absent in a journal of version 0.
    schema: NotRequired[int]
    # The sealed agents of the roles of the run, from the main checkout. In a journal of version 1 the field holds the record of #205: the agent, the path and the SHA-256 of each agent file. Absent in a journal of version 0.
    agents: NotRequired[list[SealedAgentRecord]]


class StepStart(TypedDict):
    ticket: str
    stack: str
    branch: str
    worktree: str
    base_commit: str
    agent: str
    tier: str
    model: str


class AgentStart(TypedDict):
    ticket: str
    role: str
    round: int
    event_stream: str


class AdapterResult(TypedDict):
    ticket: str
    exit_status: int
    end_state: str
    session_id: str | None
    event_stream: str
    refused: NotRequired[list[Refusal]]
    tokens_new: int
    tokens_cache: int
    last_event: str | None


class IdleTimeout(TypedDict):
    ticket: str
    role: str
    round: int
    seconds: float


class AgentTimeout(TypedDict):
    ticket: str
    role: str
    round: int
    seconds: float


class ReportValidation(TypedDict):
    ticket: str
    valid: bool
    path: str
    reason: str | None
    status: str | None
    blocked_reason: str | None
    lenient_parse: NotRequired[bool]


class HotspotFinding(TypedDict):
    ticket: str
    phase: str
    round: int
    matches: list[HotspotMatch]


class GateStart(TypedDict):
    ticket: str
    round: int
    phase: str
    command: str
    gate_log: str
    name: NotRequired[str]


class GateResult(TypedDict):
    ticket: str
    round: int
    phase: str
    command: str
    commit: str
    exit_status: int
    green: bool
    timed_out: bool
    output_tail: str
    clean: bool
    changed: NotRequired[list[str]]
    name: NotRequired[str]


class GateSkip(TypedDict):
    ticket: str
    round: int
    phase: str
    command: str
    commit: str
    green_seq: int
    reason: str
    name: NotRequired[str]


class PrepareStart(TypedDict):
    ticket: str
    round: int
    tree: str
    path: str
    command: str
    prepare_log: str


class PrepareResult(TypedDict):
    ticket: str
    round: int
    tree: str
    path: str
    command: str
    prepare_log: str
    exit_status: int


class Continuation(TypedDict):
    ticket: str
    count: int
    limit: int
    trigger: str
    mode: str
    resume_session: str | None
    commits: list[str]
    reason: str


class ContinuationLimit(TypedDict):
    ticket: str
    count: int
    limit: int
    trigger: str


class Resume(TypedDict):
    run_id: str
    built: list[str]
    failed: list[str]
    skipped: list[str]
    open: str | None
    # Only a resume with `--agents-changed`, or a resume of a journal with no record of seals (version 0 or 1), has these
    # (#205, #217): the new record of the seals, and the note that says that the journal had none.
    agents: NotRequired[list[SealedAgentRecord]]
    agents_note: NotRequired[str]


class AgentsChanged(TypedDict):
    ticket: str
    role: str
    round: int
    tree: str
    agent: str
    path: str
    recorded_hash: str
    # None when the file of the seal is missing.
    new_hash: str | None
    # Only for the role `fix-up`: the fix-up that did not start, so that a retry runs it again (#209).
    rejected_commit: NotRequired[str]
    findings_file: NotRequired[str]
    # Only for a stop before a chain fix-up of the verification of `economy` mode (#209).
    stack: NotRequired[str]
    tickets: NotRequired[list[str]]
    # Only for the role `fix-up`: `gates-red` or `reject`. A journal of an earlier version has it only for a chain fix-up (#277).
    trigger: NotRequired[str]


class GuardProbe(TypedDict):
    """The result of a guard probe (#219): the verifier, its handle, `denied`, `ran` or `inconclusive`, and the event stream of the session."""

    agent: str
    handle: str
    result: str
    event_stream: str


class PlanChange(TypedDict):
    added: list[str]
    changed: list[str]
    fields: dict[str, list[str]]
    removed: list[str]
    order: list[str]
    reviewed: list[str]
    plan: list[PlanTicket]
    # Only when a retry starts a failed ticket again with a new text (#202): the old and the new `text_sha256` of each such ticket.
    retry_text: NotRequired[dict[str, dict[str, str]]]


class Retry(TypedDict):
    ticket: str
    phase: str
    round: int
    reason: str
    # Only with `phase` `chain-fixup` (#209): the other tickets that the stop failed and that the retry builds again. With
    # `phase` `chain` (#225): the other tickets that the verification of the chain failed and that the retry builds again.
    revived: NotRequired[list[str]]
    # Only with `--coordinator-commit` (#225): the commit of the coordinator at the tip of the branch, and the paths that it
    # changes. The hotspot check of the step does not count them.
    coordinator_commit: NotRequired[str]
    coordinator_paths: NotRequired[list[str]]
    # Only when the retry changes the text of the ticket and starts it again from its base (#204): `restart` is true,
    # and `failed_branch` is the branch that keeps the old work.
    restart: NotRequired[bool]
    failed_branch: NotRequired[str]


class LockBroken(TypedDict):
    run_id: str
    pid: int


class PauseRequested(TypedDict):
    ticket: str
    requested_at: str | None


class Paused(TypedDict):
    ticket: str
    before: str
    command: NotRequired[str]
    phase: NotRequired[str]
    round: NotRequired[int]
    stack: NotRequired[str]
    tickets: NotRequired[list[str]]
    name: NotRequired[str]


class Halted(TypedDict):
    ticket: str
    role: str
    round: int
    message: str


# The `phase` of a `crashed` event that is not a phase of a running step: a crash before the `step-start` of the step,
# and a crash of the verification of an `economy` chain. The other values are the phases of a step: `specialist`,
# `gates`, `verifier` and `fix-up`.
CRASH_PHASE_SETUP: str = "setup"
CRASH_PHASE_CHAIN: str = "chain"


class Crashed(TypedDict):
    ticket: str
    phase: str
    # The type of the exception, and its message.
    error: str
    message: str
    # The path of the file that holds the traceback (#194). Not there when the engine could not write the file.
    traceback: NotRequired[str]
    # Only for a crash of the step of a `--retry`: true. `--retry` goes on with that step again.
    retry: NotRequired[bool]


class Interrupted(TypedDict):
    run_id: str


class Skip(TypedDict):
    ticket: str
    blockers: list[str]
    reason: str


class Rebase(TypedDict):
    ticket: str
    result: str
    onto_commit: str
    from_commit: str
    to_commit: str | None
    files: list[str]


class VerifyStart(TypedDict):
    ticket: str
    round: int
    agent: str
    tier: str
    model: str
    commit: str
    copy: str
    snapshot: NotRequired[dict[str, object]]
    stack: NotRequired[str]
    tickets: NotRequired[list[str]]


class VerifyResult(TypedDict):
    ticket: str
    round: int
    exit_status: int
    end_state: str
    session_id: str | None
    event_stream: str
    tokens_new: int
    tokens_cache: int
    last_event: str | None
    refused: NotRequired[list[Refusal]]


class InvariantViolation(TypedDict):
    ticket: str
    round: int
    worktree: str
    head_before: str
    head_after: str
    changes: list[str]


class VerifyReport(TypedDict):
    ticket: str
    round: int
    valid: bool
    path: str
    reason: str | None
    source: NotRequired[str]
    lenient_parse: NotRequired[bool]


class Verdict(TypedDict):
    ticket: str
    round: int
    mode: str
    verdict: str
    findings: list[str]
    unverified: list[str]
    report: str
    stack: NotRequired[str]
    tickets: NotRequired[list[str]]
    mapped: NotRequired[dict[str, list[str]]]
    unmapped: NotRequired[list[str]]


class FixupStart(TypedDict):
    ticket: str
    round: int
    rejected_commit: str
    findings_file: str
    agent: str
    tier: str
    model: str
    stack: NotRequired[str]
    tickets: NotRequired[list[str]]
    trigger: NotRequired[str]


class FixupResult(TypedDict):
    ticket: str
    round: int
    exit_status: int
    end_state: str
    session_id: str | None
    event_stream: str
    tokens_new: int
    tokens_cache: int
    last_event: str | None
    refused: NotRequired[list[Refusal]]


class FixupReport(TypedDict):
    ticket: str
    round: int
    valid: bool
    path: str
    reason: str | None
    status: str | None
    blocked_reason: str | None
    lenient_parse: NotRequired[bool]


class FixupRefused(TypedDict):
    ticket: str
    round: int
    reason: str


class RunBranchAdvance(TypedDict):
    ticket: str
    run_branch: str
    from_commit: str
    to_commit: str
    recovered: NotRequired[bool]


class RunBranchMoved(TypedDict):
    ticket: str | None
    run_branch: str
    expected: str
    found: str


class StepEnd(TypedDict):
    ticket: str
    state: str
    reason: str | None
    # Only the `step-end` of a crash in a journal of an earlier version has it: the path of the file that holds the
    # traceback (#194). A crash now writes `crashed` (#281).
    traceback: NotRequired[str]


class Cleanup(TypedDict):
    worktrees_removed: list[str]
    branches_deleted: list[str]
    worktrees_kept: list[KeptWorktree]
    branches_kept: list[KeptBranch]
    # Only the event that `delegate clean` writes after `run-end` has these.
    copies_removed: NotRequired[int]
    tracebacks_removed: NotRequired[list[str]]
    # The scratch directory of the run that `delegate clean` removed, as a list of one path or none (#227).
    scratch_removed: NotRequired[list[str]]
    bytes_freed: NotRequired[int]


class JournalRepaired(TypedDict):
    bytes_removed: int


class RunEnd(TypedDict):
    result: str
    built: list[str]
    failed: list[str]
    skipped: list[str]
    # Only the `run-end` with the result `abandoned` has it (#201).
    reason: NotRequired[str]


# Every event name with the `TypedDict` of its fields.
EVENTS: dict[str, type] = {
    RUN_START: RunStart,
    STEP_START: StepStart,
    AGENT_START: AgentStart,
    ADAPTER_RESULT: AdapterResult,
    IDLE_TIMEOUT: IdleTimeout,
    AGENT_TIMEOUT: AgentTimeout,
    REPORT_VALIDATION: ReportValidation,
    HOTSPOT_FINDING: HotspotFinding,
    GATE_START: GateStart,
    GATE_RESULT: GateResult,
    GATE_SKIP: GateSkip,
    PREPARE_START: PrepareStart,
    PREPARE_RESULT: PrepareResult,
    CONTINUATION: Continuation,
    CONTINUATION_LIMIT: ContinuationLimit,
    RESUME: Resume,
    AGENTS_CHANGED: AgentsChanged,
    GUARD_PROBE: GuardProbe,
    PLAN_CHANGE: PlanChange,
    RETRY: Retry,
    LOCK_BROKEN: LockBroken,
    PAUSE_REQUESTED: PauseRequested,
    PAUSED: Paused,
    HALTED: Halted,
    CRASHED: Crashed,
    INTERRUPTED: Interrupted,
    SKIP: Skip,
    REBASE: Rebase,
    VERIFY_START: VerifyStart,
    VERIFY_RESULT: VerifyResult,
    INVARIANT_VIOLATION: InvariantViolation,
    VERIFY_REPORT: VerifyReport,
    VERDICT: Verdict,
    FIXUP_START: FixupStart,
    FIXUP_RESULT: FixupResult,
    FIXUP_REPORT: FixupReport,
    FIXUP_REFUSED: FixupRefused,
    RUN_BRANCH_ADVANCE: RunBranchAdvance,
    RUN_BRANCH_MOVED: RunBranchMoved,
    STEP_END: StepEnd,
    CLEANUP: Cleanup,
    JOURNAL_REPAIRED: JournalRepaired,
    RUN_END: RunEnd,
}


def schema_version(events: list[dict[str, object]]) -> int:
    """The schema version of a journal, from its events: the `schema` of `run-start`, or 0 when it has none."""
    if events and events[0].get("event") == RUN_START:
        version = events[0].get("schema", UNVERSIONED)
        if isinstance(version, int) and not isinstance(version, bool):
            return version
    return UNVERSIONED
