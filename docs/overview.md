# Start here

Handing work to a coding agent is easy. Trusting what comes back is the hard part, and this kit exists for that part.

This page is the kit at a glance: the problem, the idea, and a picture of how one piece of work moves from start to merge. It is not a tutorial and it is not a reference. It links both at the end. Each term is in bold where it first appears, and it links its entry in the [glossary](glossary.md).

## The problem

You delegate implementation work to coding agents. Each agent runs in a [**harness**](glossary.md#harness): Claude Code, OpenCode, or the `agent` CLI of Cursor. In every harness you meet the same four problems, and each one costs you something.

### Trust arrives too late

The common pattern runs many implementer agents in one [**session**](glossary.md#session) of a harness and reviews the result once, at the end. The reviewer reads one large diff. It takes the implementer's word that the tests pass, and only its prompt keeps it from changing the code. "The tests pass" is a claim, not evidence. The agent may have run a different command, or run the tests before its last edit.

The review has a quieter flaw. A reviewer that reads the implementer's account first looks where the account points. If the account says "I fixed the empty-list case", the reviewer checks the empty-list case. The case that nobody thought of gets no attention at all.

Then the review finds a defect, and the fix loop has no stop rule. It can go round for hours. What comes back is a draft, and you finish it yourself.

### The scripts belong to one harness

An orchestration script written for one harness does not run in another harness. It names models, so a change of [**gateway**](glossary.md#gateway) or vendor means a change to every script. And the scripts fail in ways that the harness causes:

- a [**worktree**](glossary.md#worktree), the separate working tree that an agent builds in, starts from the wrong base, and the agent builds on the wrong commit;
- one agent leaves out its structured output, and the whole job stops, so one bad agent costs you hours of work;
- a restart after an interruption builds the finished work again, and you pay for it twice;
- nothing tells you when the job needs you, so you watch it by hand.

### Cost and speed are one fixed choice

Some work is a few changes where a defect in one spoils the next. You want to know about that defect before anything builds on it, and you will pay for that. Other work is a long series of small, easy changes. A review for each one is most of the bill, and most of those reviews find nothing. A process with one setting forces a bad choice. You skip the process for the cheap work, or you pay for assurance that the work does not need.

### A guard is only as strong as its harness

A reviewer that must not write, and an implementer that must not push, are rules. In most setups a hook or a permission of the harness enforces them, so each rule is exactly as strong as that hook. In a [**headless**](glossary.md#headless) session, with no person at the terminal, the harness can run without its hooks entirely.

Add it up, and the person who delegated the work is still its reviewer, its finisher and its watcher.

## The idea

`delegate` is a kit that hands implementation work to coding agents and gives you independent evidence at each merge. The rule under it is short: no claim that matters at a merge comes from the agent that wrote the code. Something other than that agent checks that the code builds and the tests pass. Something that never read the author's account checks that the change does what you asked for. The rule is the same whatever harness runs the agents, whatever models they use, and whatever you choose to spend. And the merge stays yours.

## What delegate is not

- *Not a coding agent, and not a harness.* It writes no code. It drives the harness that you already use, through its command line, and the agents in that harness write the code.
- *Not an issue tracker.* It reads each [**ticket**](glossary.md#ticket), one unit of work written as behaviour, from a local file or from inline text. It never contacts a tracker and never closes a ticket. You save the tickets to disk first.
- *Not a CI system.* It runs each [**gate**](glossary.md#gate) of your repository (a command that proves a change is good, such as the test suite) on your machine, to decide whether the work of one ticket can land. It never pushes, and it does not replace the checks that run after you push.
- *Not a sandbox.* An agent runs as you, with your permissions, in a worktree. If a policy needs a container, this kit is not enough by itself.

## The mental model

### Who is in the loop

The [**coordinator**](glossary.md#coordinator) is you: the person who directs the work, from a terminal or from an interactive session of a harness. The [**specialist**](glossary.md#specialist) is an agent that builds one ticket. The [**verifier**](glossary.md#verifier) is a second agent that checks the work of the specialist. It holds every [**skill**](glossary.md#skill) of the specialist, the instructions for that kind of code, so it judges the work by the same standard. But it can only read.

A specialist and its verifier are an [**agent pair**](glossary.md#agent-pair). A repository has one agent pair for each [**stack**](glossary.md#stack), each technology that it holds, such as a Python package or a web front end.

The [**engine**](glossary.md#engine) is `delegate run`: a program, not a model. It does the work that I do not trust a model to remember.

### The loop: Brief, Build, Verify, Merge

Four verbs name the loop, and every ticket goes through all four.

- *Brief.* You write a ticket, and list it in a [**workflow**](glossary.md#workflow) file. The engine turns the ticket into a [**brief**](glossary.md#brief), the prompt for one agent.
- *Build.* The specialist builds the ticket in its own worktree and commits. The engine then runs the gates itself.
- *Verify.* A [**blind**](glossary.md#blind) verifier checks the work. Blind means it gets the ticket and the diff, and never the specialist's own account of the work.
- *Merge.* Accepted work lands on the [**run branch**](glossary.md#run-branch), a branch that the engine owns. You merge the run branch.

### One ticket, from Brief to Merge

Follow one ticket through the loop: "The export command writes a header row."

1. *Brief.* You put the ticket in a workflow, with the stack `python`, the gate `python3 -m unittest`, and the [**hotspot**](glossary.md#hotspot) `workflow.toml`, a path that only you change. You run `delegate run workflow.toml`, which starts a [**run**](glossary.md#run). The engine creates the run branch `run/demo` from your [**base branch**](glossary.md#base-branch), `main`. It makes a worktree with its own branch for the ticket, and sets a [**push guard**](glossary.md#push-guard) in it, so no push leaves that worktree. Then it writes the brief: the ticket, the [**file boundary**](glossary.md#file-boundary) (the worktree, and the hotspots that the specialist must not touch), the gate, and the path where the specialist writes its [**report**](glossary.md#report), its own account of the work.
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

### Two things you tune

The first is the [**tier**](glossary.md#tier). A workflow names strengths, never models: `strong`, `standard`, `cheap` and `verifier`. The [**tier table**](glossary.md#tier-table) maps each tier to a model for each harness, and your own [**tier file**](glossary.md#tier-file) can point the tiers at your gateway. A new model, or a new vendor, changes one file and no workflow.

The second is the [**mode**](glossary.md#mode), which trades cost against how soon you learn that something is wrong. [**`assure`**](glossary.md#assure) is the mode of the example: it verifies each ticket at once, before the next ticket builds on it. [**`economy`**](glossary.md#economy) builds a [**chain**](glossary.md#chain), where each ticket starts from the one before, and verifies once for each stack at the end. It costs the fewest tokens, and a defect shows late.

Neither mode changes an [**invariant**](glossary.md#invariant), one of the seven rules that hold in every mode. The verifier is blind. The engine runs the gates. Nothing reaches the run branch without an ACCEPT. And a cheaper mode never means a weaker verifier, because a cheap check is a false saving.

### What the engine keeps

The engine records the run in the [**journal**](glossary.md#journal): one line for each event, appended and never rewritten. Gate results, verdicts, fix-ups and each move of the run branch are all in it.

I made it this way for three reasons, and each one answers a problem from the top of this page. A run that stops, after Ctrl-C, a kill or a pause overnight, can [**resume**](glossary.md#resume) from the journal at the first ticket that is not complete, and it never builds a finished ticket again. `delegate status` and `delegate watch` read the journal, so you see where a run is, and `watch` exits when the run needs you. And the journal is the record of the evidence: what ran, in which order, with which result.

## The parts of the kit

- The `agent-delegation` skill holds the [**protocol**](glossary.md#protocol): the roles, the brief template, blind verification, fix-ups and hotspots, in words that an agent reads. You can follow it by hand for a single ticket.
- The `agent-definitions` skill holds the rules of the [**renderer**](glossary.md#renderer) and the [**validator**](glossary.md#validator). One [**declaration**](glossary.md#declaration) renders an agent pair for each harness, and the validator checks the files against the schema of each harness.
- The command [**bootstrap**](glossary.md#bootstrap) writes the declaration, the agent pair, and a [**context skill**](glossary.md#context-skill) that reads your [**delegation document**](glossary.md#delegation-document), the file with the gates, the hotspots and the rules of your repository.
- The [**brief generator**](glossary.md#brief-generator) builds every verifier brief from the ticket, the diff and the gates, and never from the report.
- The engine drives each harness through an [**adapter**](glossary.md#adapter). Claude Code and OpenCode have one today.
- The tier table maps each tier to a model for each harness, so a workflow never names a model.

## Where to go next

- [The tutorial](tutorial.md) runs the loop once, from install to one verified ticket. Start there.
- The how-to pages each solve one job: [bootstrap a single-stack repository](how-to/bootstrap-a-single-stack-repository.md), [bootstrap a monorepo](how-to/bootstrap-a-monorepo.md), [run an economy chain](how-to/run-an-economy-chain.md), [watch and resume a run](how-to/watch-and-resume-a-run.md), [point the tiers at a gateway](how-to/point-the-tiers-at-a-gateway.md), [add a harness adapter](how-to/add-a-harness-adapter.md), and [use the kit after `to-spec` and `to-tickets`](how-to/use-the-kit-after-to-spec-and-to-tickets.md).
- The reference gives every field and every flag: [the workflow file](reference/workflow.md), [`delegate run`](reference/run.md), [`delegate status` and `delegate watch`](reference/status-and-watch.md), and [`render`, `validate`, `bootstrap` and `brief`](reference/commands.md).
- The explanation pages give the reasons: [why the verifier is blind](explanation/why-the-verifier-is-blind.md), [why each specialist has a verifier twin](explanation/why-each-specialist-has-a-twin.md), [the mode trade-off](explanation/the-mode-trade-off.md), [the enforcement model and its limits](explanation/the-enforcement-model-and-its-limits.md), [prior art](explanation/prior-art.md), and [the decision records](explanation/decision-records.md).
- The [glossary](glossary.md) holds every term, with the page that explains it in full.
