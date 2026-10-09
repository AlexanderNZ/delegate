# Why the model should not matter

The model I pick should change one thing: how good the code is. What the agent reads, which tools it may use, how it proves that it's done, and who checks it should be the same for every model. I didn't start from that position. I got there one lesson at a time, and each lesson moved a rule out of prose and into structure. This page tells that story, states the thesis as a table, draws five lessons from it, and ends with my advice on which model should verify.

## How the kit got its shape

I built my own web apps and projects with coding agents, and the kit grew out of what went wrong there. I name no project here, because the projects don't matter. The pattern does.

### First, me at a terminal

Version one was me in a terminal with one coding agent. Do this, do that, write that function, build this feature. It worked, and every rule of the process lived in my head. I was the process.

### Then, subagents and a model router

Next I delegated to subagents, and I got frustrated, because they behaved inconsistently. Two [**sessions**](../glossary.md#session) of the same model, Opus, on two similar [**tickets**](../glossary.md#ticket), each a full vertical slice from the front end to the database. They planned in different ways, used the tools in different ways and wrote code that read differently. A short remark about how I like to write Java changed the code that came back.

My first answer was a delegation [**protocol**](../glossary.md#protocol) and a model router, side by side. The protocol was a page of prose: a [**coordinator**](../glossary.md#coordinator) that splits the work, a [**specialist**](../glossary.md#specialist) that builds one task and doesn't push, and a [**verifier**](../glossary.md#verifier) that checks the diff, [**blind**](../glossary.md#blind) for a risky change. The router picked the best model for each task from benchmarks.

The logic was sound, as far as it went. If the models differ, pick the right one each time. But a benchmark measures the code, and the code was rarely where the work went wrong.

### Then, rules for each model, in prose

So I wrote rules for each model. One vendor's CLI got its own section: concise prompts, and no reference to any [**skill**](../glossary.md#skill). One model got a [**headless**](../glossary.md#headless) profile of its own, because the schemas of its tool servers filled its context in three or four turns. Models below a [**tier**](../glossary.md#tier) got routing rules: paste the exemplar diff into the [**brief**](../glossary.md#brief), flag a test assertion that a change loosened, and escalate after a second rejection.

Read that list again. Each section managed a model like a new colleague: a reminder for the habit I corrected yesterday, and a checklist for the one I expected tomorrow.

Then I reviewed the transcripts. The review found seven patterns of failure, and all seven were behaviour, not code quality. An agent improvised when a tool was missing, where it should have stopped and reported. The coordinator reviewed its own work. A verifier checked a stale diff, from before the rebase. A brief described a visual design in prose, and did not hand over the file. Each pattern got another paragraph of prose. Not one of them was about how good the code was.

### Then, contracts for each role

The turn was to stop writing rules for a model, and to write them for a role. At that point it stopped being about delegation. It was about defining team members who all follow the same rules and behave the same way, whatever model fills the seat. One [**declaration**](../glossary.md#declaration) renders a specialist and its verifier for each [**harness**](../glossary.md#harness), and the verifier carries every skill of the specialist. A [**validator**](../glossary.md#validator) turns the rules into build failures: a specialist that can push, a verifier that can write, and a specialist with no verifier each fail the build. See [why each specialist has its own verifier](why-each-specialist-has-its-own-verifier.md).

Then I deleted the prose sections for each model, because the agent declarations now enforced them.

The [**fix-up**](../glossary.md#fix-up) got the same treatment. A fix-up verifier checks only the [**delta**](../glossary.md#delta), the diff since the rejected commit, and it refuses a history that was amended, because an amend destroys the delta. In the same step, a generator started to write each verifier brief from the ticket and the diff, so no [**report**](../glossary.md#report) of the specialist could reach the verifier.

### Then, the engine

The contracts fixed how each agent behaved. They didn't fix how the agents worked together. One verifier checked one specialist, and then the two played football. Reject. Fix. Reject. Now it passes. A couple of rounds of fix-ups, a re-scope, and a fresh check of the whole change, even when two lines had moved. Work got done very quickly. The quality was inconsistent, and the token bill was massive.

The loop had no stop rule, and I was the one running it, by hand, during a [**run**](../glossary.md#run): rebase before the check, run each [**gate**](../glossary.md#gate), stop after the second rejection, and write down what happened. So I made the work a [**workflow**](../glossary.md#workflow): a simple, sequential list of tickets that the [**engine**](../glossary.md#engine) dispatches one at a time. Each ticket is built, checked and resolved before the next one starts. The engine runs the gates itself, after the rebase. It caps the fix-up rounds. It writes every event to the [**journal**](../glossary.md#journal). And it reaches each harness through an [**adapter**](../glossary.md#adapter), so the same rules hold in Claude Code and in OpenCode.

### What came from others

Two pieces of work shaped these steps, and neither one is mine. Without them I wouldn't have met these problems, and I wouldn't have solved them this way. The page on [prior art](prior-art.md) says what each one gave.

Matt Pocock's [planning skills](https://github.com/mattpocock/skills) came first. They were in use before the delegation protocol existed. [**`grilling`**](../glossary.md#grilling) and [**`wayfinder`**](../glossary.md#wayfinder) settle the decisions, [**`to-spec`**](../glossary.md#to-spec) writes the [**spec**](../glossary.md#spec), and [**`to-tickets`**](../glossary.md#to-tickets) cuts the spec into slices, each one a ticket. Planning and scope were fixed before any of the [**steps**](../glossary.md#step) above, so the kit never had to fix them.

The order of the steps is the order of Bassim Eledath's [**ladder**](../glossary.md#ladder), from his [levels of agentic engineering](https://www.bassimeledath.com/blog/levels-of-agentic-engineering). The rules for each model and the contracts were work on levels 3 to 5: the brief, the written rules, and the skills of each role. The engine is levels 6 and 7, and it came last. It automates only what the levels under it had made explicit, which is what his ladder asks for. The ladder also told me what each problem was. Without it, the history above would read as a pile of fixes. With it, it reads as a climb: from context engineering, to agent teams, and maybe on to something like an automated software factory. Two of his lines also put the lessons below better than I can, and I quote them there.

### Where each rule lived

The picture follows six rules of the kit through the four steps after the terminal. At the terminal every rule lived in my head, so that step has no column. A hollow mark is prose: a model reads it, and can ignore it. A filled mark is structure: code or a check holds it, whatever the model.

![Six rules of the kit as rows, and the four steps of the lineage after the terminal as columns. Five rules start as prose, or do not exist yet, and end as structure. The rule that the coordinator does not implement is prose at every step. The table below gives the same information.](../assets/lineage-light.svg#gh-light-mode-only)
![Six rules of the kit as rows, and the four steps of the lineage after the terminal as columns. Five rules start as prose, or do not exist yet, and end as structure. The rule that the coordinator does not implement is prose at every step. The table below gives the same information.](../assets/lineage-dark.svg#gh-dark-mode-only)

The same picture as a table:

| Rule | 1. Model router | 2. Rules for each model | 3. Contracts for each role | 4. The engine |
| --- | --- | --- | --- | --- |
| The specialist does not push | Prose | Prose | Structure | Structure |
| The verifier is blind | Prose | Prose | Structure | Structure |
| The verifier holds the skills of the specialist | Not yet a rule | Prose | Structure | Structure |
| A fix-up is a new commit | Not yet a rule | Not yet a rule | Structure | Structure |
| The gates run outside the specialist | Prose | Prose | Prose | Structure |
| The coordinator does not implement | Prose | Prose | Prose | Prose |

## What varies between models, and what fixes it

Give two models the same ticket, and five things come back different. Four of them are about how the agent works. The fifth is the code. The kit fixes the first four, and leaves the fifth free.

| What varies | How the kit fixes it |
| --- | --- |
| Context | The brief holds the ticket, the [**file boundary**](../glossary.md#file-boundary) and the gates, and nothing else. The [**context skill**](../glossary.md#context-skill) reads the [**delegation document**](../glossary.md#delegation-document) live, so every agent reads the same rules. |
| Planning and scope | Matt Pocock's planning skills fix it before the kit sees the work: `grilling` and `wayfinder` settle the decisions, and `to-spec` and `to-tickets` cut the work to size. In the kit, the file boundary and each [**hotspot**](../glossary.md#hotspot) hold the scope. |
| Tool use | The declaration gives each role its tools. A [**push guard**](../glossary.md#push-guard) in git refuses every push, and the [**verifier guard**](../glossary.md#verifier-guard) permits a fixed list of commands. |
| The claim of "done" | The report is a claim, never evidence. The engine runs the gates itself, and a blind verifier gives the [**verdict**](../glossary.md#verdict). |
| The code | Nothing. It is free on purpose: it is the thing I pick a model for, with the tier. |

Change the model, and the first four rows stay as they are. Only the last row moves. That is the whole thesis, and it can be proven wrong: if a change of model changes anything in the first four rows, the kit has a hole. Found one? Open an issue.

## Five lessons

### Constraints beat instructions

Bassim Eledath puts it in one line: "Constraints > instructions." His reason is that "defining boundaries works better than giving checklists, because agents fixate on the list and ignore anything not on it." I agree, and the lineage adds a reason of its own. How seriously a model obeys prose is itself one of the ways that models differ. So a rule in prose is only as strong as the model that reads it, and it needs a new test with each new model. A rule in structure, such as a validator finding, a [**`pre-push` hook**](../glossary.md#pre-push-hook) or a gate that the engine runs, is the same for every model. It does not need the model to agree.

### A rule for one model is a warning sign

Each section that I wrote for one model was a sign that the process leaked. But not every difference between models is behaviour. Some of it is ability. A weaker model writes weaker code, and a large ticket overwhelms it sooner.

So split the two. Match ability with the tier and with the size of the ticket. Fix behaviour with contracts. When I catch myself writing a rule that starts with the name of a model, I ask which of the two I'm looking at. If it is ability, change the tier or cut the ticket smaller. If it is behaviour, the rule belongs in a contract, where every model meets it.

### The coordinator's conversation is a hidden input

I saw this before I understood it: a short remark about how I like to write Java changed the code that came back. A coordinator session writes a brief at the end of a long conversation. A remark from an hour ago, a correction that it half remembers, a file that it read and I didn't: all of it shapes the brief, and none of it is on the page afterwards. Two runs of the same ticket are not the same run when the conversations differ. And nobody can review an input they can't read.

So the inputs of a run are files: the ticket, the workflow, the delegation document, and the brief that the engine writes from them. Each one can be read after the run, and the run can go again. Every model gets the same inputs.

### The twin gets the same textbook, but sits a separate exam

Bassim Eledath is blunt about review: "if the same model instance implements and evaluates its own work, it's biased." His rule for it is "Don't let the same model grade its own exam — separate the implementer from the reviewer".

The verifier answers that in two halves. It gets the same textbook: every skill of the specialist, so it judges the work by the same standard. And it sits a separate exam: a fresh session, no report, the gates run again in a [**temporary copy**](../glossary.md#temporary-copy) that it may break, and a [**red proof**](../glossary.md#red-proof) that each new test can fail. Shared knowledge. Separate work.

### The coordinator is the part not yet fixed

Look at the last row of the picture. The specialist and the verifier are contracts, and the engine is code. The coordinator is still prose. It splits the work, writes the workflow, picks the tiers and decides the merge, and the rule that it does not implement is a sentence in a skill that nothing checks.

When the coordinator is me, that is fine: the merge is mine, and so is the accountability. When the coordinator is a model in an interactive session, its behaviour still varies with the model, and nothing in the kit holds it yet. I do not have the answer. It is the next lesson, and I expect to learn it the way I learnt the others: in a transcript.

## Which model verifies: a recommendation, not a rule

My recommendation is a verifier one tier above the specialist. A mid tier builds, and a top tier verifies. That is what [**`economy`**](../glossary.md#economy) [**mode**](../glossary.md#mode) does with the bundled [**tier table**](../glossary.md#tier-table): the specialist runs on `standard`, and the verifier runs on `verifier`. My reason is simple. A reviewer that is a little stronger than the author has a chance to see what the author could not.

Two things are acceptable that I might be expected to forbid.

- The same model grading the same model is acceptable. In [**`assure`**](../glossary.md#assure) mode, the bundled table puts `strong` and `verifier` on the same model. The verifier still sits a separate exam: a fresh session, blind, with its own gates and its own red proof. Bassim Eledath allows this too: "Have a different model (or a different instance with a review-specific prompt) do the review pass."
- A different model family is not required. Not every user has more than one family. A [**gateway**](../glossary.md#gateway) can serve one vendor only, and a kit that needs two families would leave those users out. A second family can go behind the `verifier` tier in a [**tier file**](../glossary.md#tier-file). That is a choice for each team, not a rule of the kit.

Here my work and Bassim Eledath's part ways. He argues for model diversity in each role: "use different models for different jobs", because "the cumulative output is stronger than any single model working alone." I do not argue with that. This work does something else. It fixes the process so that model diversity is safe and measurable. Change the model behind one tier, and the brief, the gates and the check stay the same. Any difference in the journal, in [**REJECT**](../glossary.md#reject) verdicts and in fix-up rounds, is then the difference that the model made. A second family can be tried with evidence, not with faith.

The recommendation is not enforced. The engine enforces one rule about the model of the verifier, and it is the rule that [the workflow reference](../reference/workflow.md#tier-overrides) states: the verifier tier never goes down. In a workflow, `tier-overrides.verifier` must name `strong` or `verifier`. A weaker tier, `standard` or `cheap`, is an error when the engine loads the file, and `--dry-run` reports it. Nothing checks that the verifier is one tier above the specialist, or that it comes from another family.

What to do today:

1. Start with the bundled tier table, and run a few tickets.
2. Read the journal. A run of REJECT verdicts and fix-up rounds on small tickets says that the specialist is short of ability for the size of the tickets. Cut the tickets smaller, or raise the tier.
3. To follow the recommendation in `assure` mode, move the specialist one tier down in the workflow:

```toml
[tier-overrides]
specialist = "standard"
```

To point the tiers at other models, see [how to point the tiers at a gateway](../how-to/point-the-tiers-at-a-gateway.md).
