# delegate

`delegate` is a kit for delegating implementation work to coding agents, with independent evidence at each merge. It ships a delegation protocol as agent skills, and a tool that renders a specialist and a blind verifier twin for each harness from one declaration, validates the agent files, and writes each verifier's brief. The kit names no person, company, or tracker; each repository keeps its own gates and hotspots.

## Install

```bash
uv tool install "git+https://github.com/AlexanderNZ/delegate#subdirectory=skills/agent-definitions/validator"
```

This puts three commands on your PATH: `delegate`, `agent-definitions` and `verifier-brief`. `delegate` is the umbrella command. Its subcommands `render`, `validate`, `bootstrap`, `brief full` and `brief fixup` take the same arguments as the standalone commands and give the same result. `agent-definitions` is the standalone renderer and validator, and `verifier-brief` is the standalone brief generator. The kit needs Python 3.11 or later and git. Nix is optional.

The two skills are plain Markdown directories: `skills/agent-delegation/` and `skills/agent-definitions/`. Copy or link them into a directory that your harness reads skills from.

## Docs

- [Reference: the workflow file](docs/reference/workflow.md): every workflow field, and the `delegate run <workflow> --dry-run` check.
- [Reference: `delegate run`](docs/reference/run.md): the build of a ticket through a harness adapter, the adapter interface, the specialist report, and the journal.

## Generated files

The files that the tool generates (rendered agents, context skills, delegation docs) belong to you. They need no copyright notice or licence notice from this project.

## Licence

MIT. See [LICENSE](LICENSE).
