"""The plan of a `--retry`: where a failed step goes on, and the check of a commit of the coordinator (#339).

A resume with `retry` goes on with one failed step, also in a run that ended. `retry_plan` reads the journal events of the
step and says the first phase that did not complete (`RetryPlan`), or refuses the retry with an `EngineError`. The rules also
check a commit of the coordinator (#225, `check_coordinator_commit`), the hotspot check of the chain (`check_chain_hotspots`), and
the commits that an earlier retry recorded (`coordinator_commits`). `retry_outcome` is the answer that the dry run of a resume
prints, and `retry_hints` lists the retries that the rules accept for the failed steps of a run.

The module reads the journal and the version-control port. It writes no event, and it imports no engine: the engine imports
it, and goes on with the step at the phase of the plan (see `engine._retry_ticket`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..ports.vcs import VcsError, VersionControl
from . import domain, run_state, step_scans
from .errors import EngineError
from .events import (
    ROLE_FIXUP, ROLE_SPECIALIST,
    RETRY_PHASE_SPECIALIST, RETRY_PHASE_GATES, RETRY_PHASE_VERIFIER, RETRY_PHASE_SCOPED_VERIFIER, RETRY_PHASE_FIXUP,
    RETRY_PHASE_CHAIN_FIXUP, RETRY_PHASE_CHAIN,
)
from .guards import hotspot_matches
from .journal import JournalError, read_events
from .plan_review import PlanReview
from .progress import Progress, restore_progress
from .vcs_access import branch_tip, vcs_errors
from .workflow import Workflow, plan_order, ticket_branch


@dataclass(frozen=True)
class RetryHint:
    """A retry that the engine accepts for a failed step of a run (#261): the ticket, and whether it needs `--coordinator-commit`."""

    ticket: str
    coordinator: bool = False


@dataclass(frozen=True)
class RetryPlan:
    """Where a failed step goes on: the phase and the verifier round, from the journal events of the step (`prior`).

    `rejected` and `findings` are set when the branch holds a fix-up commit: the
    commit that the last verdict rejected, and the findings of that verdict. A plan of the phase `fixup` sets them too,
    although the branch holds no fix-up commit yet.

    `revive` holds the other tickets that the verification of the chain failed with the same stop, for the phase
    `chain-fixup`: the retry builds them again, because their branches hold their commits (see `_chain_stop`). For the
    phase `chain` it holds the other tickets that the verification of the chain failed (see `_chain_retry_plan`).

    `coordinator` is the commit of `--coordinator-commit`, when the engine accepted it (see `check_coordinator_commit`).
    """

    ticket: str
    phase: str
    round: int
    prior: tuple[dict[str, object], ...]
    rejected: str | None = None
    findings: tuple[str, ...] = ()
    restart: bool = False
    revive: tuple[str, ...] = ()
    coordinator: CoordinatorCommit | None = None

    @property
    def coordinator_fields(self) -> dict[str, object]:
        """The fields of the `retry` event that record the commit of the coordinator. Empty when the retry has none."""
        if self.coordinator is None:
            return {}
        return {"coordinator_commit": self.coordinator.commit, "coordinator_paths": list(self.coordinator.paths)}


@dataclass(frozen=True)
class CoordinatorCommit:
    """A commit of the coordinator at the tip of the branch of a failed step, and the paths that it changes (#225)."""

    commit: str
    paths: tuple[str, ...]


def retry_plan(
    workflow: Workflow, progress: Progress, run_id: str, ticket_id: str, restart: bool = False, coordinator: bool = False,
    config_paths: Sequence[str] = (),
) -> RetryPlan:
    """The first phase of the failed step of `ticket_id` that did not complete. Raise EngineError when it cannot be retried.

    The phase comes from the journal events of the step. The round is the
    number of rounds that the step used (see `rounds_used`): a verifier run that ended with no verdict does
    not count against the fix-up limit, and a fix-up round that red gates started does:

    - No verifier started: the gates.
    - The last verifier started after the last verdict: that verifier again.
      It is the full verifier when the step has no verdict, and otherwise the
      scoped verifier of the fix-up commit.
    - The last verdict is a REJECT, and the fix-up specialist of the next round
      reported committed work: the gates of that fix-up, then its scoped verifier.
      Red gates of that fix-up start the next round while rounds remain (#277).

    A retry of `assure` mode spawns no specialist, with three exceptions. Two come from the agent check (#205). A step that it
    stopped before a specialist session (`_stopped_before_specialist`) has the plan: the specialist. A step that it stopped
    before the fix-up session of a round (`_stopped_before_fixup`) has the plan: the fix-up of that round, again, on the same
    rejected commit with the same findings, and it is not a new round. The third is a valid report of the specialist that
    says partial or blocked, and whose `blocked_reason` names no hotspot of the stack (`config_paths` are hotspots too, see
    `report_names_hotspot`). Its plan is the specialist (#284): it continues in the worktree of the step, with its
    commits, and with the continuation limit of the mode again (see `_build_ticket`). A specialist can do such work, for
    example after the permission mode of the host refused an edit. Any other step whose specialist, or whose fix-up
    specialist, gave no valid report that says committed is refused. So is a step that the verifier accepted, or rejected
    after the last fix-up round. For `economy` mode, see `_economy_retry_plan`.

    A run that cannot be resumed (an invariant violation or a moved run branch ended it, see `run_state.Unresumable`) is
    refused in each mode: the state of the worktree that the verifier changed cannot be trusted.

    With `restart` the retry changes the text of the ticket, and starts it again
    from its base (see `_restart_step`). It spawns a specialist, so no phase of the
    failed step matters: the plan is the specialist at round 0, and the refusals
    that depend on the phase of the step do not apply.

    `coordinator` tells that the retry has `--coordinator-commit`. `check_coordinator_commit` checks the commit after the plan.
    For `economy` mode, see `_economy_retry_plan`. In `assure` mode it is one more exception to the refusal of a report
    that does not say committed (#264): the work can need a change of a hotspot path, which only the coordinator can
    make. A valid report of the specialist that says partial or blocked then has the plan: the gates of round 0, on the
    tip of the branch. A valid report of the fix-up specialist of round N that says partial or blocked has the plan: the
    gates of that fix-up, then its scoped verifier, with the rejected commit and the findings of round N. It is not a new
    round. Without `coordinator`, the message of the refusal names the retry with `--coordinator-commit <sha>` when the
    report gives a reason. A report of the specialist that names a hotspot is such a refusal: no specialist can change a
    hotspot path.
    """
    refused = cannot_be_retried(ticket_id, run_id)
    found = run_state.fold(progress.events).unresumable
    if found is not None:
        raise EngineError(f"{refused}: {found.reason}; start a new run")
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
    prior = tuple(step_scans.step_events(progress.events, ticket_id))
    if workflow.mode == domain.ECONOMY:
        return _economy_retry_plan(workflow, progress, refused, ticket_id, prior, restart, coordinator)
    if restart:
        return RetryPlan(ticket_id, RETRY_PHASE_SPECIALIST, 0, prior, restart=True)
    if _stopped_before_specialist(prior):
        return RetryPlan(ticket_id, RETRY_PHASE_SPECIALIST, 0, prior)
    if not specialist_committed(prior):
        report = unfinished_report(step_scans.specialist_reports(prior))
        if coordinator and report is not None:
            return RetryPlan(ticket_id, RETRY_PHASE_GATES, 0, prior)
        if report is not None and not report_names_hotspot(workflow, report, config_paths):
            return RetryPlan(ticket_id, RETRY_PHASE_SPECIALIST, 0, prior)
        raise EngineError(
            f"{refused}: its specialist gave no valid report that says committed; "
            "--retry goes on only at the gates, the verifier or the scoped verifier" + _coordinator_hint(report, ticket_id)
        )
    starts = [index for index, e in enumerate(prior) if e["event"] == "verify-start"]
    verdicts = [index for index, e in enumerate(prior) if e["event"] == "verdict"]
    round_number = rounds_used(prior)
    if not starts:
        return RetryPlan(ticket_id, RETRY_PHASE_GATES, 0, prior)
    if not verdicts:
        return RetryPlan(ticket_id, RETRY_PHASE_VERIFIER, 0, prior)
    last = prior[verdicts[-1]]
    if domain.is_accepted(str(last["verdict"])):
        raise EngineError(f"{refused}: its verifier accepted it, and the step failed after the verdict: {progress.failures[ticket_id]}")
    rejected = step_scans.verify_commit_before(prior, verdicts[-1])
    findings = tuple(str(finding) for finding in last["findings"])  # type: ignore[union-attr]
    if starts[-1] > verdicts[-1]:
        return RetryPlan(ticket_id, RETRY_PHASE_SCOPED_VERIFIER, round_number, prior, rejected, findings)
    if int(last["round"]) >= domain.fixup_round_limit(workflow.mode):  # type: ignore[call-overload]
        raise EngineError(f"{refused}: its verifier rejected it after the last fix-up round")
    if _stopped_before_fixup(prior, round_number):
        return RetryPlan(ticket_id, RETRY_PHASE_FIXUP, round_number, prior, rejected, findings)
    reports = [e for e in prior if e["event"] == "fixup-report" and e["round"] == round_number]
    if not reports or not reports[-1]["valid"] or reports[-1]["status"] != domain.COMMITTED:
        report = unfinished_report(reports)
        if not (coordinator and report is not None):
            raise EngineError(
                f"{refused}: the fix-up specialist of round {round_number} gave no valid report that says committed; "
                "--retry goes on only at the gates, the verifier or the scoped verifier" + _coordinator_hint(report, ticket_id)
            )
    return RetryPlan(ticket_id, RETRY_PHASE_GATES, round_number, prior, rejected, findings)


def rounds_used(prior: Sequence[dict[str, object]]) -> int:
    """The number of rounds that the journal events `prior` of an `assure` step use: the round of a resume or a retry.

    It is the number of verdicts, because a verifier run that ended with no verdict does not count against the fix-up
    limit. A fix-up round that red gates started has no verdict before it (#277), so a `fixup-start` of a later round
    sets the count to its round.
    """
    verdicts = sum(1 for e in prior if e["event"] == "verdict")
    fixups = [int(e["round"]) for e in prior if e["event"] == "fixup-start"]  # type: ignore[call-overload]
    return max([verdicts, *fixups])


def unfinished_report(reports: Sequence[dict[str, object]]) -> dict[str, object] | None:
    """The newest of the report events `reports` when it is valid and says partial or blocked, else None (#264).

    `reports` are the `report-validation` events of the specialist after its last spawn, or the `fixup-report` events of
    one fix-up round. Such a report can say that the work needs a change of a hotspot path: a retry with a commit of the
    coordinator then goes on with the step.
    """
    if not reports or not reports[-1]["valid"] or reports[-1]["status"] == domain.COMMITTED:
        return None
    return reports[-1]


def _coordinator_hint(report: dict[str, object] | None, ticket_id: str) -> str:
    """The end of the refusal of a retry for a report that says partial or blocked and gives a reason (#264), or ""."""
    if report is None or not report.get("blocked_reason"):
        return ""
    return (
        f"; the report says {report['status']}: {report['blocked_reason']}; when the work needs a change of a hotspot path, "
        f"commit the change on top of the branch of the ticket, and retry with --retry {ticket_id} --coordinator-commit <sha>"
    )


def _stopped_before_specialist(prior: tuple[dict[str, object], ...]) -> bool:
    """True when the agent check (#205) stopped the step before a specialist session, and no agent started after it.

    The step then holds no work of that session, so a retry of `assure` mode can spawn the specialist. A retry of
    `assure` mode also spawns one after a report of the specialist that says partial or blocked and names no hotspot
    (#284), and after a new text of the ticket (see `_restart_step`).
    """
    stop = step_scans.unanswered_stop(prior)
    return stop is not None and prior[stop]["role"] == ROLE_SPECIALIST


def _stopped_before_fixup(prior: tuple[dict[str, object], ...], round_number: int) -> bool:
    """True when the agent check (#205) stopped the step before the fix-up session of `round_number`, and no agent started after it.

    The step then holds no work of that session: the branch holds the rejected commit, and the journal holds no
    `fixup-start` of the round (see `_run_fixup`). A retry of `assure` mode can run the fix-up of the round again.
    """
    stop = step_scans.unanswered_stop(prior)
    return stop is not None and prior[stop]["role"] == ROLE_FIXUP and prior[stop]["round"] == round_number


def _chain_stop(prior: tuple[dict[str, object], ...]) -> dict[str, object] | None:
    """The `agents-changed` event of a stop before the chain fix-up of an `economy` step, or None (#209).

    The stop is the newest `agents-changed` of the step, for the role `fix-up`, and no agent started after it. The
    chain verification wrote it with the ticket that ends the stack (see `chain.ChainRun`), and failed the tickets of the stack.
    """
    stop = step_scans.unanswered_stop(prior)
    return prior[stop] if stop is not None and prior[stop]["role"] == ROLE_FIXUP else None


def cannot_be_retried(ticket_id: str, run_id: str) -> str:
    return f"ticket {ticket_id!r} of run {run_id!r} cannot be retried"


def specialist_committed(prior: tuple[dict[str, object], ...]) -> bool:
    """True when the journal shows a valid specialist report that says committed, after the last spawn of the specialist."""
    validations = [e for e in prior if e["event"] == "report-validation"]
    return step_scans.specialist_done(list(prior)) and validations[-1]["status"] == domain.COMMITTED


def _economy_retry_plan(
    workflow: Workflow, progress: Progress, refused: str, ticket_id: str, prior: tuple[dict[str, object], ...], restart: bool = False,
    coordinator: bool = False,
) -> RetryPlan:
    """The first phase of a failed step of `economy` mode that did not complete. Raise EngineError when it cannot be retried.

    The phase is the gates when the journal shows a valid report of the
    specialist that says committed: no specialist spawns, and the engine checks
    the report, the commits and the hotspots again. The gates then run on the
    tip of the chain. Otherwise the phase is the specialist, which continues.

    A step that the verification of the chain failed goes on with the verification of the chain: the phase is `chain`
    (see `_chain_retry_plan`), with a commit of the coordinator (`coordinator`, #225) or with none (#269). Only the
    ticket at the tip of the chain goes on. A step after which the run built a ticket is refused: the
    branch of that ticket does not hold the commits of this step. A step with
    no `step-start` is refused too. `_check_chain_start` refuses a step whose
    chain start moved.

    With `restart` the step starts again from the chain start of now, so the
    phase is the specialist, and the check of the chain start does not apply
    (see `_restart_step`). A step that the verification of the chain failed cannot start again with a new text.

    A step that the agent check stopped before the chain fix-up session goes on at that fix-up: the phase is
    `chain-fixup` (see `_chain_fixup_plan`).
    """
    if not any(e["event"] == "step-start" for e in prior):
        raise EngineError(f"{refused}: its step did not start; --retry goes on only with a step that started")
    stop = None if restart else _chain_stop(prior)
    if stop is not None:
        return _chain_fixup_plan(workflow, progress, ticket_id, prior, stop)
    if step_scans.chain_failed(prior) and not restart:
        return _chain_retry_plan(workflow, progress, refused, ticket_id, prior, coordinator)
    if step_scans.chain_failed(prior):
        raise EngineError(
            f"{refused}: the verification of the chain failed it, so a new text of the ticket cannot start it again; "
            "--retry with no new text goes on with the verification of the chain"
        )
    order = [ticket.id for ticket in plan_order(workflow)]
    later = [other for other in order[order.index(ticket_id) + 1 :] if other in progress.built]
    if later:
        raise EngineError(
            f"{refused}: ticket {later[0]!r} after it in the chain is built, "
            "and its branch does not hold the commits of this step"
        )
    if restart:
        return RetryPlan(ticket_id, RETRY_PHASE_SPECIALIST, 0, prior, restart=True)
    phase = RETRY_PHASE_GATES if specialist_committed(prior) else RETRY_PHASE_SPECIALIST
    return RetryPlan(ticket_id, phase, 0, prior)


def _chain_retry_plan(
    workflow: Workflow, progress: Progress, refused: str, ticket_id: str, prior: tuple[dict[str, object], ...],
    coordinator: bool = True,
) -> RetryPlan:
    """The plan of a retry of the ticket at the tip of a chain whose verification failed (#225, #261, #269).

    Red gates on the tip of the chain can need a change of a hotspot path, for example a new render of the agent pair.
    No specialist can make it, so the chain fails. The coordinator commits the change at the tip of the chain, on the
    branch of the last ticket of the chain, and retries that ticket with `--coordinator-commit` (`coordinator`). The other
    tickets that the verification of the chain failed go in `revive`: their branches hold their commits, and the chain
    verification checks them again with the ticket. The verification of the chain starts again after the `retry` event:
    the gates run on the tip, and a fresh verifier checks each stack that has no ACCEPT. The fix-up rounds before the
    retry do not count (see `chain.ChainRun`).

    With no `coordinator` the plan is the same, and the tip does not move (#269): for example a gate that failed one time
    and then passes, or a stop of the agent check before a verifier session of the chain (#261). When the gates were green
    on the tip, the new verification counts that result (see `gate-skip`), and a verifier starts.

    Raise EngineError when a ticket after `ticket_id` in the plan is in the chain, and name the ticket at the tip. With no
    `coordinator`, also raise EngineError when the agent check stopped the chain before the fix-up of an earlier ticket
    (see `_chain_stop`): the retry of that ticket goes on at the stopped fix-up, and a retry of the tip drops the fix-up
    and its findings. So the retry hints name the chain once (see `retry_hints`).
    """
    order = plan_order(workflow)
    revive = tuple(
        ticket.id for ticket in order
        if ticket.id != ticket_id and ticket.id in progress.failed and step_scans.chain_failed(step_scans.step_events(progress.events, ticket.id))
    )
    chain = [ticket.id for ticket in order if ticket.id == ticket_id or ticket.id in revive or ticket.id in progress.built]
    if chain[-1] != ticket_id:
        where = "put the commit of the coordinator on its branch, and " if coordinator else ""
        raise EngineError(f"{refused}: ticket {chain[-1]!r} is at the tip of the chain; {where}retry it with --retry {chain[-1]}")
    stopped = next((other for other in revive if _chain_stop(tuple(step_scans.step_events(progress.events, other))) is not None), None)
    if stopped is not None and not coordinator:
        raise EngineError(
            f"{refused}: the agent check stopped the chain before the fix-up of ticket {stopped!r}; "
            f"retry it with --retry {stopped} --agents-changed"
        )
    return RetryPlan(ticket_id, RETRY_PHASE_CHAIN, 0, prior, revive=revive)


def _step_since(prior: Sequence[dict[str, object]]) -> str:
    """Where the work of a step starts: its `base_commit`, or the commit of the run branch of its newest rebase."""
    base = str(next(e for e in prior if e["event"] == "step-start")["base_commit"])
    rebases = [e for e in prior if e["event"] == "rebase" and e["result"] == "rebased"]
    return domain.work_starts_at(base, str(rebases[-1]["onto_commit"]) if rebases else None)


def check_coordinator_commit(
    workflow: Workflow, plan: RetryPlan, vcs: VersionControl, repo: Path, run_id: str, given: str, config_paths: Sequence[str]
) -> CoordinatorCommit:
    """Check the commit of `--coordinator-commit` for the retry of `plan`, and return it with its paths (#225).

    The commit must be the tip of the branch of the ticket, beyond the start of the work of the step, and each path
    that it changes must match a hotspot of the stack. Its paths are the paths that differ from the commit before it on
    the branch. A retry that starts the ticket again from its base keeps no commit of the step, so it is refused. For the
    phase `chain` the engine also checks the other commits of the step against the hotspots here, before the journal
    changes: a match refuses the retry. In the other phases the hotspot check of the step does it (see `_hotspot_stop`).
    Raise EngineError, which names the commit and the cause, when a check fails.
    """
    refused = cannot_be_retried(plan.ticket, run_id)
    if plan.restart:
        raise EngineError(
            f"{refused} with --coordinator-commit: the new text starts the ticket again from its base, "
            "and the new branch keeps no commit of the old step"
        )
    ticket = next(ticket for ticket in workflow.tickets if ticket.id == plan.ticket)
    stack = workflow.stacks[ticket.stack]
    branch = ticket_branch(workflow, ticket)
    try:
        commit = vcs.commit_of(repo, given)
    except VcsError as error:
        raise EngineError(f"{refused}: --coordinator-commit {given} names no commit: {error}") from None
    tip = branch_tip(vcs, repo, branch)
    if commit != tip:
        raise EngineError(f"{refused}: the coordinator commit {commit} is not on top of the branch {branch}: the tip of the branch is {tip}")
    since = _step_since(plan.prior)
    with vcs_errors():
        own = vcs.commits_since(repo, since, commit)
        if not own:
            raise EngineError(f"{refused}: the coordinator commit {commit} is not on top of the branch {branch}: it is no commit beyond {since}")
        paths = vcs.changed_paths(repo, own[1].id if len(own) > 1 else since, commit)
    outside = [path for path in paths if not hotspot_matches([path], stack.all_hotspots(config_paths))]
    if outside:
        raise EngineError(
            f"{refused}: the coordinator commit {commit} changes paths that are no hotspot of the stack {ticket.stack}: "
            + ", ".join(f"`{path}`" for path in outside) + "; a coordinator commit changes only hotspot paths"
        )
    if plan.phase == RETRY_PHASE_CHAIN:
        check_chain_hotspots(workflow, plan, vcs, repo, run_id, config_paths, commit)
    return CoordinatorCommit(commit, tuple(paths))


def check_chain_hotspots(
    workflow: Workflow, plan: RetryPlan, vcs: VersionControl, repo: Path, run_id: str, config_paths: Sequence[str], coordinator: str | None = None
) -> None:
    """Refuse a retry at the phase `chain` when a commit of the step changes a hotspot path of the stack (#225, #269).

    The commits are those of the branch of the ticket after the start of the work of the step, to the tip of the branch:
    the commits of the specialist and the fix-up commits of the chain. The verification of the chain does not check
    them again, so the retry checks them before the journal changes. The change of `coordinator`, the commit of
    `--coordinator-commit`, does not count, and nor does the change of a commit of the coordinator that an earlier retry
    of the step recorded (see `coordinator_commits`). Raise EngineError, which names each match.
    """
    ticket = next(ticket for ticket in workflow.tickets if ticket.id == plan.ticket)
    since = _step_since(plan.prior)
    with vcs_errors():
        tip = coordinator or branch_tip(vcs, repo, ticket_branch(workflow, ticket))
        excluded = {commit for commit, _ in coordinator_commits(vcs, repo, plan.prior)} | ({coordinator} if coordinator else set())
        matches = hotspot_matches(paths_without(vcs, repo, since, tip, True, excluded), workflow.stacks[ticket.stack].all_hotspots(config_paths))
    if not matches:
        return
    found = "; ".join(f"{m.path} matches the hotspot {m.pattern!r}" for m in matches)
    if coordinator:
        raise EngineError(f"{cannot_be_retried(plan.ticket, run_id)}: hotspot finding: {found}; only the paths of the coordinator commit {coordinator} are not counted")
    raise EngineError(
        f"{cannot_be_retried(plan.ticket, run_id)}: hotspot finding: {found}; a commit of the step changes a hotspot path, "
        "and only the coordinator changes a hotspot path"
    )


def paths_without(vcs: VersionControl, path: Path, since: str, until: str | None, merge_base: bool, excluded: set[str]) -> list[str]:
    """The paths that the commits from `since` to `until` change, with the change of each commit in `excluded` left out.

    `until` None is the commit that `path` has checked out. With `merge_base` the comparison starts at the merge base of
    `since` and `until`. The change of a commit is the difference from the commit before it in the list of the commits
    after `since`, or from `since` for the first one. A commit in `excluded` that is not in the range changes nothing.
    Raise VcsError when the port fails.
    """
    commits = [commit.id for commit in vcs.commits_since(path, since, until)]  # newest first
    if not excluded.intersection(commits):
        return vcs.changed_paths(path, since, until, from_merge_base=merge_base)
    found: set[str] = set()
    upper: str | None = until  # the end of the next part, newest first
    for index, commit in enumerate(commits):
        if commit not in excluded:
            continue
        if upper != commit:
            found.update(vcs.changed_paths(path, commit, upper))
        if index + 1 == len(commits):
            return sorted(found)  # the oldest commit is excluded: nothing is left before it
        upper = commits[index + 1]
    found.update(vcs.changed_paths(path, since, upper, from_merge_base=merge_base))
    return sorted(found)


def coordinator_commits(vcs: VersionControl, repo: Path, own: Sequence[dict[str, object]]) -> list[tuple[str, tuple[str, ...]]]:
    """Each commit of the coordinator of a step, as the branch holds it now, with its paths, oldest first (#225).

    `own` are the journal events of the step (see `step_scans.step_events`). A `retry` with `coordinator_commit` records each
    one. A rebase onto the run branch writes the commits of the step again, so the engine follows each commit through
    each rebase after its `retry`: the commit at the same place in the list of the commits of the step. When the rebase
    changed the number of the commits, the commit is not followed, and the hotspot check counts its paths again.
    """
    commits: list[tuple[str, tuple[str, ...]]] = []
    start = next((str(e["base_commit"]) for e in own if e["event"] == "step-start"), None)
    for event in own:
        if event["event"] == "retry" and event.get("coordinator_commit"):
            commits.append((str(event["coordinator_commit"]), tuple(str(p) for p in event.get("coordinator_paths", []))))  # type: ignore[union-attr]
        elif event["event"] == "rebase" and event["result"] == "rebased":
            onto = str(event["onto_commit"])
            if commits and start is not None:
                with vcs_errors():
                    old = [c.id for c in vcs.commits_since(repo, start, str(event["from_commit"]))]
                    new = [c.id for c in vcs.commits_since(repo, onto, str(event["to_commit"]))]
                if len(old) == len(new):
                    commits = [(new[old.index(commit)] if commit in old else commit, paths) for commit, paths in commits]
            start = onto
    return commits


def _chain_fixup_plan(
    workflow: Workflow, progress: Progress, ticket_id: str, prior: tuple[dict[str, object], ...], stop: dict[str, object]
) -> RetryPlan:
    """The plan of a retry after the agent check stopped the chain verification before a fix-up session (#209).

    The ticket is the one that ends the stack, the ticket of `stop`. The stop failed each ticket of the stack, and the tickets
    of the stacks after it, which the chain did not verify. Those tickets, which the journal failed after the stop, go in
    `revive`: their branches hold their commits, and the chain verification checks them again with the ticket. The round is
    the round of the stopped fix-up.
    """
    stop_index = max(index for index, e in enumerate(progress.events) if e is stop or e == stop)
    ended = {str(e["ticket"]): index for index, e in enumerate(progress.events) if e["event"] == "step-end"}
    revive = tuple(
        ticket.id for ticket in plan_order(workflow)
        if ticket.id != ticket_id and ticket.id in progress.failed and ended[ticket.id] > stop_index
    )
    return RetryPlan(ticket_id, RETRY_PHASE_CHAIN_FIXUP, int(stop["round"]), prior, revive=revive)  # type: ignore[call-overload]


def retry_outcome(
    workflow: Workflow, events: list[dict[str, object]], run_id: str, review: PlanReview, retry: str | None,
    coordinator_commit: str | None, vcs: VersionControl, repo: Path, config_paths: Sequence[str],
) -> tuple[str | None, str | None]:
    """The message with which the rules of the retry (`retry_plan`) refuse the retry of `retry` (#261), and the line that
    tells the phase of the retry that they accept (#269). One of the two is None, and both are None with no `retry`.

    The line is `retry <ticket>: phase <phase>`, and it ends with `, revives <tickets>` when the retry builds again other
    tickets that the verification of the chain failed. With `coordinator_commit`, the function also makes the checks of
    the commit that the resume makes (`check_coordinator_commit`, with the hotspot check of the chain), and their refusal has
    the same text (#283). The hotspot check of a retry at the phase `chain` with no commit needs the repository too, so
    the resume makes it, not this function.
    """
    if retry is None:
        return None, None
    restart = review.check is not None and retry in review.check.restarted
    try:
        progress = restore_progress(events, run_id, retrying=True)
        plan = retry_plan(workflow, progress, run_id, retry, restart, coordinator_commit is not None, config_paths)
        if coordinator_commit is not None:
            check_coordinator_commit(workflow, plan, vcs, repo, run_id, coordinator_commit, config_paths)
    except EngineError as error:
        return str(error), None
    revives = f", revives {', '.join(plan.revive)}" if plan.revive else ""
    return None, f"retry {plan.ticket}: phase {plan.phase}{revives}"


def retry_hints(workflow: Workflow, journal: Path, run_id: str, config_paths: Sequence[str] = ()) -> list[RetryHint]:
    """The retries that the rules of the retry (`retry_plan`) accept for the failed steps of a run, in plan order (#261).

    A failed ticket whose retry the rules refuse has no hint. A retry that the rules accept only with a new text of the
    ticket (see `_restart_step`) has a hint: the retry goes on when the text changes. In `economy` mode, a retry of a
    ticket that the verification of the chain failed goes on only at one ticket: the ticket at the tip of the chain, or
    after a stop before a chain fix-up the ticket that ends the stack (#209). So the hints name that ticket once. The
    retry of the tip goes on with no commit (#269). Its hint names a commit of the coordinator (#225) only when the newest
    report of a chain fix-up names a hotspot path (see `_chain_report_names_hotspot`). In `assure` mode, the hint
    of a step whose report says partial or blocked names the retry with a commit of the coordinator (#264) when the
    rules refuse the retry with no commit: the report of a fix-up specialist, or a report of the specialist that names a
    hotspot. The work can need a hotspot, and the retry with a new text of the ticket that the rules also accept starts
    the work again. A report of the specialist that names no hotspot has the plain hint: the specialist continues (#284).
    Return no hint when the journal cannot be read.
    """
    try:
        events = read_events(journal, torn_tail=True)
    except JournalError:
        return []
    progress = restore_progress(events, run_id, retrying=True)
    failed = [ticket.id for ticket in plan_order(workflow) if ticket.id in progress.failed]

    def plan(ticket_id: str, coordinator: bool = False, restart: bool = False) -> RetryPlan | None:
        try:
            return retry_plan(workflow, progress, run_id, ticket_id, restart, coordinator, config_paths)
        except EngineError:
            return None

    def accepted(ticket_id: str, coordinator: bool = False, restart: bool = False) -> bool:
        return plan(ticket_id, coordinator, restart) is not None

    plain = [ticket_id for ticket_id in failed if accepted(ticket_id) or accepted(ticket_id, restart=True)]
    hints = {ticket_id: RetryHint(ticket_id) for ticket_id in plain}
    if workflow.mode == domain.ECONOMY and _chain_report_names_hotspot(workflow, events, config_paths):
        tips = [t for t in plain if (p := plan(t)) is not None and p.phase == RETRY_PHASE_CHAIN]
        hints.update({t: RetryHint(t, coordinator=True) for t in tips})
    if workflow.mode == domain.ASSURE:
        hints.update({t: RetryHint(t, coordinator=True) for t in failed if not accepted(t) and accepted(t, True)})
    return [hints[ticket_id] for ticket_id in failed if ticket_id in hints]


def _chain_report_names_hotspot(workflow: Workflow, events: Sequence[dict[str, object]], config_paths: Sequence[str]) -> bool:
    """True when the newest report of a fix-up of the last verification of the chain names a hotspot path (#269).

    The report is valid and says partial or blocked (see `unfinished_report`), and a word of its `blocked_reason` matches
    a hotspot of the stack of its ticket: the work can need a change that only the coordinator makes. The last
    verification of the chain is its events after the newest `retry` that is not a retry at the chain fix-up, or the
    newest `plan-change`, as in `chain.ChainRun`.
    """
    start = run_state.fold(events).chain.start
    report = unfinished_report([e for e in events[start:] if e["event"] == "fixup-report"])
    return report is not None and report_names_hotspot(workflow, report, config_paths)


def report_names_hotspot(workflow: Workflow, report: dict[str, object], config_paths: Sequence[str]) -> bool:
    """True when a word of the `blocked_reason` of the report event `report` matches a hotspot of the stack of its ticket.

    `config_paths` are the project configuration paths of the adapter, which are hotspots too (#273). A report with no
    `blocked_reason`, or of a ticket that the workflow does not hold, names no hotspot.
    """
    ticket = next((t for t in workflow.tickets if t.id == report["ticket"]), None)
    if ticket is None or not report.get("blocked_reason"):
        return False
    words = [word.strip("`'\".,;:()[]") for word in str(report["blocked_reason"]).split()]
    return bool(hotspot_matches(words, workflow.stacks[ticket.stack].all_hotspots(config_paths)))
