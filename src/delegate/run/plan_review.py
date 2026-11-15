"""The review of a plan change: what a resume does with the plan of a run, and the rules that read the journal of a run for it (#338).

`review_workflow` applies the rules of a plan change (`domain.check_plan_change`) to the newest plan of a run, from its journal, and
changes nothing. `PlanReview` is its result. The dry run of a resume prints it (see `dry_run.review_plan_change`), and the resume
refuses a change that the rules refuse (see `engine.check_start`). The module also holds the record of the plan and of the
configuration of a run in the journal: `plan_of` and `plan_record` write it, `recorded_plan`, `recorded_configuration` and
`configuration_record` read and compare it.

The module imports no other part of the engine: the engine imports it. What a resume does with the retry and with the tickets that
the run skipped needs the rules of the retry, which the engine holds, so the engine adds those fields to the result (see
`engine.plan_review_with_outlook`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import domain, run_state, step_scans
from .errors import EngineError
from .events import RESUME, RUN_START, STACK_CHANGE
from .journal import abandoned_end, plan_ids
from .progress import restore_progress
from .workflow import Workflow, plan_order, ticket_branch


def check_same_run(workflow: Workflow, workflow_path: Path, run_start: dict[str, object], run_id: str) -> None:
    """Refuse a resume when the workflow is not the one that the run started from. The message names the field.

    The tickets can differ: the rules of a plan change decide (see `review_workflow`).
    """
    now: dict[str, object] = {
        "mode": workflow.mode, "adapter": workflow.adapter, "base_branch": workflow.base_branch, "run_branch": workflow.run_branch,
    }
    for name, value in now.items():
        if run_start[name] != value:
            raise EngineError(f"workflow {workflow_path} differs from run {run_id}: {name} was {run_start[name]!r}, is now {value!r}")


def configuration_record(workflow: Workflow) -> dict[str, object]:
    """The configuration of the run as the journal records it, in the field `configuration` of `run-start` and of `resume` (#289).

    The record holds each field of each stack except its name, and the top-level fields `ticket_start` and
    `keep_worktrees`. It is canonical: the JSON form of the values, so a record that the journal gives back is equal to
    the record of the same workflow. A new field of `Stack` goes into the record with no change here.
    """
    stacks = {name: {key: value for key, value in asdict(stack).items() if key != "name"} for name, stack in sorted(workflow.stacks.items())}
    record = {"stacks": stacks, "ticket_start": workflow.ticket_start, "keep_worktrees": workflow.keep_worktrees}
    return json.loads(json.dumps(record))


def recorded_configuration(events: list[dict[str, object]]) -> dict[str, object] | None:
    """The newest record of the configuration of a run: the `configuration` of the newest `resume` that has one, or else of
    `run-start`, with each `stack-change` after it applied (#290). So a stack has the newest record of each of its fields.

    None when the journal records none: an earlier version of the engine started the run.
    """
    record: dict[str, object] | None = None
    for event in events:
        if event["event"] in (RUN_START, RESUME) and "configuration" in event:
            record = json.loads(json.dumps(event["configuration"]))
        elif event["event"] == STACK_CHANGE and record is not None:
            stack = record["stacks"].setdefault(event["stack"], {})  # type: ignore[attr-defined]
            for name, change in event["changes"].items():  # type: ignore[attr-defined]
                stack[name] = change["new"]
    return record


@dataclass(frozen=True)
class StackChange:
    """The deliberate change of one stack at a resume (#290): the stack, and the old and the new value of each changed field."""

    stack: str
    fields: dict[str, tuple[object, object]]

    def event_fields(self) -> dict[str, object]:
        """The fields of the `stack-change` event."""
        return {"stack": self.stack, "changes": {name: {"old": old, "new": new} for name, (old, new) in self.fields.items()}}


def _stack_changes(old: dict[str, object], new: dict[str, object]) -> list[StackChange]:
    """Each stack whose fields differ from the record `old` to the record `new`, in the order of the names of the stacks.

    A stack that only one record has is not a change: no ticket of the run used a new stack, and a ticket of a removed
    stack is not in the plan. A field that `old` does not hold is not compared: a later version of the engine added it.
    """
    changes: list[StackChange] = []
    old_stacks, new_stacks = old["stacks"], new["stacks"]
    for name in sorted(old_stacks.keys() & new_stacks.keys()):  # type: ignore[attr-defined]
        recorded, now = old_stacks[name], new_stacks[name]  # type: ignore[index]
        fields = {key: (recorded[key], now.get(key)) for key in recorded if recorded[key] != now.get(key)}
        if fields:
            changes.append(StackChange(name, fields))
    return changes


def _check_same_configuration(
    workflow: Workflow, workflow_path: Path, events: list[dict[str, object]], run_id: str, stack_changed: bool = False,
) -> tuple[StackChange, ...]:
    """Refuse a resume when the configuration of a stack, or a top-level field of the record, differs from the newest record (#289).

    The message names each stack with its changed fields, and each changed top-level field. A journal with no record
    passes: the resume writes the record (see `_execute`). With `stack_changed` (`--stack-changed`, #290) a change of a
    stack does not refuse the resume: the result holds each change, and the resume records it in `stack-change`. A change
    of a top-level field still refuses it. The flag with no change of a stack is refused too, so a command does not carry it by habit.
    """
    recorded = recorded_configuration(events)
    now = configuration_record(workflow)
    stacks = [] if recorded is None else _stack_changes(recorded, now)
    top = [] if recorded is None else [key for key in ("ticket_start", "keep_worktrees") if key in recorded and recorded[key] != now[key]]
    named = [f"stack {change.stack!r}: {', '.join(change.fields)}" for change in stacks] + top
    if top or (stacks and not stack_changed):
        # A change of a stack can be meant, so the message names the flag. A change of a top-level field cannot be recorded.
        meant = f"if the change of a stack is meant, go on with delegate run --resume {run_id} --stack-changed; otherwise " if not top else ""
        raise EngineError(
            f"workflow {workflow_path} changes the configuration of run {run_id}: {'; '.join(named)}; "
            "the journal records the configuration under which the run checks its tickets, and a resume does not change it: "
            f"{meant}put the recorded values back in the workflow file, or start a new run"
        )
    if stack_changed and not stacks:
        raise EngineError(
            f"--stack-changed: workflow {workflow_path} changes no stack of run {run_id}, so there is no change to record; "
            "go on with the resume without the flag"
        )
    return tuple(stacks)


def plan_of(workflow: Workflow) -> list[domain.PlanTicket]:
    """The plan of a workflow: each ticket in plan order, with its stack, its blockers, the SHA-256 of its text, and its tier."""
    return [
        domain.PlanTicket(t.id, t.stack, tuple(t.blocked_by), hashlib.sha256(t.text.encode()).hexdigest(), t.tier)
        for t in plan_order(workflow)
    ]


def plan_record(plan: list[domain.PlanTicket]) -> list[dict[str, object]]:
    """The plan as the journal records it, in the field `plan` of `run-start` and of `plan-change`.

    A ticket with no `tier` has no `tier` in the record, as in a journal from before #231.
    """
    return [
        {"id": t.id, "stack": t.stack, "blocked_by": list(t.blocked_by), "text_sha256": t.text_sha256, **({"tier": t.tier} if t.tier else {})}
        for t in plan
    ]


def recorded_plan(events: list[dict[str, object]]) -> list[domain.PlanTicket] | None:
    """The newest plan of a run: the plan of its newest `plan-change`, or else of `run-start`.

    None when the journal records no plan: an earlier version of the engine wrote only the ticket ids.
    """
    records = [event["plan"] for event in events if event["event"] == "plan-change"] or [events[0].get("plan")]
    if records[-1] is None:
        return None
    return [
        domain.PlanTicket(
            str(r["id"]), str(r["stack"]), tuple(str(b) for b in r["blocked_by"]), str(r["text_sha256"]),  # type: ignore[index, union-attr]
            None if r.get("tier") is None else str(r["tier"]),  # type: ignore[union-attr]
        )
        for r in records[-1]  # type: ignore[union-attr]
    ]


@dataclass(frozen=True)
class SkippedTicket:
    """What a resume does with a ticket that the run skipped (#260): `text` says if its step runs, and why, in the words of the dry run."""

    ticket: str
    text: str


@dataclass(frozen=True)
class PlanReview:
    """What a resume does with the plan of a run: the old and the new order, and the verdict of each rule of a plan change.

    `check` is None when the tickets of the workflow are the newest plan of the
    run. `missing` holds the later tickets that `--reviewed` does not cover.
    `texts` holds the text of each added or changed ticket. `other` holds the
    problems that are not a rule of the plan: an unknown id in `--reviewed`,
    and the branch of a new ticket that exists already.
    """

    run_id: str
    mode: str
    old_order: list[str]
    new_order: list[str]
    check: domain.PlanCheck | None
    missing: list[str] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)
    other: list[str] = field(default_factory=list)
    # What the resume does with each ticket that the run skipped, in plan order (#260). Only `review_plan_change` sets it.
    skipped: tuple[SkippedTicket, ...] = ()
    # The message that refuses the retry of `--retry`, or None when the engine accepts it (#261). Only `review_plan_change`
    # sets it. It is not in `problems`: a resume refuses the retry with the same message after the plan change.
    retry_refusal: str | None = None
    # The line that tells the phase of the retry of `--retry` when the engine accepts it (#269). Only `review_plan_change` sets it.
    retry_outlook: str | None = None
    # The changes of stacks that `--stack-changed` lets the resume record (#290), one for each stack. Empty without the flag.
    stack_changes: tuple[StackChange, ...] = ()
    # The message with which another check of the resume (`engine.resume_checks`) refuses the resume, or None (#357): the run ended,
    # the run branch moved or is gone, the start of the chain, the hotspots of the chain. Only `review_plan_change` sets it, and
    # only when the rules of the plan and the rules of the retry accept: their refusals have their own fields.
    resume_refusal: str | None = None

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


def review_workflow(
    workflow: Workflow, workflow_path: Path, events: list[dict[str, object]], run_id: str, reviewed: tuple[str, ...],
    branch_exists: Callable[[str], bool], retry: str | None = None, stack_changed: bool = False,
) -> PlanReview:
    """Apply the rules of a plan change to the workflow and the journal of a run. Change nothing.

    The workflow is compared with the newest plan of the run, not with
    `run-start`. Raise EngineError when a field other than the tickets differs
    (see `check_same_run`), when the plan changed and the journal records
    no plan to compare with, or when the owner abandoned the run (#201).

    `retry` is the ticket of `--retry`. When the run state gives it as failed,
    its text can change (rule 10): the retry starts it again. `stack_changed` is `--stack-changed`: the changes of stacks
    that it lets the resume record are in `stack_changes` of the result (see `_check_same_configuration`).
    """
    abandoned = abandoned_end(events)
    if abandoned is not None:
        reason = f" ({abandoned['reason']})" if abandoned.get("reason") else ""
        raise EngineError(f"run {run_id!r} cannot be resumed: it ended abandoned{reason}; start a new run")
    check_same_run(workflow, workflow_path, events[0], run_id)
    stack_changes = _check_same_configuration(workflow, workflow_path, events, run_id, stack_changed)
    old_order, new_plan = plan_ids(events), plan_of(workflow)
    new_order = [t.id for t in new_plan]
    old = recorded_plan(events)
    if old is None:
        if old_order != new_order:
            raise EngineError(
                f"workflow {workflow_path} differs from run {run_id}: tickets was {old_order!r}, is now {new_order!r}; "
                "the journal of the run records no plan, because an earlier version of delegate started it, "
                "so a plan change cannot be checked: start a new run"
            )
        return PlanReview(run_id, workflow.mode, old_order, new_order, None, stack_changes=stack_changes)
    if old == new_plan:
        return PlanReview(run_id, workflow.mode, old_order, new_order, None, stack_changes=stack_changes)
    started = {str(event["ticket"]) for event in events if event["event"] == "step-start"}
    outcome = next((t.outcome for t in run_state.fold(events).tickets if t.id == retry), None)
    check = domain.check_plan_change(
        old, new_plan, started, retry if outcome == run_state.FAILED else None, _failed_chain(workflow, events, run_id)
    )
    other = [
        f"--reviewed names {ticket!r}, which is not a ticket of the workflow"
        for ticket in reviewed if ticket != domain.REVIEWED_ALL and ticket not in new_order
    ]
    for ticket in workflow.tickets:
        branch = ticket_branch(workflow, ticket)
        if ticket.id in check.added and branch_exists(branch):
            other.append(f"branch {branch!r} for the new ticket {ticket.id!r} exists already; delete it or give the ticket another id")
    texts = {t.id: t.text for t in workflow.tickets if t.id in check.added or t.id in check.changed}
    return PlanReview(
        run_id, workflow.mode, old_order, new_order, check, domain.unreviewed(check.later, reviewed), texts, other, stack_changes=stack_changes
    )


def _failed_chain(workflow: Workflow, events: list[dict[str, object]], run_id: str) -> list[str]:
    """The tickets that the verification of an `economy` chain failed, in plan order, or no ticket (#270).

    Such a ticket failed, and its newest step has a `step-end` built before its newest `step-end` (see `step_scans.chain_failed`).
    A retry of one of them can build it again: then it is not failed. In `assure` mode there is no chain.
    """
    if workflow.mode != domain.ECONOMY:
        return []
    failed = restore_progress(events, run_id, retrying=True).failed
    return [
        ticket_id for ticket_id in plan_ids(events)
        if ticket_id in failed and step_scans.chain_failed(step_scans.step_events(events, ticket_id))
    ]
