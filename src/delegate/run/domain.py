"""The rules of a run: the domain module of the run context.

The modes, the limits of fix-up rounds and continuations, the dependency order
of the tickets, the skip rule, the start of a ticket in a chain, the handling
of a verdict, and the progress and the ETA of a run are here. Each rule is a function of plain values: strings,
numbers and lists. The module imports nothing from the package and nothing that
does I/O. It has no git, no harness, no file system and no subprocess, and
`tests/test_architecture.py` checks that. The engine reads and writes the world,
and asks this module what the rules say.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

ASSURE: str = "assure"
ECONOMY: str = "economy"

# The two modes of a run.
MODES: tuple[str, ...] = (ASSURE, ECONOMY)

# The tier of the specialist role in each mode, before a workflow override.
SPECIALIST_TIER: dict[str, str] = {ASSURE: "strong", ECONOMY: "standard"}

# The tier of the verifier role, before a workflow override. It is the same in every mode.
VERIFIER_TIER: str = "verifier"

# A REJECT starts at most this many fix-up rounds. Each round is one fix-up
# commit and one fresh verifier. The limit depends on the mode: `assure` verifies
# each ticket, and `economy` verifies each stack once, at the end of the chain.
FIXUP_ROUND_LIMIT: dict[str, int] = {ASSURE: 2, ECONOMY: 1}

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
    """The ticket that the next ticket starts from, or None when it starts from the base branch.

    In `assure` mode every ticket starts from the base branch. In `economy`
    mode the tickets form a chain: a ticket starts from the last ticket before
    it, in the order of the plan, that was built. A ticket that failed or was
    skipped is not part of the chain.
    """
    if mode == ECONOMY:
        previous = [ticket_id for ticket_id in order if ticket_id in built]
        if previous:
            return previous[-1]
    return None


def work_starts_at(mode: str, base_branch: str, base_commit: str) -> str:
    """The commit or branch from which the commits of a ticket are its own work.

    In a chain the branch of a ticket starts at the tip of the previous ticket,
    so the commits of earlier tickets are not counted. Otherwise the work
    starts at the base branch.
    """
    return base_commit if mode == ECONOMY else base_branch


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
