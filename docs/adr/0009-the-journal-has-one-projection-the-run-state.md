# ADR 0009: The journal has one projection: the run state

- Status: accepted
- Date: 2026-11-14
- Applies to: the engine, `delegate status`, `delegate watch` and `delegate runs`

## Context

The journal of a run is the record of the run. Five places read it and fold it into a state, each with its own rules. The engine has `restore_progress` (the outcome of each ticket and the open step at a resume) and `_verify_stacks` (the start of the current pass of the chain verification, and the verdicts and the fix-up rounds of each stack in it). `delegate status`, and `watch` and `runs` through `snapshot_at`, have `_ticket`, `_since_chain_restart`, and the helpers `newest_run_end`, `is_paused`, `halt_of`, `crash_of` and `setup_problem_of`.

When two folds have different rules, the engine does one thing and a view shows another. Ticket #304 was such a case: the views did not use the start of the pass that the engine used. Three differences were in the code when this ADR was written:

1. A plan change took a removed ticket, and a changed ticket that was skipped, out of the skipped tickets in memory only. `restore_progress` did not apply the `plan-change` event, so a resume after a crash, before the next `step-start`, read that ticket as skipped again.
2. `status` read a ticket with an `unskip` as pending. `restore_progress` read it as skipped until its `step-start`.
3. `status` ended a run with a `run-end` after the newest `resume`. The engine ended a run with a `run-end` after the newest `plan-change`.

## Decision

The journal has one projection: the **run state**. It is one pure function of the events of a journal, `run_state.fold`, in the module `delegate.run.run_state`. The value that it gives is frozen. It holds, for each ticket of the newest plan, the outcome (`pending`, `open`, `built`, `failed` or `skipped`), the reason, and whether the skip rule skipped the ticket; the open ticket; the state of the run (active, ended, paused, halted, crashed, or stopped by the check of the setup) with the event that gives it; and the current pass of the chain verification.

The engine and each view decide the state of a run and of its tickets from the run state. A later ticket moves each caller, one at a time. The run state at an earlier time is the run state of the events up to that time.

The module is pure, as the domain module is: it imports no other module of `delegate`, and `tests/test_architecture.py` lists it in `PURE_MODULES`. `starts_chain_verification`, the rule of the start of a pass, moved into it from the domain module for that reason.

The run state has one rule for each difference above:

1. A `plan-change` takes each removed ticket, and each changed ticket, out of the skipped tickets.
2. An `unskip` makes the ticket pending.
3. A `run-end` ends the run when no `resume` comes after it. The engine writes `plan-change` only after `resume`, so this rule and the old rule of the engine agree on each journal that the engine writes.

The facts that are not in the journal stay outside the run state: the lock of a live process, a pause request, the event streams, and the clock. A caller adds them to the run state. `snapshot_at` does it already for the first two and for the clock.

## Rejected alternatives

- **A second record of the state on the disk.** Two records of one run can disagree, and a write that fails would lose a fact. [ADR 0008](0008-the-run-index-is-derived-from-the-journals.md) gives the same reason for the run index.
- **A fold in each view.** This is the state before this ADR. Each fold has its own rules, and they drift apart, as #304 showed.

## Consequences

- A rule of the state of a run is in one place. A change of the rule changes the engine and each view together.
- The agreement test in `tests/run_state/` compares the run state with `restore_progress` and with `snapshot_at` on a table of journals. It names the three differences above, so the move of a caller shows each change of behavior.
- The outcome of a ticket with a `retry` stays `failed` or `built` until its `step-end`, as the engine reads it. The field `retrying` of the ticket tells a view to show it as running.
- The run state holds the tickets of the newest plan only. A ticket that a plan change removed is not in it, but `restore_progress` still lists it as built or failed.
