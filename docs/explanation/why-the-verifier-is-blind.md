# Why the verifier is blind, and why it runs the gates

A verifier in this kit gets two things: the ticket and the diff. It never gets the specialist's report. The verifier also runs the gates, and the engine runs them first. I made both choices on purpose, and this page gives my reasons.

## The report is a claim

When a specialist finishes, it writes a report. The report says what the specialist did, which gates passed, and what it could not check. That text is useful to me. It is not evidence. The specialist wrote it, and the specialist wants to be done.

Many review setups give the report to the reviewer. The reviewer then read the diff with the story already in mind. A good reviewer still finds some defects. But the story sets where the reviewer looks. If the report says "I fixed the empty-list case", the reviewer checks the empty-list case. The case that the specialist did not think of gets no attention, because the report never named it.

I call this anchoring. A fresh reviewer needs a fresh question: does this diff do what the ticket asks? The verifier cannot answer that question well when it has read an answer already. So the engine builds the verifier brief from the ticket and the three-dot diff, and from nothing that the specialist wrote. The [agent-delegation skill](../../skills/agent-delegation/SKILL.md#the-invariants-of-every-mode) lists this as the first invariant of every mode, and no setting turns it off.

Blindness costs something. The verifier does not know the intent behind a strange line, so it may flag a line that is correct. I accept that. A false finding costs one fix-up round. A defect that nobody looked for costs more, and it costs it later.

## The gate result is a claim too

A report also says "the gates are green". I do not take that word either. The agent may have run a different command, it may have run the gates before its last edit, or it may have run them in a state that the branch does not hold.

So the engine runs the gates itself, after the specialist finishes, in the worktree of the specialist. The result is the exit code of the engine's own command. It does not come from the agent that wrote the code. The engine runs them a second time after the rebase in `assure` mode, because the rebase can change the result.

## Why the verifier runs the gates as well

If the engine runs the gates, why does the verifier run them?

The reason is the red proof. A verifier that only reads a test cannot tell whether the test can fail. A test that passes before and after a change protects nothing. To know that a test is not decoration, the verifier breaks the code the test covers and watches the test go red. That means the verifier must run the test.

The verifier must not break the real worktree. So the engine prepares a temporary copy of the branch and passes its path in the brief. The verifier may edit the copy, run the gates in it, and throw it away. After the verifier ends, the engine checks that the real worktree has the same HEAD and the same status as before. A difference is an invariant violation, and the run stops. I do not rely on the verifier's promise to stay read-only. I rely on the check.

## What this does not give you

Blind verification is not a proof. The verifier is a model, and it can miss a defect. The engine's checks are only as good as your gates. If your gates do not test a behaviour, a green gate says nothing about it. The kit gives you independent evidence for each merge. It does not give you certainty.

For the limits of the guards that keep the verifier read-only, see [the enforcement model and its limits](the-enforcement-model-and-its-limits.md). For the engine, the reports and the journal, see [the `delegate run` reference](../reference/run.md).
