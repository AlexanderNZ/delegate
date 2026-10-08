"""The workflow engine: build each ticket through a harness adapter.

For each ticket the engine makes a worktree from the base branch, spawns the
stack's specialist through the adapter, checks the specialist's report, runs
the stack's gates itself, and records each event in the journal.

In `assure` mode the engine then verifies the built branch. It makes a
temporary copy of the branch, builds a full verifier brief with the brief
generator, spawns the stack's verifier on the verifier tier, and checks the
verdict report. On ACCEPT the engine fast-forwards the run branch to the
verified commit. On REJECT the engine starts a fix-up round: it writes the
findings to a file, sends the specialist a fix-up brief in the same worktree,
requires the fix as a new commit on top of the rejected commit, runs the gates,
and spawns a fresh verifier with the scoped brief (the findings and the delta
from the rejected commit). After `FIXUP_ROUND_LIMIT` rounds the step fails and
the run ends. The run branch moves only on ACCEPT.

Standard library and git through subprocess only.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import adapters
from .adapters import Adapter, AdapterRequest
from .brief import BriefError, fixup_brief, specialist_brief, verifier_run_brief, verifier_run_sections
from .journal import Journal
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

BUILT: str = "built"
FAILED: str = "failed"


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


def run_workflow(workflow: Workflow, workflow_path: Path, repo: Path, tiers: Tiers) -> RunResult:
    """Build the tickets of a workflow in dependency order. Return the result of the run."""
    try:
        adapter = adapters.get(workflow.adapter)
    except KeyError:
        raise EngineError(
            f"adapter {workflow.adapter!r} has no implementation yet; registered: {', '.join(adapters.registered_names()) or 'none'}"
        ) from None
    repo = Path(_git(repo, "rev-parse", "--show-toplevel"))
    state_dir = (repo / _git(repo, "rev-parse", "--git-common-dir")).resolve() / "delegate"
    _check_branches(workflow, repo)
    needed = ("specialist", "verifier") if workflow.mode == "assure" else ("specialist",)
    roles = {role: _role_model(workflow, tiers, adapter.tier_column, role) for role in needed}
    _git(repo, "branch", workflow.run_branch, workflow.base_branch)

    run_id = _new_run_id()
    journal = Journal(state_dir / "runs" / run_id / "journal.jsonl")
    order = plan_order(workflow)
    journal.append(
        "run-start", run_id=run_id, workflow=str(workflow_path), mode=workflow.mode, adapter=workflow.adapter,
        base_branch=workflow.base_branch, run_branch=workflow.run_branch, tickets=[t.id for t in order],
    )
    built: list[str] = []
    failed: list[str] = []
    failures: dict[str, str] = {}
    for ticket in order:
        reason = _build_ticket(workflow, ticket, adapter, roles, repo, state_dir, run_id, journal)
        journal.append("step-end", ticket=ticket.id, state=FAILED if reason else BUILT, reason=reason)
        if reason:
            failed.append(ticket.id)
            failures[ticket.id] = reason
            break
        built.append(ticket.id)
    journal.append("run-end", result=BUILT if not failed else FAILED, built=built, failed=failed)
    return RunResult(run_id, journal.path, built, failed, failures)


def _build_ticket(
    workflow: Workflow, ticket: Ticket, adapter: Adapter, roles: dict[str, tuple[str, str]],
    repo: Path, state_dir: Path, run_id: str, journal: Journal,
) -> str | None:
    """Build one ticket. Return None when it is built, or the reason it failed.

    In `assure` mode a ticket is built only when its verifier accepts it.
    """
    tier, model = roles["specialist"]
    stack = workflow.stacks[ticket.stack]
    branch = _ticket_branch(workflow, ticket)
    worktree = state_dir / "worktrees" / run_id / ticket.id
    report_path = state_dir / "runs" / run_id / "reports" / f"{ticket.id}.specialist.json"
    base_commit = _git(repo, "rev-parse", workflow.base_branch)
    _git(repo, "worktree", "add", "-q", "-b", branch, str(worktree), workflow.base_branch)
    journal.append(
        "step-start", ticket=ticket.id, stack=ticket.stack, branch=branch, worktree=str(worktree),
        base_commit=base_commit, agent=stack.specialist, tier=tier, model=model,
    )
    result = adapter.run(AdapterRequest(
            stack.specialist, model,
            specialist_brief(ticket.id, ticket.text, worktree, stack.hotspots, stack.gates, report_path),
            worktree, report_path,
        ))
    journal.append(
        "adapter-result", ticket=ticket.id, exit_status=result.exit_status, end_state=result.end_state,
        session_id=result.session_id, event_stream=str(result.event_stream),
    )
    if result.end_state != adapters.FINISHED or result.exit_status != 0:
        return f"the specialist ended {result.end_state} with exit status {result.exit_status}"
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
    red = [gate for gate in stack.gates if not _run_gate(gate, ticket.id, worktree, journal, round_number=0)]
    if red:
        return "gates red: " + "; ".join(red)
    if workflow.mode == "assure":
        return _verify_ticket(_Step(workflow, ticket, adapter, roles, repo, state_dir, run_id, journal))
    return None


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


def _verify_ticket(step: _Step) -> str | None:
    """Verify the built branch of a ticket blind. Return None on ACCEPT, or the reason the step fails.

    A REJECT starts a fix-up round, up to `FIXUP_ROUND_LIMIT` rounds. Each
    round, and the first pass, spawns a fresh verifier.
    """
    try:
        rejected = ""
        report: VerifierReport | None = None
        for round_number in range(FIXUP_ROUND_LIMIT + 1):
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
    red = [gate for gate in step.stack.gates if not _run_gate(gate, ticket.id, step.worktree, step.journal, round_number)]
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


def _run_gate(command: str, ticket: str, worktree: Path, journal: Journal, round_number: int) -> bool:
    """Run one gate command in the worktree, record the result, and return True when it is green.

    `round_number` is 0 for the first build and the fix-up round for a later run.
    """
    r = subprocess.run(["bash", "-c", command], cwd=worktree, capture_output=True, text=True)
    output = (r.stdout + r.stderr)[-GATE_OUTPUT_TAIL_CHARS:]
    journal.append(
        "gate-result", ticket=ticket, round=round_number, command=command, exit_status=r.returncode, green=r.returncode == 0,
        output_tail=output,
    )
    return r.returncode == 0
