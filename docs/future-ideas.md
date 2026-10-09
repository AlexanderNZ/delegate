# Future ideas

Each entry on this page is an idea to investigate. It is not a promise and it is not a plan. Nothing here is scheduled, and an entry can change or go away when we learn more. The entries are here so that an idea does not get lost, and does not turn into scope creep in a [**ticket**](glossary.md#ticket) that has another job.

Each entry gives the idea, why it matters, and the open questions.

## A benchmark from real tickets

**The idea.** When everything except the code is fixed, the [**verifier**](glossary.md#verifier) measures the one thing that may vary. The [**journal**](glossary.md#journal) already records each [**verdict**](glossary.md#verdict) and the [**tier**](glossary.md#tier) of each [**run**](glossary.md#run). A [**report**](glossary.md#report) of the [**REJECT**](glossary.md#reject) rate for each tier and each model, on real tickets, would measure models on real work.

**Why it matters.** A public benchmark measures a model on the tasks of someone else. This report would use the repository, the [**gates**](glossary.md#gate) and the tickets of the team that runs it. It would show whether a cheaper model is good enough for that team's work.

**Open questions.**

- How many tickets make a sample worth trusting?
- Tickets differ in difficulty. How does the report keep a hard ticket from counting against the model that happened to get it?
- How does it compare tiers fairly, when a stronger tier also gets the harder tickets?

## Fixing the coordinator role

**The idea.** The [**coordinator**](glossary.md#coordinator) is still held by prose: the [**skill**](glossary.md#skill) and the memory of the [**session**](glossary.md#session). In [the lineage of the kit](explanation/why-the-model-should-not-matter.md#where-each-rule-lived), it is the one rule that is prose at every [**step**](glossary.md#step). Some parts of its job could move into the [**engine**](glossary.md#engine), or into checks.

**Why it matters.** A rule that lives in prose is a rule that a model can forget. Each rule that moved into structure stopped being a lesson that we had to relearn.

**Open questions.** Which parts of the job can move next? Which parts need judgement, and so must stay with a person or a session?

## Further up the ladder

**The idea.** The kit is a slice through levels 2 to 7 of the [**ladder**](glossary.md#ladder). The climb so far went from context engineering to a team of agents with one contract. The next step up might be an automated software factory: work that moves from a settled decision to a merged change with less of me in the loop.

**Why it matters.** Each step so far took a job that I did by hand and gave it to a contract or to the engine. The coordinator role is the largest job left, and level 8 is where Eledath puts agent teams that coordinate themselves.

**Open questions.** Eledath advises against level 8 for now. Which parts of a factory need it, and which parts are levels 6 and 7 done more thoroughly? What evidence would show that a step up keeps the quality, and doesn't only add speed?

## The Cursor adapter

**The idea.** An [**adapter**](glossary.md#adapter) for the `agent` CLI of Cursor, so that the engine drives a third [**harness**](glossary.md#harness). [Issue 18](https://github.com/AlexanderNZ/delegate/issues/18) holds the draft.

**Why it matters.** The engine is the same for each harness, so the kit should reach the tools that teams already use.

**Open questions.** It is undecided. The CLI cannot select a named agent, so the adapter would have to put the agent body in the prompt, and it is not clear that this keeps the contract of a rendered agent.
