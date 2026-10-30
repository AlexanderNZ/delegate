"""The journal schema: the name and the fields of each event, and the version of the schema (#189).

The journal of a run is a public interface: tools outside the kit read it. This module is the one place that names
each event and its fields. `docs/reference/run.md` holds the same names in its journal table, and
`tests/test_run_events.py` checks that the two agree in both directions.

Every line of a journal also has `seq`, `time` and `event`. The `TypedDict` of an event holds only its other fields.
A field with `NotRequired` is not in every line of the event: the reference says when it is.

`SCHEMA_VERSION` is the version of the schema. `run-start` writes it as `schema`. A new optional field does not change
it. A field that is removed or changed does. A journal with no `schema` in `run-start` is version 0.

The readers of the journal still read the fields by their string key. They do not use this module yet.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

SCHEMA_VERSION: int = 1

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
PLAN_CHANGE: str = "plan-change"
RETRY: str = "retry"
LOCK_BROKEN: str = "lock-broken"
PAUSE_REQUESTED: str = "pause-requested"
PAUSED: str = "paused"
HALTED: str = "halted"
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


class GateSkip(TypedDict):
    ticket: str
    round: int
    phase: str
    command: str
    commit: str
    green_seq: int
    reason: str


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


class PlanChange(TypedDict):
    added: list[str]
    changed: list[str]
    fields: dict[str, list[str]]
    removed: list[str]
    order: list[str]
    reviewed: list[str]
    plan: list[PlanTicket]


class Retry(TypedDict):
    ticket: str
    phase: str
    round: int
    reason: str


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


class Halted(TypedDict):
    ticket: str
    role: str
    round: int
    message: str


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
    # Only the `step-end` of a crash has it: the path of the file that holds the traceback (#194).
    traceback: NotRequired[str]


class Cleanup(TypedDict):
    worktrees_removed: list[str]
    branches_deleted: list[str]
    worktrees_kept: list[KeptWorktree]
    branches_kept: list[KeptBranch]
    # Only the event that `delegate clean` writes after `run-end` has these.
    copies_removed: NotRequired[int]
    tracebacks_removed: NotRequired[list[str]]
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
    PLAN_CHANGE: PlanChange,
    RETRY: Retry,
    LOCK_BROKEN: LockBroken,
    PAUSE_REQUESTED: PauseRequested,
    PAUSED: Paused,
    HALTED: Halted,
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
