# delegate

Models are different, the same way people are different. I wanted a team of models that works like a good team of people: one way of working, with the members differing only in skill. Instead, two sessions of the same model planned, used the tools and wrote code in different ways, and one short remark in the conversation changed the code. I managed each model in prose, and the review loop played football: reject, fix, reject, pass, re-check everything. Work got done very quickly. The quality was inconsistent, and the token bill was massive.

`delegate` is the machine I built to fix that. It fixes everything except the ability of the model. Every agent gets the same team contract, whatever model runs it: a role, the context for that role, the tools it may use, a definition of done, and an independent check. The model I pick then changes one thing: how good the code is.

The independent check doesn't trust the agent that wrote the code. The engine runs the gates itself. A blind verifier gets the ticket and the diff, never the specialist's report. Fix-up rounds have a cap, and a fix-up check reads only the new commits. Nothing reaches the run branch without an ACCEPT.

The kit has three parts:

- A delegation protocol, shipped as agent skills: a coordinator, a specialist and a verifier, with a brief template, scoped fix-ups and hotspots that only the coordinator writes.
- A renderer. One declaration renders a specialist and its verifier for each harness, and a validator checks the files against the schema of each harness.
- A workflow engine. `delegate run` drives a harness through its headless command line, makes the worktrees, runs the gates, spawns the verifiers, and records every step in a journal.

The skills and the engine name no person, company or tracker. Each repository keeps its own gates, hotspots, models and writing style.

## Position

This kit builds on two pieces of work. It was only possible because of them: without them I wouldn't have met these problems, and I wouldn't have solved them this way.

- *The map:* [the levels of agentic engineering](https://www.bassimeledath.com/blog/levels-of-agentic-engineering) by Bassim Eledath. He describes eight levels, from tab completion to autonomous agent teams, and the rule that levels 3 to 5 must hold before more automation. This kit is one vertical slice through levels 2 to 7. It does not attempt level 8. The [overview](docs/overview.md) draws the slice.
- *The input:* the planning skills of [mattpocock/skills](https://github.com/mattpocock/skills) by Matt Pocock. `grilling` and `wayfinder` settle the decisions, `to-spec` writes the spec, and `to-tickets` cuts it into tickets with blocking edges. This kit takes those tickets as its input, and it replaces only the build step.

The same project has a build skill, `implement-spec`. It optimises developer flow and throughput: one session, maximum parallelism, and a human reviewer as the trust point. This kit sits beside it on the same foundation, and optimises independent evidence at each merge.

## Install

You need Python 3.11 or later, git, and a harness that the engine drives. Claude Code and OpenCode have adapters today. Nix is optional.

```bash
uv tool install git+https://github.com/AlexanderNZ/delegate
```

This puts three commands on your PATH: `delegate`, `agent-definitions` and `verifier-brief`. `delegate` is the umbrella command. Its subcommands `render`, `validate`, `bootstrap`, `brief full` and `brief fixup` take the same arguments as the standalone commands and give the same result.

## Quick start

In your repository:

1. Write your gates and hotspots in `docs/agents/delegation.md`.
2. Run `delegate bootstrap` to write the context skill, the declaration and the agent pair.
3. Write a workflow file with your tickets, and check it with `delegate run <workflow> --dry-run`.
4. Run `delegate run <workflow>`, and read the result with `delegate status` and `delegate watch`.

The [tutorial](docs/tutorial.md) does all of this once, on a small sample repository.

## Skills

The two skills are plain Markdown directories: `skills/agent-delegation/` and `skills/agent-definitions/`. Copy or link them into a directory that your harness reads skills from.

## Docs

Start with the first two pages.

- [Start here: the kit at a glance](docs/overview.md): the problem (models differ the way people do), the idea (one team contract for every model), the ladder that the kit slices through, the team and its loop from a ticket to a merge (Brief, Build, Verify, Merge) with one ticket followed through it, the two dials (tier and mode), and what the kit is not.
- [Glossary](docs/glossary.md): every term of these docs, in alphabetical order, with what it is, why it matters, and the page with the detail.
- [Tutorial: from install to one verified ticket](docs/tutorial.md): bootstrap a sample repository, run one ticket in `assure` mode on Claude Code, and read the verdict and the journal. Its sample files are in [`examples/tutorial`](examples/tutorial).
- [How to bootstrap a single-stack repository](docs/how-to/bootstrap-a-single-stack-repository.md): write the delegation document and a skill, run `delegate bootstrap`, check the pair, and name it in a workflow.
- [How to bootstrap a monorepo](docs/how-to/bootstrap-a-monorepo.md): one gate block, one skill and one agent pair for each stack, and a workflow with a stack for each pair.
- [How to run an economy chain](docs/how-to/run-an-economy-chain.md): write a chain of tickets, run it, read the result, and merge the run branch.
- [How to watch and resume a run](docs/how-to/watch-and-resume-a-run.md): follow a run with `status` and `watch`, go on from a position, and resume a run after Ctrl-C or a kill.
- [How to point the tiers at a gateway](docs/how-to/point-the-tiers-at-a-gateway.md): write your own tier file, give it with `--tiers`, and see the engine ask the harness for your models.
- [How to add a harness adapter](docs/how-to/add-a-harness-adapter.md): the parts of the adapter interface, an example adapter, and the contract-test pattern with recorded streams.
- [How to use the kit after `to-spec` and `to-tickets`](docs/how-to/use-the-kit-after-to-spec-and-to-tickets.md): save the tickets to files, write a workflow that names them, and run the plan.
- [Why the model should not matter](docs/explanation/why-the-model-should-not-matter.md): how the kit got its shape, from one agent in a terminal, to subagents and a model router, to rules for each model, to contracts for each role, to the engine; what varies between models and how the kit fixes it; five lessons; and which model to verify with, as a recommendation and not a rule.
- [Why the verifier is blind, and why it runs the gates](docs/explanation/why-the-verifier-is-blind.md): the report is a claim, so the verifier gets the task and the diff; the engine runs the gates, and the verifier runs them in a copy that it may break.
- [Why each specialist has its own verifier](docs/explanation/why-each-specialist-has-its-own-verifier.md): a verifier needs the skills of the specialist, so one declaration renders both halves of a pair, with a pair for each stack.
- [The mode trade-off](docs/explanation/the-mode-trade-off.md): why there are two modes, what `assure` and `economy` each give up, and the rules that no mode changes.
- [Prior art](docs/explanation/prior-art.md): what the kit is built on (the levels of agentic engineering of Bassim Eledath, and the planning skills of mattpocock/skills), and four projects beside it: superpowers, mattpocock/skills `implement-spec`, Sandcastle and wshobson/agents. What each does, how this kit differs, and when to choose the other tool.
- [The enforcement model and its limits](docs/explanation/the-enforcement-model-and-its-limits.md): what the engine enforces with git, what it does not stop, and every limit of the guards, so that you trust a guard no further than it goes.
- [The decision records](docs/explanation/decision-records.md): an index of every ADR of the kit, with the reason in one line for each.
- [Reference: the workflow file](docs/reference/workflow.md): every workflow field, and the `delegate run <workflow> --dry-run` check.
- [Reference: `delegate run`](docs/reference/run.md): the build of a ticket through a harness adapter, the adapter interface, the specialist report, the journal, the resume of a stopped run, and the run lock.
- [Reference: the `claude-code` adapter](docs/reference/claude-code-adapter.md): the command, how the adapter selects the agent, the end states, the recorded streams, and the live smoke run.
- [Reference: the `opencode` adapter](docs/reference/opencode-adapter.md): the command, how the adapter selects the agent, the working directory, the end states, the recorded streams, and the live smoke run.
- [Reference: `delegate status` and `delegate watch`](docs/reference/status-and-watch.md): the state of each ticket of a run, the follow of its journal, the problem events, and the exit code of each reason.
- [Reference: `render`, `validate`, `bootstrap` and `brief`](docs/reference/commands.md): the options of each command, the commands that the verifier guard permits, and the finding codes of the validator.
- [Reference: `delegate docs`](docs/reference/docs.md): the command that writes the generated sections of these pages from the code. After a change to a flag, an exit code, a guard command or a finding code, run `delegate docs`; a test fails when a committed page differs from the code.
- [Reference: the layout of the repository](docs/reference/layout.md): the place of each part of the repository, the groups of modules in the package, and the dependency rule that a test holds.
- [Future ideas](docs/future-ideas.md): ideas to investigate, not promises and not a plan: a benchmark from real tickets, more of the coordinator role in the engine, the next step up the ladder, and a Cursor adapter.
- [The agent rules of this repository](docs/agents/delegation.md): the gates and the hotspots that bind every agent that works on the kit itself.

## Contributing

See [CONTRIBUTING](CONTRIBUTING.md) for the setup with `pip` and `pytest`, the test-first rule, the regeneration command, and the checklist for a new harness adapter. Each release is listed in the [changelog](CHANGELOG.md).

## Generated files

The files that the tool generates (rendered agents, context skills, delegation docs) belong to you. They need no copyright notice or licence notice from this project.

## Licence

MIT. See [LICENSE](LICENSE).
