# delegate

[![CI](https://github.com/AlexanderNZ/delegate/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/AlexanderNZ/delegate/actions/workflows/ci.yml)
[![Docs](https://github.com/AlexanderNZ/delegate/actions/workflows/docs.yml/badge.svg?branch=main&event=push)](https://alexandernz.github.io/delegate/)

`delegate` is a kit for delegating implementation work to coding agents. Every agent gets the same team contract, whatever model runs it: a role, the context for that role, the tools it may use, a definition of done, and an independent check. The engine runs the gates itself, and a blind verifier checks each change before it reaches the run branch. So the choice of model changes one thing only: how good the code is.

It builds on [the levels of agentic engineering](https://www.bassimeledath.com/blog/levels-of-agentic-engineering) by Bassim Eledath and on the planning skills of [mattpocock/skills](https://github.com/mattpocock/skills) by Matt Pocock: `grilling`, `wayfinder`, `to-spec` and `to-tickets`. It would not exist without them. [Prior art](docs/explanation/prior-art.md) says what each one gave.

## Docs

The docs are at **[alexandernz.github.io/delegate](https://alexandernz.github.io/delegate/)**, built from [`docs/`](docs/overview.md). Start with [the overview](docs/overview.md): the problem, the idea, the team and the loop from a ticket to a merge. Then follow [the tutorial](docs/tutorial.md) from install to one verified ticket. Each term is in [the glossary](docs/glossary.md).

## Status

The project follows [semantic versioning](https://semver.org) from 1.0. Its public interface is the workflow file, the commands with their flags and exit codes, and the format of an agent declaration. A change that breaks one of them needs a new major version. [The changelog](CHANGELOG.md) records each change, and [the releases](https://github.com/AlexanderNZ/delegate/releases) hold each version. The project is young, and it has one maintainer. The engine drives Claude Code and OpenCode today. [Future ideas](docs/future-ideas.md) lists what is under consideration, with no promise of any of it.

## Install

The kit needs Python 3.11 or later and git. A run also needs a harness that the engine drives: [Claude Code](docs/reference/claude-code-adapter.md) or [OpenCode](docs/reference/opencode-adapter.md).

```bash
uv tool install git+https://github.com/AlexanderNZ/delegate
```

This puts [three commands](docs/reference/commands.md) on the PATH: `delegate`, `agent-definitions` and `verifier-brief`. The two skills are plain Markdown directories, `skills/agent-delegation/` and `skills/agent-definitions/`, to copy or link into a directory that the harness reads skills from.

## The repository

| Path | What it holds |
| --- | --- |
| `src/delegate/` | The Python package: every command, the engine, the renderer, the validator and the harness adapters. |
| `tests/` | The pytest suite of the package and of the docs. |
| `skills/` | The two skills: the delegation protocol and the agent-definition rules. |
| `docs/` | The docs site, the decision records in `docs/adr/`, and the agent rules of this repository in `docs/agents/`. |
| `flake.nix`, `package.nix` | The optional Nix flake. |

The package has one dependency rule: the domain never imports the adapters or the command line, and `tests/test_architecture.py` holds it. [The layout of the repository](docs/reference/layout.md) gives each part, the groups of modules, and the rule in full. [Why the code has this shape](docs/explanation/why-the-code-has-this-shape.md) gives the reasons for the two contexts, the ports and the rule.

## Development

The setup needs only Python and git:

```bash
pip install . pytest
python -m pytest -rs
```

These are the commands that CI runs. The suite also tests the docs: a dead link, a stale generated section, or a glossary term that is not linked at its first use fails it. After a change to a flag, an exit code, a guard command, a finding code or the glossary, run [`delegate docs`](docs/reference/docs.md) and commit what it writes.

To build the docs site, run `uvx "zensical==$(cat .zensical-version)" build --strict`. To preview it, run the same command with `serve` in place of `build`.

Nix is optional. The flake gives a development shell (`nix develop`, or `direnv allow` once), the package, a check that runs the suite inside the Nix build (`nix flake check`), and the paths of the two skills as `lib.skills` for a Nix consumer. Nothing in the kit needs Nix at run time.

[CONTRIBUTING](CONTRIBUTING.md) has the full setup, the test-first rule, the rules of the docs, the adapter checklist and the release steps.

## Contributing

Contributions are welcome. Read [CONTRIBUTING](CONTRIBUTING.md) first, and the [code of conduct](CODE_OF_CONDUCT.md). Open an issue with one of the templates: a **Bug** for behaviour that disagrees with the docs, a **Task** for work with a decided design, or a **Decision** for a question that needs the maintainer before anyone builds. A question is welcome as a blank issue.

To report a vulnerability, do not open an issue. See [the security policy](SECURITY.md).

## Generated files

The files that the tool generates (rendered agents, context skills, delegation docs) belong to you. They need no copyright notice or licence notice from this project.

## Licence

MIT. See [LICENSE](LICENSE).
