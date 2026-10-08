# delegate

`delegate` is a kit for delegating implementation work to coding agents, with independent evidence at each merge.

I built it because the usual pattern trusts the agent that wrote the code. One session runs many implementers, a reviewer reads one large diff at the end, and the reviewer takes the implementer's word that the tests pass. This kit does not take that word. The engine runs your gates itself. A blind verifier gets the ticket and the diff, never the implementer's report. Nothing reaches the run branch without an ACCEPT.

The kit has three parts:

- A delegation protocol, shipped as agent skills: a coordinator, a specialist and a verifier, with a brief template, scoped fix-ups and hotspots that only the coordinator writes.
- A renderer. One declaration renders a specialist and its verifier twin for each harness, and a validator checks the files against the schema of each harness.
- A workflow engine. `delegate run` drives a harness through its headless command line, makes the worktrees, runs the gates, spawns the verifiers, and records every step in a journal.

The kit names no person, company or tracker. Each repository keeps its own gates, hotspots, models and writing style.

## Position

This kit does not compete with [mattpocock/skills](https://github.com/mattpocock/skills). That project optimises developer flow and throughput: one session, maximum parallelism, and a human reviewer as the trust point. This kit optimises independent evidence at each merge. It takes the output of that project's planning skills (a spec, and tickets with blocking edges) as its input, and it replaces only the build step.

## Install

You need Python 3.11 or later, git, and a harness that the engine drives. Claude Code and OpenCode have adapters today. Nix is optional.

```bash
uv tool install "git+https://github.com/AlexanderNZ/delegate#subdirectory=skills/agent-definitions/validator"
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

- [Tutorial: from install to one verified ticket](docs/tutorial.md): bootstrap a sample repository, run one ticket in `assure` mode on Claude Code, and read the verdict and the journal. Its sample files are in [`examples/tutorial`](examples/tutorial).
- [How to bootstrap a single-stack repository](docs/how-to/bootstrap-a-single-stack-repository.md): write the delegation document and a skill, run `delegate bootstrap`, check the pair, and name it in a workflow.
- [How to bootstrap a monorepo](docs/how-to/bootstrap-a-monorepo.md): one gate block, one skill and one agent pair for each stack, and a workflow with a stack for each pair.
- [How to run an economy chain](docs/how-to/run-an-economy-chain.md): write a chain of tickets, run it, read the result, and merge the run branch.
- [Reference: the workflow file](docs/reference/workflow.md): every workflow field, and the `delegate run <workflow> --dry-run` check.
- [Reference: `delegate run`](docs/reference/run.md): the build of a ticket through a harness adapter, the adapter interface, the specialist report, the journal, the resume of a stopped run, and the run lock.
- [Reference: the `claude-code` adapter](docs/reference/claude-code-adapter.md): the command, how the adapter selects the agent, the end states, the recorded streams, and the live smoke run.
- [Reference: the `opencode` adapter](docs/reference/opencode-adapter.md): the command, how the adapter selects the agent, the working directory, the end states, the recorded streams, and the live smoke run.
- [Reference: `delegate status` and `delegate watch`](docs/reference/status-and-watch.md): the state of each ticket of a run, the follow of its journal, the problem events, and the exit code of each reason.
- [Reference: `render`, `validate`, `bootstrap` and `brief`](docs/reference/commands.md): the options of each command, the commands that the verifier guard permits, and the finding codes of the validator.
- [Reference: `delegate docs`](docs/reference/docs.md): the command that writes the generated sections of these pages from the code. After a change to a flag, an exit code, a guard command or a finding code, run `delegate docs`; a test fails when a committed page differs from the code.
- [The agent rules of this repository](docs/agents/delegation.md): the gates and the hotspots that bind every agent that works on the kit itself.

## Generated files

The files that the tool generates (rendered agents, context skills, delegation docs) belong to you. They need no copyright notice or licence notice from this project.

## Licence

MIT. See [LICENSE](LICENSE).
