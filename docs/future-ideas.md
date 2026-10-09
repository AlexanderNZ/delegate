# Future ideas

Each entry on this page is an idea to investigate. It is not a promise and it is not a plan. Nothing here is scheduled, and an entry can change or go away when we learn more. The entries are here so that an idea does not get lost, and does not turn into scope creep in a ticket that has another job.

Each entry gives the idea, why it matters, and the open questions.

## A benchmark from your own tickets

**The idea.** When everything except the code is fixed, the [**verifier**](glossary.md#verifier) measures the one thing that may vary. The [**journal**](glossary.md#journal) already records each [**verdict**](glossary.md#verdict) and the tier of each run. A report of the [**REJECT**](glossary.md#reject) rate for each tier and each model, on real tickets, would measure models on your own work.

**Why it matters.** A public benchmark measures a model on the tasks of someone else. This report would use your repository, your gates and your tickets. It would show whether a cheaper model is good enough for the work that you do.

**Open questions.**

- How many tickets make a sample that you can trust?
- Tickets differ in difficulty. How does the report keep a hard ticket from counting against the model that happened to get it?
- How does it compare tiers fairly, when a stronger tier also gets the harder tickets?

## Fixing the coordinator role

**The idea.** The [**coordinator**](glossary.md#coordinator) is still held by prose: the skill and the memory of the session. In [the lineage of the kit](explanation/why-the-model-should-not-matter.md#where-each-rule-lived), it is the one rule that is prose at every step. Some parts of its job could move into the [**engine**](glossary.md#engine), or into checks.

**Why it matters.** A rule that lives in prose is a rule that a model can forget. Each rule that moved into structure stopped being a lesson that we had to relearn.

**Open questions.** Which parts of the job can move next? Which parts need judgement, and so must stay with a person or a session?

## The Cursor adapter

**The idea.** An [**adapter**](glossary.md#adapter) for the `agent` CLI of Cursor, so that the engine drives a third [**harness**](glossary.md#harness). [Issue 18](https://github.com/AlexanderNZ/delegate/issues/18) holds the draft.

**Why it matters.** The engine is the same for each harness, so the kit should reach the tool that you already use.

**Open questions.** It is undecided. The CLI cannot select a named agent, so the adapter would have to put the agent body in the prompt, and it is not clear that this keeps the contract of a rendered agent.
