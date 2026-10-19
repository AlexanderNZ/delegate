"""The rules of a run: the domain module of the run context.

The modes, the limits of fix-up rounds and continuations, the dependency order
of the tickets, the skip rule, the start of a ticket in a chain, the handling
of a verdict, the rules of a plan change, and the progress and the ETA of a run are here. Each rule is a function of plain values: strings,
numbers and lists. The module imports nothing from the package and nothing that
does I/O. It has no git, no harness, no file system and no subprocess, and
`tests/test_architecture.py` checks that. The engine reads and writes the world,
and asks this module what the rules say.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import NamedTuple

ASSURE: str = "assure"
ECONOMY: str = "economy"

# The two modes of a run.
MODES: tuple[str, ...] = (ASSURE, ECONOMY)

# The values of the workflow field `ticket-start`: the branch that each ticket branch of `assure` mode starts from.
# `base-branch` is the default: a ticket branch starts from the base of the run, the commit of the base branch at
# `run-start`. With `run-branch`, a ticket branch starts from the tip of the run branch at the start of its step, so it
# holds the accepted work of the tickets before it. A ticket with `blocked-by` starts from the run branch with both values.
START_BASE_BRANCH: str = "base-branch"
START_RUN_BRANCH: str = "run-branch"
TICKET_STARTS: tuple[str, ...] = (START_BASE_BRANCH, START_RUN_BRANCH)

# The tier of the specialist role in each mode, before a workflow override.
SPECIALIST_TIER: dict[str, str] = {ASSURE: "strong", ECONOMY: "standard"}

# The tier of the verifier role, before a workflow override. It is the same in every mode.
VERIFIER_TIER: str = "verifier"

# A REJECT starts at most this many fix-up rounds. Each round is one fix-up
# commit and one fresh verifier. The limit depends on the mode: `assure` verifies
# each ticket, and `economy` verifies each stack once, at the end of the chain.
# In `economy` mode, red gates on the tip of the chain also start a fix-up round,
# and the rounds of red gates and of a REJECT count together against the limit.
FIXUP_ROUND_LIMIT: dict[str, int] = {ASSURE: 2, ECONOMY: 2}

# The `phase` of a gate run on the tip of an `economy` chain, in the journal.
GATES_OF_CHAIN: str = "chain"

# The specialist of a ticket continues in the same worktree at most this many
# times after it ends capped or failed, or after the gates are red. One count
# covers all three causes. The limit depends on the mode: `assure` pays for
# more assurance, `economy` for fewer tokens.
CONTINUATION_LIMIT: dict[str, int] = {ASSURE: 2, ECONOMY: 1}

# After its report is valid, an agent session that writes no event for this
# many seconds is idle, and the engine ends it. A foreground command of an agent
# writes no event while it runs, so the time is longer than the ten minutes
# that a harness gives one such command at most.
IDLE_TIMEOUT_SECONDS: int = 900

# The two verdicts of a verifier.
ACCEPT: str = "ACCEPT"
REJECT: str = "REJECT"
VERDICTS: tuple[str, ...] = (ACCEPT, REJECT)

# The status of a specialist report that says the work is committed.
COMMITTED: str = "committed"


def role_tier(mode: str, role: str, overrides: Mapping[str, str]) -> str:
    """The tier of a role: the override of the workflow, or the default of the role in the mode."""
    default = VERIFIER_TIER if role == "verifier" else SPECIALIST_TIER[mode]
    return overrides.get(role, default)


def fixup_round_limit(mode: str) -> int:
    """The number of fix-up rounds that a REJECT can start in the mode."""
    return FIXUP_ROUND_LIMIT[mode]


def gates_each_ticket(mode: str) -> bool:
    """Tell whether the engine runs the gates after the build of each ticket.

    In `assure` mode it does. In `economy` mode it runs the gates once for each
    stack, on the tip of the chain, before the verifier of the stack.
    """
    return mode == ASSURE


def continuation_limit(mode: str) -> int:
    """The number of times a specialist can continue in the mode."""
    return CONTINUATION_LIMIT[mode]


def may_continue(mode: str, continuations: int) -> bool:
    """Tell whether a specialist that continued `continuations` times can continue once more."""
    return continuations < continuation_limit(mode)


def dependency_order(tickets: Sequence[tuple[str, Sequence[str]]]) -> list[str]:
    """The ticket ids so that each one follows every ticket that blocks it.

    `tickets` holds the id of each ticket with the ids that block it, in the
    order of the file. Among tickets that are ready at the same time, the order
    of the file wins. Raise ValueError on a cycle, and name the tickets that wait.
    """
    done: list[str] = []
    done_ids: set[str] = set()
    pending = list(tickets)
    while pending:
        ready = next((ticket for ticket in pending if set(ticket[1]) <= done_ids), None)
        if ready is None:
            raise ValueError(f"tickets {[ticket_id for ticket_id, _ in pending]} wait on each other")
        pending.remove(ready)
        done.append(ready[0])
        done_ids.add(ready[0])
    return done


def unbuilt_blockers(blocked_by: Sequence[str], built: Collection[str]) -> list[str]:
    """The blockers of a ticket that are not built, in the order of the ticket."""
    return [blocker for blocker in blocked_by if blocker not in built]


def skip_reason(blocked_by: Sequence[str], built: Collection[str], failed: Collection[str]) -> str | None:
    """The reason to skip a ticket, or None when every blocker is built.

    A ticket with a blocker that is not built is skipped. A blocker that failed
    is named as failed, and any other is named as skipped. A ticket with no
    failed blocker still runs: the rule looks at the blockers of the ticket only.
    """
    blockers = unbuilt_blockers(blocked_by, built)
    if not blockers:
        return None
    return "blocked by " + "; ".join(f"{blocker}, which {'failed' if blocker in failed else 'was skipped'}" for blocker in blockers)


def unreached_reason(ticket_id: str, halted: bool) -> str:
    """The reason to skip a ticket that the run did not reach: the run ended at `ticket_id`, which halted it or crashed."""
    return f"the run ended after ticket {ticket_id} {'halted the run' if halted else 'crashed'}"


def chain_predecessor(mode: str, order: Sequence[str], built: Collection[str]) -> str | None:
    """The ticket that the next ticket starts from, or None when it starts from the run (see `starts_from_run_branch`).

    In `assure` mode no ticket starts from another ticket. In `economy`
    mode the tickets form a chain: a ticket starts from the last ticket before
    it, in the order of the plan, that was built. A ticket that failed or was
    skipped is not part of the chain.
    """
    if mode == ECONOMY:
        previous = [ticket_id for ticket_id in order if ticket_id in built]
        if previous:
            return previous[-1]
    return None


def starts_from_run_branch(mode: str, ticket_start: str, blocked_by: Sequence[str] = ()) -> bool:
    """Tell whether a ticket branch starts from the tip of the run branch, and not from the base of the run.

    The base of the run is the commit of the base branch at `run-start`. No
    ticket branch starts from the base branch itself: other work can land on
    it while the run goes on.

    In `assure` mode a ticket starts from the run branch when the workflow field
    `ticket-start` is `run-branch`, or when the ticket has `blocked-by`: its
    blockers are accepted, and the run branch holds their commits. In `economy`
    mode the chain sets the start of a ticket.
    """
    return mode == ASSURE and (ticket_start == START_RUN_BRANCH or bool(blocked_by))


def work_starts_at(base_commit: str, rebased_onto: str | None = None) -> str:
    """The commit from which the commits of a ticket are its own work.

    It is the commit where the branch of the ticket started, `base_commit`:
    the base of the run, the tip of the run branch, or the tip of the previous
    ticket of a chain. After a rebase onto the run branch, it is the commit of
    the run branch that the branch was rebased onto, `rebased_onto`. So the
    commits of earlier tickets, and the commits of the base branch, are not counted.
    """
    return rebased_onto if rebased_onto is not None else base_commit


def is_accepted(verdict: str) -> bool:
    """Tell whether a verdict lets the branch through."""
    return verdict == ACCEPT


def verification_rounds(mode: str, first_round: int) -> range:
    """The rounds of verification of a step: round 0 is the full pass, and each later round follows a fix-up.

    A resume sets `first_round` to the number of verdicts that the journal
    holds, so the limit counts across the stop. A verifier run that ended with
    no verdict does not count. The range is empty when those verdicts use up the
    fix-up rounds of the mode.
    """
    return range(first_round, fixup_round_limit(mode) + 1)


def rounds_used_up_reason(mode: str, first_round: int) -> str | None:
    """The reason a resumed step fails at once, or None when a round is left.

    The reason holds when the verifier runs with a verdict of the stopped run are
    more than the fix-up rounds of the mode.
    """
    limit = fixup_round_limit(mode)
    if first_round > limit:
        return f"the {first_round} verifier runs of the stopped run use up the {limit} fix-up rounds"
    return None


def _rounds_text(mode: str) -> str:
    limit = fixup_round_limit(mode)
    return f"{limit} fix-up round{'' if limit == 1 else 's'}"


def rejection_reason(mode: str, findings: Sequence[str]) -> str:
    """The reason a step fails when the verifier rejects the branch after the last fix-up round."""
    return f"the verifier rejected the branch after {_rounds_text(mode)}: " + "; ".join(findings)


def chain_rejection_reason(mode: str, findings: Sequence[str]) -> str:
    """The reason a ticket of a chain fails when the verifier rejects the chain after the last fix-up round."""
    return f"the verifier rejected the chain after {_rounds_text(mode)}: " + "; ".join(findings)


def chain_gates_reason(mode: str, red: Sequence[str]) -> str:
    """The reason a ticket of a chain fails when the gates on the tip of the chain are red after the last fix-up round."""
    return f"gates red on the tip of the chain after {_rounds_text(mode)}: " + "; ".join(red)


def unverified_chain_reason(stack: str) -> str:
    """The reason a ticket of a later stack fails when the verification of `stack` failed first."""
    return f"not verified: the chain ended when the verification of the stack {stack!r} failed"


def unfinished_reason(status: str, blocked_reason: str | None, who: str) -> str | None:
    """The reason to fail a step when the report of `who` does not say committed, or None when it does."""
    if status == COMMITTED:
        return None
    detail = f": {blocked_reason}" if blocked_reason else ""
    return f"{who} reported status {status!r}{detail}"


# The phases of a running ticket, and the part of one ticket that each phase counts for the progress of a run,
# in tenths. A ticket that ended counts 10 tenths. Whole numbers keep the percentage exact.
PHASE_SPECIALIST: str = "specialist"
PHASE_GATES: str = "gates"
PHASE_VERIFIER: str = "verifier"
PHASE_FIXUP: str = "fix-up"
PHASE_TENTHS: dict[str, int] = {PHASE_SPECIALIST: 0, PHASE_GATES: 6, PHASE_VERIFIER: 8, PHASE_FIXUP: 8}
ENDED_TENTHS: int = 10


def chain_units(mode: str) -> int:
    """The units of the progress of a run that are not tickets: 1 for the verification of the chain in `economy` mode.

    In `economy` mode the gates and the verifier of the chain run after the last ticket ended. For the percentage and the
    ETA, the verification of the chain, with its gates, is one more unit of the run, as a ticket is: it ends only when the
    run ends.
    """
    return 1 if mode == ECONOMY else 0


def percent_complete(ended: int, running_phases: Sequence[str], total: int) -> int:
    """The percentage of a run that is complete, rounded down.

    A ticket that ended counts 1, and a running ticket counts the part of its
    phase (see `PHASE_TENTHS`). The sum, divided by the ticket count, times 100.
    A run with no ticket is complete.
    """
    if total == 0:
        return 100
    tenths = ended * ENDED_TENTHS + sum(PHASE_TENTHS[phase] for phase in running_phases)
    return tenths * 100 // (total * ENDED_TENTHS)


def seconds_to_end(durations: Sequence[float], not_ended: int, running_seconds: float) -> int | None:
    """The seconds from now to the estimated end of a run, or None before the first ticket ends.

    `durations` holds the seconds of each ticket that ended after a step.
    The estimate is the mean of them, times the tickets that did not end, minus
    the seconds that the running ticket used. It is never less than 0.
    """
    if not durations:
        return None
    mean = sum(durations) / len(durations)
    return max(0, int(mean * not_ended - running_seconds))


# The rules of a plan change: a resume with a workflow whose tickets differ from the newest plan of the run (#107).
# Each rule has a number, and the dry run of a resume prints the verdict of each one.
PLAN_RULES: dict[int, str] = {
    1: "the built part is fixed",
    2: "a new ticket goes after the built part",
    3: "a ticket that has not started can change",
    4: "no built work is changed",
    5: "a new ticket gets its own gates and verifier",
    6: "the journal records the change",
    7: "a ticket that has started cannot be removed",
    8: "the coordinator reviews the later tickets",
    9: "the brief of each later ticket gets the section Plan change",
}

# The value of `--reviewed` that covers every later ticket.
REVIEWED_ALL: str = "all"


class PlanTicket(NamedTuple):
    """One ticket of a plan, as the journal records it: the id, the stack, the blockers, and the SHA-256 of the text."""

    id: str
    stack: str
    blocked_by: tuple[str, ...]
    text_sha256: str


# The fields of a ticket that a plan change compares, with the name that a message gives each one.
PLAN_FIELDS: tuple[tuple[str, str], ...] = (("text_sha256", "text"), ("stack", "stack"), ("blocked_by", "blocked-by"))


class PlanCheck(NamedTuple):
    """The result of the rules of a plan change.

    `fixed` is the built part: the tickets of the old order up to the last
    ticket that started. `changed` maps each changed ticket to the names of
    its changed fields. `later` holds each ticket that has not started and
    comes after an added or changed ticket, in the new order; an added ticket
    is not in it. `problems` maps a rule number to the messages of that rule
    that refuse the change.
    """

    fixed: list[str]
    added: list[str]
    changed: dict[str, list[str]]
    removed: list[str]
    later: list[str]
    problems: dict[int, list[str]]


def check_plan_change(old: Sequence[PlanTicket], new: Sequence[PlanTicket], started: Collection[str]) -> PlanCheck:
    """Apply the rules of a plan change to the old plan and the new plan, each in plan order.

    `started` holds the ids of the tickets with a `step-start`. The built part
    is fixed: each ticket in it keeps its id, its text, its stack, its
    blockers and its place, and the new order starts with it. A ticket in the
    built part that did not start (a ticket that the run skipped) is fixed
    too, because the run went past it. A new ticket goes after the built part.
    A ticket after the built part can change or go.
    """
    old_ids, new_ids = [t.id for t in old], [t.id for t in new]
    by_id = {t.id: t for t in new}
    last = max((old_ids.index(i) for i in started if i in old_ids), default=-1)
    fixed = old_ids[: last + 1]
    added = [i for i in new_ids if i not in old_ids]
    removed = [i for i in old_ids if i not in new_ids]
    changed: dict[str, list[str]] = {}
    for ticket in old:
        if ticket.id in by_id:
            fields = [name for attribute, name in PLAN_FIELDS if getattr(ticket, attribute) != getattr(by_id[ticket.id], attribute)]
            if fields:
                changed[ticket.id] = fields
    problems: dict[int, list[str]] = {}
    last_started = fixed[-1] if fixed else None
    for ticket_id in fixed:
        where = "has started" if ticket_id in started else f"is in the built part, before ticket {last_started!r}, which has started"
        if ticket_id in removed:
            rule = 7 if ticket_id in started else 1
            problems.setdefault(rule, []).append(f"ticket {ticket_id!r} {where}; it cannot be removed")
        elif ticket_id in changed:
            problems.setdefault(1, []).append(f"ticket {ticket_id!r} {where}; its {', '.join(changed[ticket_id])} changed")
    if not problems and new_ids[: len(fixed)] != fixed:
        end = new_ids.index(last_started) if last_started in new_ids else len(new_ids)
        early = [i for i in new_ids[:end] if i in added]
        if early:
            problems[4] = [
                f"ticket {i!r} is new and comes before ticket {last_started!r}, which has started; "
                "a new ticket cannot go before built work: retry the step, or start a new run"
                for i in early
            ]
        else:
            problems[2] = [f"the new order {', '.join(new_ids)} does not start with the built part {', '.join(fixed)}"]
    moved = [i for i in new_ids if i in added or i in changed]
    later: list[str] = []
    if moved:
        first = new_ids.index(moved[0])
        later = [i for i in new_ids[first + 1 :] if i not in added and i not in started]
    return PlanCheck(fixed, added, changed, removed, later, problems)


def unreviewed(later: Sequence[str], reviewed: Collection[str]) -> list[str]:
    """The later tickets that `reviewed` does not cover, in plan order. `REVIEWED_ALL` covers every one."""
    if REVIEWED_ALL in reviewed:
        return []
    return [ticket_id for ticket_id in later if ticket_id not in reviewed]
