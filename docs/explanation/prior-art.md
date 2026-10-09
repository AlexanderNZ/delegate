# Prior art

I did not build this kit in a vacuum. This page has two parts. *Built on* names the two bodies of work that the kit stands on, and says what each one gave. *Alongside* compares four projects that answer a nearby question, and says when I would choose the other tool. Each entry links its primary source and gives the date I read it.

## Built on

Two bodies of work come first, because the kit takes their output as its input. It would not exist in this form without them. The story of how the kit grew from them is in [why the model should not matter](why-the-model-should-not-matter.md).

### Bassim Eledath: the levels of agentic engineering

[The 8 Levels of Agentic Engineering](https://www.bassimeledath.com/blog/levels-of-agentic-engineering) is an article by Bassim Eledath. Read 2026-10-09.

It sets out eight levels, from tab completion to autonomous agent teams, and I call them the [**ladder**](../glossary.md#ladder). It gave the kit four things.

- *The map.* With the ladder I could place each part of the kit on a level, and see which levels the kit leaves alone. The kit is one slice through levels 2 to 7. It does not attempt level 8. See [the ladder](../overview.md#the-ladder).
- *The order of the climb.* "Levels 3 through 5 are the building blocks for everything that follows." So the kit makes the [**brief**](../glossary.md#brief), the written rules and the [**skills**](../glossary.md#skill) explicit first, and it automates only on top of them.
- *The codify loop.* At level 4 he describes "a plan, delegate, assess, codify loop", and he asks for the last step: "And then, crucially, you codify what you learned: what worked, what broke, what pattern to follow next time." In the kit, that is the [**delegation document**](../glossary.md#delegation-document). You write the rules down once, and every agent reads them live.
- *Backpressure.* "If you want autonomy, you need backpressure." His word means automated feedback, such as tests, linters and pre-commit hooks, that lets an agent find its own mistakes. In the kit, the [**gates**](../glossary.md#gate) that the [**engine**](../glossary.md#engine) runs itself are backpressure, and so is the [**pre-push hook**](../glossary.md#pre-push-hook) in each [**worktree**](../glossary.md#worktree).

He describes the levels horizontally, as practices that a team adopts across all of its work. The kit cuts the other way. He did not write about this kit, and any error in how I apply his ladder is mine.

### Matt Pocock: the planning skills

[mattpocock/skills](https://github.com/mattpocock/skills) is a set of skills for coding agents by Matt Pocock. Read 2026-10-09.

Four of its skills plan the work before an agent builds it. The kit starts where they stop. Each one gave the kit a part of its input.

- [**`grilling`**](../glossary.md#grilling) settles the decisions. It asks a round of questions at a time, until no decision is open, so no agent has to guess.
- [**`wayfinder`**](../glossary.md#wayfinder) maps work that is bigger than one [**session**](../glossary.md#session). It charts the work as a map of decisions, each sized to one agent session, and resolves them one at a time.
- [**`to-spec`**](../glossary.md#to-spec) writes the [**spec**](../glossary.md#spec) from what the conversation has already settled.
- [**`to-tickets`**](../glossary.md#to-tickets) cuts the spec into vertical slices, [**tracer bullets**](../glossary.md#tracer-bullet). Each slice names the [**tickets**](../glossary.md#ticket) that block it, and each one is sized to fit one fresh context window.

The last one matters most for the kit. A ticket that fits one context window is a ticket that one [**specialist**](../glossary.md#specialist) can build in one session, and that one [**verifier**](../glossary.md#verifier) can read as one diff. The [**blockers**](../glossary.md#blocker) set the order of a [**chain**](../glossary.md#chain).

I say it plainly: the kit takes the output of these skills as its input. I wrote no planning step, because the planning was already done, and done well. To use the two together, see [how to use the kit after `to-spec` and `to-tickets`](../how-to/use-the-kit-after-to-spec-and-to-tickets.md). The same author has a build skill, `implement-spec`, and it is an entry below.

## Alongside

Four projects sit beside the kit. I read each one, and I took something from some of them. None of these tools is wrong. They answer a different question. This kit answers one question: what independent evidence do I have at each merge?

### superpowers

[superpowers](https://github.com/obra/superpowers) is a skills framework and a development methodology for coding agents. Read 2026-10-09.

Its `subagent-driven-development` skill is the closest match to the [**protocol**](../glossary.md#protocol) of this kit. A fresh implementer subagent builds each task. A task reviewer checks spec compliance and code quality after each task, and a broad review runs at the end. A task gets up to five fix rounds. The skill also covers brainstorming, planning and test-driven development, which this kit does not.

The difference is where the rules live. In superpowers, a model session follows a skill, so the session carries the process. In this kit, the rules that matter most are in an engine outside the session: it runs the gates, builds the verifier brief, and moves the [**run branch**](../glossary.md#run-branch). I chose this because a model that coordinates can forget a rule or be talked out of it.

Choose superpowers when you want a full methodology inside one session, from the first idea to the last review. Choose this kit when the work is a chain of tickets and you want the gates and the [**blind**](../glossary.md#blind) verifier enforced by code.

### mattpocock/skills `implement-spec`

[`implement-spec`](https://github.com/mattpocock/skills/blob/main/skills/engineering/implement-spec/SKILL.md) is a skill in [mattpocock/skills](https://github.com/mattpocock/skills), version 1.3.1. Read 2026-10-09.

It builds a whole spec on one integration branch. It treats the tickets as a task graph and runs implementer subagents at the same time across the ready tickets, each in its own worktree. A merger subagent lands each result. When all tickets are done, it runs `code-review` once on the integration branch and fixes the issues in a single subagent.

That is a good design for throughput. One session runs the most work in parallel, and the human reviewer is the trust point. This kit does not compete with it. It takes the output of the planning skills of that project, `to-spec` and `to-tickets`, as its input, and it replaces only the build step. I give the reasons in [the mode trade-off](the-mode-trade-off.md) and in [why the verifier is blind](why-the-verifier-is-blind.md).

Choose `implement-spec` when you want the most parallel work and you will read the result yourself. Choose this kit when you want independent evidence at each merge, and a stop rule for the fix loop.

### Sandcastle

[Sandcastle](https://github.com/mattpocock/sandcastle) is a TypeScript library that runs coding agents in isolated sandboxes. Read 2026-10-09.

It runs agents in containers or microVMs, with providers for Docker, Podman and Vercel. It has several agent types, structured output, and session resume. I took one thing from it: the shape of the [**headless**](../glossary.md#headless) command for each [**harness**](../glossary.md#harness). The source is `src/AgentProvider.ts`, and Sandcastle is MIT licensed. I measured each command on a real machine before I used it. See [the `claude-code` adapter reference](../reference/claude-code-adapter.md).

The differences are runtime and isolation. Sandcastle needs Node and a sandbox provider. This kit needs Python, git and the harness, and it isolates work with git worktrees and guards, not with containers. A container is stronger isolation than a worktree. I put a container sandbox out of scope until a policy needs it.

Choose Sandcastle when you need container isolation, or when you want a library to build your own orchestration in TypeScript. Choose this kit when you want a finished protocol with a blind verifier and a [**journal**](../glossary.md#journal), and you cannot add Node and Docker.

### wshobson/agents

[wshobson/agents](https://github.com/wshobson/agents) is a plugin marketplace for several harnesses. Read 2026-10-09.

It is a large catalogue of agents, skills and commands. One Markdown source builds the files for each harness, and model aliases map to the models of each harness. That is the same idea as my one [**declaration**](../glossary.md#declaration) and my [**tier table**](../glossary.md#tier-table). The README that I read describes a catalogue and its installers. I found no engine in it that runs gates or a verifier.

So the two do different jobs. Its agents are building blocks, and this kit is a process that uses building blocks. You can use an agent from the catalogue as the specialist of a pair. Choose wshobson/agents when you need a broad set of ready-made agents. Choose this kit when you need the process around them.

### How to choose

If you want a methodology for one session, use superpowers. If you want throughput and you review the end result, use `implement-spec`. If you need containers, use Sandcastle. If you need a catalogue of agents, use wshobson/agents. If you want evidence at each merge, from gates that the agent did not run and a verifier that did not see the [**report**](../glossary.md#report), use this kit. Several of these combine well. The kit stands on the ladder of Eledath and on the planning skills of mattpocock/skills, as the first part of this page says, and I read the others for what they teach.
