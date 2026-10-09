# Start here

Models differ the way people do. Give one model the same task twice, and you get two plans, two ways to use the tools, two readings of the context, and two different pieces of code. One short remark early in the conversation changes what comes back.

`delegate` exists so that the model you pick changes one thing only: how good the code is. This page is the kit at a glance: the problem, the idea, a picture of where the kit sits, and how one piece of work moves from start to merge. It is not a tutorial and it is not a reference. It links both at the end. Each term is in bold where it first appears, and it links its entry in the [glossary](glossary.md).

## The problem

We met this problem as we built our own web apps and projects with coding agents. Each model had to be managed like a new colleague. You explain in prose how the team works. You correct one habit, and the next [**session**](glossary.md#session) shows a different one. The review loop goes back and forth, and each round is about how to work, not about the code.

Bassim Eledath names the cause in one sentence: "These models were post-trained differently and have meaningfully different dispositions." He puts those differences to work, and for exploration they help. For the build of one piece of work, a difference in disposition is noise. You want to choose a model for its ability, and get the same way of working every time.

The pain shows up in four places, whatever [**harness**](glossary.md#harness) runs the agents: Claude Code, OpenCode, or the `agent` CLI of Cursor.

### Trust arrives too late

One session runs many implementer agents, and a reviewer reads one large diff at the end. The reviewer takes the implementer's word that the tests pass, but "the tests pass" is a claim, not evidence. A reviewer that reads the implementer's account also looks where the account points, so the case that nobody thought of gets no attention. And the fix loop has no stop rule. What comes back is a draft, and you finish it yourself.

### The scripts belong to one harness

An orchestration script for one harness does not run in another. It names models, so a new [**gateway**](glossary.md#gateway) or vendor changes every script. It also fails in the ways that the harness fails: a [**worktree**](glossary.md#worktree) that starts from the wrong base, one agent that leaves out its structured output and stops the whole job, a restart that builds finished work again, and no signal when the job needs you.

### Cost is one fixed choice

Some work needs each change checked before the next change builds on it. Other work is a long series of small, easy changes, where a review of each one is most of the bill and finds nothing. A process with one setting makes you skip the process for the cheap work, or pay for assurance that the work does not need.

### A guard is only as strong as its harness

"The reviewer must not write" and "the implementer must not push" are rules. In most setups a hook of the harness enforces them, so each rule is exactly as strong as that hook. In a [**headless**](glossary.md#headless) run, with no person at the terminal, the harness can run without its hooks.

Add it up, and the person who delegated the work is still its reviewer, its finisher and its watcher.

## The idea

Fix everything except the ability of the model.

Every agent gets the same [**team contract**](glossary.md#team-contract), whatever model runs it: a role, the context for that role, the tools that it may use, a definition of done, and an independent check of its work. The contract is the same in every harness and at every price. So when you change the model, the only thing left to change is the code. That is the thing you pick a model for.

The independent check is the core of the contract, and its rule is short: no claim that matters at a merge comes from the agent that wrote the code. Something other than that agent checks that the code builds and the tests pass. Something that never read the author's account checks that the change does what you asked for. And the merge stays yours.

None of this is mine alone. The kit stands on two pieces of work, and it was only possible because of them. It has three layers, and I credit each one where it belongs.

- *The map is Bassim Eledath's.* His [levels of agentic engineering](https://www.bassimeledath.com/blog/levels-of-agentic-engineering) are a [**ladder**](glossary.md#ladder) of eight levels, from tab completion to autonomous agent teams. The rule of the ladder that matters most here is about order: "Levels 3 through 5 are the building blocks for everything that follows." He gives the reason too: "If your context is noisy, your prompts are under- or misspecified, or your tools are poorly described, levels 6 through 8 just amplify the mess." So the kit makes levels 3 to 5 explicit first, and it automates only on top of them.
- *The input is Matt Pocock's.* In [mattpocock/skills](https://github.com/mattpocock/skills), each planning [**skill**](glossary.md#skill) does one part of the thinking before an agent sees the work. [**`grilling`**](glossary.md#grilling) asks you questions, one round at a time, until each decision is made. [**`wayfinder`**](glossary.md#wayfinder) charts work that is too large for one session as a map of decisions. [**`to-spec`**](glossary.md#to-spec) writes the [**spec**](glossary.md#spec), and [**`to-tickets`**](glossary.md#to-tickets) cuts the spec into slices, each one a [**ticket**](glossary.md#ticket) that names the tickets that block it. The decisions are settled and the work is cut to size before an agent starts.
- *The execution is delegate.* It takes each ticket, hands it to an agent under the team contract, checks the result with evidence that the agent did not produce, and leaves it ready for you to merge.

## The ladder

Eledath describes the levels horizontally. A team climbs one level at a time, and each level is a set of practices that the team adopts across all of its work. delegate cuts the other way. It is one vertical slice through levels 2 to 7: a [**tracer bullet**](glossary.md#tracer-bullet) in Pocock's sense, a narrow path through every layer that works from end to end. You see the whole path work before you widen any part of it.

![The eight levels of agentic engineering as rows, and delegate as one vertical line through levels 2 to 7. Level 1 is outside the slice and level 8 is not attempted. The table below gives the same information.](assets/ladder-light.svg#gh-light-mode-only)
![The eight levels of agentic engineering as rows, and delegate as one vertical line through levels 2 to 7. Level 1 is outside the slice and level 8 is not attempted. The table below gives the same information.](assets/ladder-dark.svg#gh-dark-mode-only)

The same picture as a table:

| Level | Eledath's name | What delegate puts there |
| --- | --- | --- |
| 1 | Tab Complete | Nothing. The slice starts at level 2. |
| 2 | Agent IDE | The harness that you already use, driven headless through an [**adapter**](glossary.md#adapter). |
| 3 | Context Engineering | A [**brief**](glossary.md#brief) for each agent: the ticket, the [**file boundary**](glossary.md#file-boundary), and each [**gate**](glossary.md#gate) to run. Nothing else. |
| 4 | Compounding Engineering | The [**delegation document**](glossary.md#delegation-document), where you write the rules down once. Each agent reads it live, through its [**context skill**](glossary.md#context-skill). |
| 5 | MCP and Skills | An [**agent pair**](glossary.md#agent-pair) for each [**stack**](glossary.md#stack), rendered from one [**declaration**](glossary.md#declaration) with every skill of the stack. |
| 6 | Harness Engineering & Automated Feedback Loops | The [**engine**](glossary.md#engine) runs the gates itself and stops a diff that touches a [**hotspot**](glossary.md#hotspot). A [**push guard**](glossary.md#push-guard) in git holds in every harness. |
| 7 | Background Agents | Headless agents in worktrees, a [**blind**](glossary.md#blind) [**verifier**](glossary.md#verifier), and a [**journal**](glossary.md#journal) that a stopped [**run**](glossary.md#run) resumes from. |
| 8 | Autonomous Agent Teams | Not attempted. The agents never talk to each other: the engine dispatches, and the merge stays yours. |

Read the rows from the top, and Eledath's rule shows. The engine at levels 6 and 7 automates only what levels 3 to 5 make explicit: the brief, the written rules, and the skills of each stack. Take those away and the engine just runs the mess faster.

## The mental model

### Who is in the loop

The [**coordinator**](glossary.md#coordinator) is you: the person who directs the work, from a terminal or from an interactive session of a harness. The [**specialist**](glossary.md#specialist) is an agent that builds one ticket. The verifier is a second agent that checks the work of the specialist. It holds every skill of the specialist, the instructions for that kind of code, so it judges the work by the same standard. But it can only read.

A specialist and its verifier are an agent pair. A repository has one agent pair for each stack, each technology that it holds, such as a Python package or a web front end.

The engine is `delegate run`: a program, not a model. It does the work that I do not trust a model to remember.

### The loop: Brief, Build, Verify, Merge

Four verbs name the loop, and every ticket goes through all four.

- *Brief.* You write a ticket, and list it in a [**workflow**](glossary.md#workflow) file. The engine turns the ticket into a brief, the prompt for one agent.
- *Build.* The specialist builds the ticket in its own worktree and commits. The engine then runs the gates itself.
- *Verify.* A blind verifier checks the work. Blind means it gets the ticket and the diff, and never the specialist's own account of the work.
- *Merge.* Accepted work lands on the [**run branch**](glossary.md#run-branch), a branch that the engine owns. You merge the run branch.

### One ticket, from Brief to Merge

Follow one ticket through the loop: "The export command writes a header row."

1. *Brief.* You put the ticket in a workflow, with the stack `python`, the gate `python3 -m unittest`, and the hotspot `workflow.toml`, a path that only you change. You run `delegate run workflow.toml`, which starts a run. The engine creates the run branch `run/demo` from your [**base branch**](glossary.md#base-branch), `main`. It makes a worktree with its own branch for the ticket, and sets a push guard in it, so no push leaves that worktree. Then it writes the brief: the ticket, the file boundary (the worktree, and the hotspots that the specialist must not touch), the gate, and the path where the specialist writes its [**report**](glossary.md#report), its own account of the work.
2. *Build.* The specialist reads the brief, writes the header row and a test, commits, and writes its report: "committed, gates green". The engine does not take that word. It checks that the diff touches no hotspot, and then it runs `python3 -m unittest` itself. If the gate were red, or the agent ran out of turns, the engine would send the specialist back to the same worktree for a [**continuation**](glossary.md#continuation), and its commits would stay. Here the gate is green.
3. *Verify.* The engine rebases the branch onto the run branch and runs the gate again. It makes a [**temporary copy**](glossary.md#temporary-copy) of the branch, and gives the verifier a brief with two things in it: the ticket and the diff. Not the report. The verifier runs the gate in the copy. It breaks the code under the new test and watches the test go red, which is a [**red proof**](glossary.md#red-proof) that the test can fail. Then it writes its [**verdict**](glossary.md#verdict). This time the verdict is [**REJECT**](glossary.md#reject), with one [**finding**](glossary.md#finding): "The header row is missing when the data set is empty."
4. *Build again.* A REJECT starts a [**fix-up**](glossary.md#fix-up). The engine sends the finding, word for word, to the specialist in the same worktree. The specialist adds a new commit on top of the rejected one. It never amends. The engine runs the gate again.
5. *Verify again.* A fresh verifier gets the finding and the [**delta**](glossary.md#delta), the diff since the rejected commit, and checks only that. Its verdict is [**ACCEPT**](glossary.md#accept). Had it rejected again, a second round would follow, and at the limit the ticket fails and the run branch stays where it was.
6. *Merge.* On ACCEPT, the engine moves `run/demo` forward to the commit that the verifier saw. Then it stops. It never merges into `main`, never pushes, and never closes the ticket. You read the result with `delegate status`, and you merge with `git merge --ff-only run/demo`.

The same ticket, on one page:

```text
BRIEF    you: ticket in a workflow ──► engine: run branch, worktree, push guard, brief
            │
BUILD    specialist: code + test, commit, report ("gates green")
            │
         engine: hotspot check, then runs the gates itself
            │ green                            red ──► continuation, same worktree
            │
VERIFY   engine: rebase, gates again, temporary copy
            │
         blind verifier: ticket + diff, never the report
            │
            ├── REJECT ──► fix-up: new commit, gates, fresh verifier on the delta
            │                 │
            │◄──── ACCEPT ────┘          (at the limit: the ticket fails)
            │
MERGE    engine: run branch moves forward
         you:    git merge --ff-only run/demo
```

Look at where the evidence comes from. The gate result comes from the engine, not from the agent that wrote the code. The verdict comes from an agent that never saw the report. The merge comes from you. No arrow that carries evidence starts at the specialist.

### Two dials: ability and assurance

You tune two things, and neither one touches the team contract.

The first dial is ability. A workflow names a [**tier**](glossary.md#tier), a strength, and never a model: `strong`, `standard`, `cheap` or `verifier`. The [**tier table**](glossary.md#tier-table) maps each tier to a model for each harness, and your own [**tier file**](glossary.md#tier-file) can point the tiers at your gateway. A new model, or a new vendor, changes one file and no workflow. Turn this dial, and the code gets better or worse. The brief, the gates and the check stay the same.

The second dial is assurance. The [**mode**](glossary.md#mode) trades cost against how soon you learn that something is wrong. [**`assure`**](glossary.md#assure) is the mode of the example: it verifies each ticket at once, before the next ticket builds on it. [**`economy`**](glossary.md#economy) builds a [**chain**](glossary.md#chain), where each ticket starts from the one before, and verifies once for each stack at the end. It costs the fewest tokens, and a defect shows late.

One dial sets how good the code is. The other sets how soon you find out that it is not.

Neither dial changes an [**invariant**](glossary.md#invariant), one of the seven rules that hold in every mode. The verifier is blind. The engine runs the gates. Nothing reaches the run branch without an ACCEPT. And a cheaper mode never means a weaker verifier, because a cheap check is a false saving.

### What the engine keeps

The engine records the run in the journal: one line for each event, appended and never rewritten. Gate results, verdicts, fix-ups and each move of the run branch are all in it.

I made it this way for three reasons, and each one answers a problem from the top of this page. A run that stops, after Ctrl-C, a kill or a pause overnight, can [**resume**](glossary.md#resume) from the journal at the first ticket that is not complete, and it never builds a finished ticket again. `delegate status` and `delegate watch` read the journal, so you see where a run is, and `watch` exits when the run needs you. And the journal is the record of the evidence: what ran, in which order, with which result.

## What delegate is not

- *Not a model router.* It does not read a ticket and choose a model for it. A workflow names tiers, and the tier table maps each tier to a model. The choice stays with you, in one file.
- *Not an agent swarm.* The agents never talk to each other, and they never claim work from each other. That is level 8 of the ladder, and the kit does not attempt it. The engine dispatches, and you coordinate.
- *Not a way to make a weak model strong.* The contract and the checks are the same for every model, so a weak model fails where you can see it: red gates, REJECT verdicts, and fix-up rounds in the journal. The kit makes a weak model visibly weak. That tells you which tier to change.
- *Not a coding agent, and not a harness.* It writes no code. It drives the harness that you already use, through its command line, and the agents in that harness write the code.
- *Not an issue tracker.* It reads each ticket, one unit of work written as behaviour, from a local file or from inline text. It never contacts a tracker and never closes a ticket. You save the tickets to disk first.
- *Not a CI system.* It runs each gate of your repository (a command that proves a change is good, such as the test suite) on your machine, to decide whether the work of one ticket can land. It never pushes, and it does not replace the checks that run after you push.
- *Not a sandbox.* An agent runs as you, with your permissions, in a worktree. If a policy needs a container, this kit is not enough by itself.

## The parts of the kit

- The `agent-delegation` skill holds the [**protocol**](glossary.md#protocol): the roles, the brief template, blind verification, fix-ups and hotspots, in words that an agent reads. You can follow it by hand for a single ticket.
- The `agent-definitions` skill holds the rules of the [**renderer**](glossary.md#renderer) and the [**validator**](glossary.md#validator). One declaration renders an agent pair for each harness, and the validator checks the files against the schema of each harness.
- The command [**bootstrap**](glossary.md#bootstrap) writes the declaration, the agent pair, and a context skill that reads your delegation document, the file with the gates, the hotspots and the rules of your repository.
- The [**brief generator**](glossary.md#brief-generator) builds every verifier brief from the ticket, the diff and the gates, and never from the report.
- The engine drives each harness through an adapter. Claude Code and OpenCode have one today.
- The tier table maps each tier to a model for each harness, so a workflow never names a model.

## Where to go next

- [The tutorial](tutorial.md) runs the loop once, from install to one verified ticket. Start there.
- The how-to pages each solve one job: [bootstrap a single-stack repository](how-to/bootstrap-a-single-stack-repository.md), [bootstrap a monorepo](how-to/bootstrap-a-monorepo.md), [run an economy chain](how-to/run-an-economy-chain.md), [watch and resume a run](how-to/watch-and-resume-a-run.md), [point the tiers at a gateway](how-to/point-the-tiers-at-a-gateway.md), [add a harness adapter](how-to/add-a-harness-adapter.md), and [use the kit after `to-spec` and `to-tickets`](how-to/use-the-kit-after-to-spec-and-to-tickets.md).
- The reference gives every field and every flag: [the workflow file](reference/workflow.md), [`delegate run`](reference/run.md), [`delegate status` and `delegate watch`](reference/status-and-watch.md), and [`render`, `validate`, `bootstrap` and `brief`](reference/commands.md).
- The explanation pages give the reasons: [why the model should not matter](explanation/why-the-model-should-not-matter.md), [why the verifier is blind](explanation/why-the-verifier-is-blind.md), [why each specialist has a verifier twin](explanation/why-each-specialist-has-a-twin.md), [the mode trade-off](explanation/the-mode-trade-off.md), [the enforcement model and its limits](explanation/the-enforcement-model-and-its-limits.md), [prior art](explanation/prior-art.md), and [the decision records](explanation/decision-records.md).
- The [glossary](glossary.md) holds every term, with the page that explains it in full.
