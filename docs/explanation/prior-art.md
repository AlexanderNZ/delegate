# Prior art

I did not build this kit in a vacuum. Four projects shaped it, and I took something from each. This page says what each one does, how this kit differs, and when I would choose the other tool. Each entry links its primary source and gives the date I read it.

None of these tools is wrong. They answer a different question. This kit answers one question: what independent evidence do I have at each merge?

## superpowers

[superpowers](https://github.com/obra/superpowers) is a skills framework and a development methodology for coding agents. Read 2026-10-09.

Its `subagent-driven-development` skill is the closest match to the protocol of this kit. A fresh implementer subagent builds each task. A task reviewer checks spec compliance and code quality after each task, and a broad review runs at the end. A task gets up to five fix rounds. The skill also covers brainstorming, planning and test-driven development, which this kit does not.

The difference is where the rules live. In superpowers, a model session follows a skill, so the session carries the process. In this kit, the rules that matter most are in an engine outside the session: it runs the gates, builds the verifier brief, and moves the run branch. I chose this because a model that coordinates can forget a rule or be talked out of it.

Choose superpowers when you want a full methodology inside one session, from the first idea to the last review. Choose this kit when the work is a chain of tickets and you want the gates and the blind verifier enforced by code.

## mattpocock/skills `implement-spec`

[`implement-spec`](https://github.com/mattpocock/skills/blob/main/skills/engineering/implement-spec/SKILL.md) is a skill in [mattpocock/skills](https://github.com/mattpocock/skills), version 1.3.1. Read 2026-10-09.

It builds a whole spec on one integration branch. It treats the tickets as a task graph and runs implementer subagents at the same time across the ready tickets, each in its own worktree. A merger subagent lands each result. When all tickets are done, it runs `code-review` once on the integration branch and fixes the issues in a single subagent.

That is a good design for throughput. One session runs the most work in parallel, and the human reviewer is the trust point. This kit does not compete with it. It takes the output of the planning skills of that project, `to-spec` and `to-tickets`, as its input, and it replaces only the build step. I give the reasons in [the mode trade-off](the-mode-trade-off.md) and in [why the verifier is blind](why-the-verifier-is-blind.md). To use the two together, see [how to use the kit after `to-spec` and `to-tickets`](../how-to/use-the-kit-after-to-spec-and-to-tickets.md).

Choose `implement-spec` when you want the most parallel work and you will read the result yourself. Choose this kit when you want independent evidence at each merge, and a stop rule for the fix loop.

## Sandcastle

[Sandcastle](https://github.com/mattpocock/sandcastle) is a TypeScript library that runs coding agents in isolated sandboxes. Read 2026-10-09.

It runs agents in containers or microVMs, with providers for Docker, Podman and Vercel. It has several agent types, structured output, and session resume. I took one thing from it: the shape of the headless command for each harness. The source is `src/AgentProvider.ts`, and Sandcastle is MIT licensed. I measured each command on a real machine before I used it. See [the `claude-code` adapter reference](../reference/claude-code-adapter.md).

The differences are runtime and isolation. Sandcastle needs Node and a sandbox provider. This kit needs Python, git and the harness, and it isolates work with git worktrees and guards, not with containers. A container is stronger isolation than a worktree. I put a container sandbox out of scope until a policy needs it.

Choose Sandcastle when you need container isolation, or when you want a library to build your own orchestration in TypeScript. Choose this kit when you want a finished protocol with a blind verifier and a journal, and you cannot add Node and Docker.

## wshobson/agents

[wshobson/agents](https://github.com/wshobson/agents) is a plugin marketplace for several harnesses. Read 2026-10-09.

It is a large catalogue of agents, skills and commands. One Markdown source builds the files for each harness, and model aliases map to the models of each harness. That is the same idea as my one declaration and my tier table. The README that I read describes a catalogue and its installers. I found no engine in it that runs gates or a verifier.

So the two do different jobs. Its agents are building blocks, and this kit is a process that uses building blocks. You can use an agent from the catalogue as the specialist of a pair. Choose wshobson/agents when you need a broad set of ready-made agents. Choose this kit when you need the process around them.

## How to choose

If you want a methodology for one session, use superpowers. If you want throughput and you review the end result, use `implement-spec`. If you need containers, use Sandcastle. If you need a catalogue of agents, use wshobson/agents. If you want evidence at each merge, from gates that the agent did not run and a verifier that did not see the report, use this kit. Several of these combine well. The kit takes the output of the planning skills of mattpocock/skills, and I read the others for what they teach.
