"""The verification of an `economy` chain: the gates and the verifiers at the end of the chain (#340).

`ChainRun` runs the gates of each stack once on the tip of the chain, the fix-up rounds, and one verifier for each stack. It
moves the run branch when every stack is accepted. `chain_redo` finds the chain fix-up that the agent check stopped, so that a
retry runs it again (#209).

The module imports no engine. The engine drives the steps (the gate run, the fix-up session, the verifier session, the checks
of the run branch), and it gives them to `ChainRun` in `ChainSteps`. So the engine imports this module, and the module needs
no import of the engine. `ChainRun` takes the context of the run (`RunContext`, #342) and the journal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol

from ..ports.vcs import VcsError, VersionControl
from . import domain, run_state, step_scans
from .brief import ChainSegment, GateLogCopy, green_gates_text
from .context import RunContext
from .errors import Rejected, StepFailed
from .events import GATE_PHASE_CHAIN, RETRY_PHASE_CHAIN_FIXUP, ROLE_FIXUP, TRIGGER_GATES_RED, TRIGGER_REJECT
from .journal import Journal
from .reports import VerifierReport, map_findings
from .vcs_access import branch_tip
from .workflow import Stack, Ticket, specialist_tier, strongest_tier, ticket_branch


class ChainRedo(NamedTuple):
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


def chain_redo(events: list[dict[str, object]], ticket: str, stack: str) -> ChainRedo | None:
    """The fix-up of `stack` that the agent check stopped before its session, when a retry at the chain fix-up follows, else None.

    `events` are the journal events since the last retry that is not a retry at the chain fix-up. `ticket` ends the stack.
    The stop is the newest `agents-changed` of that ticket, for the role `fix-up`, with no `agent-start` after it, and a
    `retry` with the phase `chain-fixup` after it (see `step_scans.unanswered_stop`). The event holds the commit, the findings file and the
    trigger of the stopped fix-up. The state of the verifier comes from the last `verdict` of the stack.
    """
    index = step_scans.unanswered_stop(events, role=ROLE_FIXUP, ticket=ticket)
    if index is None:
        return None
    stop = events[index]
    after = step_scans.events_of(events[index + 1 :], ticket)
    if not any(e["event"] == "retry" and e.get("phase") == RETRY_PHASE_CHAIN_FIXUP for e in after):
        return None
    verdicts = [i for i, e in enumerate(events[:index]) if e["event"] == "verdict" and e.get("stack") == stack]
    rejected: str | None = None
    findings: list[str] = []
    if verdicts and not domain.is_accepted(str(events[verdicts[-1]]["verdict"])):
        rejected, findings = step_scans.verify_commit_before(events, verdicts[-1], stack), [str(f) for f in events[verdicts[-1]]["findings"]]  # type: ignore[union-attr]
    return ChainRedo(
        int(stop["round"]), str(stop["rejected_commit"]), Path(str(stop["findings_file"])).read_text(),  # type: ignore[call-overload]
        stop["trigger"] == TRIGGER_GATES_RED, rejected, findings,
    )


def chain_text(tickets: list[Ticket]) -> str:
    """The text of a stack's tickets for the fix-up specialist: the text of one ticket, or each ticket with its id."""
    if len(tickets) == 1:
        return tickets[0].text
    return "\n\n".join(f"Ticket {ticket.id}: {ticket.text.rstrip(chr(10))}" for ticket in tickets)


class ChainStep(Protocol):
    """The state that one step of the chain shares: the engine's `_Step`. The chain reads these fields and no other."""

    run: RunContext
    journal: Journal
    ticket: Ticket

    @property
    def stack(self) -> Stack: ...

    @property
    def branch(self) -> str: ...

    @property
    def worktree(self) -> Path: ...

    @property
    def run_dir(self) -> Path: ...

    @property
    def chain_fields(self) -> dict[str, object]: ...


class ChainGateRun(Protocol):
    """The outcome of one gate, as the chain reads it: `gate_run.GateRun`."""

    command: str
    green: bool
    output: str
    label: str


@dataclass(frozen=True)
class ChainSteps:
    """The steps of the engine that the verification of a chain drives. The engine builds it, and `ChainRun` calls it.

    Each field has the signature of the engine function that it holds: `make_step` makes the step of a stack (`_Step`),
    `run_gate` runs one gate of the step (`gate_run.run_gate_with_output`), `copy_gate_logs`, `scratch_directory` and `red_gates_text` write the
    findings of red gates, `run_fixup` and `checked_fixup_brief` run a fix-up round and check its commit, `findings_text`
    writes the findings of a verdict, `verify_round` runs one verifier, `check_run_branch`, `check_verifier_stop` and
    `advance_run_branch` are the checks of the run branch, and `ends_the_run` and `halt_reason` sort an error that ends the run.
    """

    make_step: Callable[..., ChainStep]
    run_gate: Callable[..., ChainGateRun]
    copy_gate_logs: Callable[[Sequence[ChainGateRun], Path], list[GateLogCopy]]
    scratch_directory: Callable[[Path, str, str], Path]
    red_gates_text: Callable[[str, list[tuple[str, str]], Sequence[GateLogCopy]], str]
    run_fixup: Callable[..., str]
    checked_fixup_brief: Callable[..., str]
    findings_text: Callable[[list[str]], str]
    verify_round: Callable[..., VerifierReport]
    check_run_branch: Callable[[Journal, VersionControl, Path, str, str], str]
    check_verifier_stop: Callable[[Journal, VersionControl, Path, str, list[dict[str, object]]], None]
    advance_run_branch: Callable[[ChainStep, str], str | None]
    ends_the_run: Callable[[Exception], bool]
    halt_reason: Callable[[Exception], str]


class ChainRun:
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
    does not hold yet. It reads only the current pass of the journal: the
    events after the newest event for which
    `run_state.starts_chain_verification` is true (see `run_state.ChainPass`,
    ADR 0009). A `plan-change` and a `retry` start a pass, except a `retry`
    with the phase `chain-fixup` (#320): it goes on with the verification that
    the stop cut off, so the verdicts and the fix-up rounds before it still
    count. So after any other retry or a plan change, the chain verification
    of the run before it does not count, and the tickets that it accepted are
    not verified again. A ticket that a plan change added after the chain
    verification gets its own gates and verifier.
    """

    def __init__(
        self, run: RunContext, journal: Journal, order: list[Ticket], built: list[str], failed: list[str], failures: dict[str, str],
        steps: ChainSteps,
    ) -> None:
        self.run, self.journal = run, journal
        self.chain = [ticket for ticket in order if ticket.id in built]
        self.built, self.failed, self.failures = built, failed, failures
        self.steps = steps

    def verify(self) -> list[str]:
        """Verify every stack of the chain and move the run branch. Return the findings that map to no ticket."""
        try:
            vcs, repo = self.run.vcs, self.run.repo
            # Two reads serve every ticket (#347): the branches that exist, and the branches that the run branch holds. A run
            # branch that does not exist holds nothing.
            existing = vcs.branch_tips(repo).keys()
            try:
                held = vcs.branches_held_by(repo, self.run.workflow.run_branch)
            except VcsError:
                held = frozenset()
            # The clean-up of a run that ended `built` deletes only a ticket branch that the run branch holds (see `_clean_up`).
            self.chain = [
                ticket for ticket in self.chain
                if (branch := ticket_branch(self.run.workflow, ticket)) in existing and branch not in held
            ]
            if not self.chain:
                return []  # the run branch holds the work of every built ticket
            return self._verify_stacks()
        except Exception as error:  # noqa: BLE001 - the journal records it and the command reports it
            if not self.steps.ends_the_run(error):
                raise  # a crash, a pause and a halt are no failure of the chain: `_execute` ends the run process with no `run-end`
            reason = self.steps.halt_reason(error)
            self._fail({ticket.id: reason for ticket in self.chain if ticket.id in self.built})
            return []

    def _fail(self, reasons: dict[str, str]) -> None:
        """Fail the built tickets in `reasons`. The newest `step-end` of a ticket wins, so the journal keeps both."""
        for ticket_id, reason in reasons.items():
            self.journal.append("step-end", ticket=ticket_id, state=run_state.FAILED, reason=reason)
            self.built.remove(ticket_id)
            self.failed.append(ticket_id)
            self.failures[ticket_id] = reason

    def _segments(self, events: list[dict[str, object]], since_retry: list[dict[str, object]]) -> dict[str, ChainSegment]:
        """For each ticket of the chain, the commits that it added: from where its branch started to its tip.

        The start is the `base_commit` of the newest `step-start` of the ticket in
        `events`. The tip is the tip of the branch now. For the last ticket of the
        chain, a resume after a fix-up takes the commit that the first fix-up of
        the current pass (`since_retry`, see `run_state.ChainPass`) started from, so the
        diff of the ticket holds no fix-up commit.
        """
        fixups = [e for e in since_retry if e["event"] == "fixup-start" and "stack" in e]
        segments: dict[str, ChainSegment] = {}
        for ticket in self.chain:
            own = step_scans.events_of(events, ticket.id)
            base = str(own[step_scans.step_start_place(own)]["base_commit"])  # type: ignore[index]
            tip = branch_tip(self.run.vcs, self.run.repo, ticket_branch(self.run.workflow, ticket))
            if fixups and ticket is self.chain[-1]:
                tip = str(fixups[0]["rejected_commit"])
            segments[ticket.id] = ChainSegment(ticket.id, ticket.text, base, tip)
        return segments

    def _verify_stacks(self) -> list[str]:
        self.steps.check_run_branch(self.journal, self.run.vcs, self.run.repo, self.run.workflow.run_branch, self.chain[-1].id)
        every_event = self.journal.events
        # The current pass of the verification of the chain: the events since the last retry or plan change (ADR 0009). A
        # retry at the chain fix-up (#209) goes on with the verification: its rounds and its verdicts still count.
        chain_pass = run_state.fold(every_event).chain
        events = every_event[chain_pass.start :]
        if chain_pass.advanced:
            return []  # a resume after the run branch moved: nothing is left to verify
        stack_passes = {stack_pass.stack: stack_pass for stack_pass in chain_pass.stacks}
        tip_ticket = self.chain[-1]
        self.steps.check_verifier_stop(self.journal, self.run.vcs, self.run.worktree(tip_ticket.id), tip_ticket.id, events)
        segments = self._segments(every_event, events)
        # The chain tip before the first fix-up of any stack. The fix-ups from here to the start of a stack are the
        # fix-ups of the stacks before it, and its full verifier sees those that change a file of its tickets (#93).
        chain_start = segments[tip_ticket.id].tip
        stacks = list(dict.fromkeys(ticket.stack for ticket in self.chain))
        last: ChainStep | None = None
        for position, name in enumerate(stacks):
            tickets = [ticket for ticket in self.chain if ticket.stack == name]
            stack_pass = stack_passes.get(name, run_state.StackPass(name, (), ()))
            verdicts = stack_pass.verdicts
            # A fix-up of the stack uses the strongest specialist tier of the tickets that the verification covers (#231).
            tier = strongest_tier(specialist_tier(self.run.workflow, ticket) for ticket in tickets)
            step = self.steps.make_step(
                self.run, self.journal, Ticket(tickets[-1].id, chain_text(tickets), name, [], tier), tip_ticket,
                tuple(segments[ticket.id] for ticket in tickets),
            )
            last = step
            if verdicts and domain.is_accepted(verdicts[-1]):
                continue  # a resume: an earlier run accepted this stack already
            try:
                self._verify_stack(step, stack_pass.fixup_starts, chain_start, chain_redo(events, tickets[-1].id, name))
            except StepFailed as failed:
                return self._reject(failed, tickets, stacks[position + 1 :])
        assert last is not None
        reason = self.steps.advance_run_branch(last, branch_tip(self.run.vcs, self.run.repo, last.branch))
        if reason:
            self._fail({ticket.id: reason for ticket in self.chain})
        return []

    def _verify_stack(
        self, step: ChainStep, fixup_starts: Sequence[Mapping[str, object]], chain_start: str, redo: ChainRedo | None = None
    ) -> None:
        """Run the gates of one stack on the chain tip, the fix-up rounds, and the verifiers, until a verifier accepts.

        Red gates start a fix-up round with the gate output. Green gates go on to
        the verifier: the full verifier when no verifier rejected the stack, and
        else the scoped verifier of the delta from the rejected commit. A REJECT
        starts a fix-up round. Raise `Rejected` when the verifier rejects after
        the last fix-up round, and `StepFailed` for any other cause, also for
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
        mode = self.run.workflow.mode
        limit = domain.fixup_round_limit(mode)
        round_number = len(fixup_starts)
        used_up = domain.rounds_used_up_reason(mode, round_number)
        if used_up:
            raise StepFailed(used_up)
        # The full verifier also sees each fix-up commit beyond this commit: the tip before the first fix-up of the stack.
        start = str(fixup_starts[0]["rejected_commit"]) if fixup_starts else branch_tip(self.run.vcs, self.run.repo, step.branch)
        earlier = (chain_start, start) if chain_start != start else None
        rejected: str | None = None  # the commit that the last verifier rejected
        findings: list[str] = []
        if redo is not None:
            rejected, findings, round_number = redo.rejected, redo.findings, redo.round
            self._fixup(step, round_number, redo.commit, redo.findings_text, redo.red_gates)
        while True:
            tip = branch_tip(self.run.vcs, self.run.repo, step.branch)
            outcomes = [self.steps.run_gate(step, gate, round_number, GATE_PHASE_CHAIN) for gate in step.stack.gates]
            red = [outcome for outcome in outcomes if not outcome.green]
            if red:
                if round_number >= limit:
                    raise StepFailed(domain.chain_gates_reason(mode, [outcome.label for outcome in red]))
                round_number += 1
                copies = self.steps.copy_gate_logs(red, self.steps.scratch_directory(step.run_dir, step.ticket.id, ROLE_FIXUP))
                self._fixup(step, round_number, tip, self.steps.red_gates_text(tip, [(outcome.command, outcome.output) for outcome in red], copies), True)
                continue
            gates_text = green_gates_text(tip, [(outcome.command, outcome.output) for outcome in outcomes])
            if rejected is None:
                report = self.steps.verify_round(step, round_number, None, gates_text, (start, tip) if start != tip else None, earlier)
            else:
                body = self.steps.checked_fixup_brief(step, round_number, rejected, self.steps.findings_text(findings), gates_text)
                report = self.steps.verify_round(step, round_number, body, gates_text)
            if domain.is_accepted(report.verdict):
                return
            if round_number >= limit:
                raise Rejected(domain.rejection_reason(mode, report.findings), report.findings)
            rejected, findings = tip, report.findings
            round_number += 1
            self._fixup(step, round_number, tip, self.steps.findings_text(findings), False)

    def _fixup(self, step: ChainStep, round_number: int, rejected: str, findings: str, red_gates: bool) -> None:
        """Run one fix-up round on the chain tip, and check its commit. The gates of the chain run after it."""
        trigger = TRIGGER_GATES_RED if red_gates else TRIGGER_REJECT
        findings_text = self.steps.run_fixup(step, round_number, rejected, findings, red_gates, {**step.chain_fields, "trigger": trigger})
        self.steps.checked_fixup_brief(step, round_number, rejected, findings_text)

    def _reject(self, failed: StepFailed, tickets: list[Ticket], unverified: list[str]) -> list[str]:
        """Fail the tickets of a stack that did not pass, and the tickets of the stacks after it. Return the unmapped findings."""
        mapped: dict[str, list[str]] = {}
        unmapped: list[str] = []
        if isinstance(failed, Rejected):
            mapped, unmapped = map_findings(failed.findings, [ticket.id for ticket in tickets])
        reasons = {
            ticket_id: domain.chain_rejection_reason(self.run.workflow.mode, findings) for ticket_id, findings in mapped.items()
        }
        if not reasons:
            reasons = {ticket.id: str(failed) for ticket in tickets}
        stack = tickets[0].stack
        for ticket in self.chain:
            if ticket.stack in unverified:
                reasons[ticket.id] = domain.unverified_chain_reason(stack)
        self._fail(reasons)
        return unmapped
