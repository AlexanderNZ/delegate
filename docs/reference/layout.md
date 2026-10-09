---
man: DELEGATE-LAYOUT(7)
man_name: "delegate-layout — the layout of the repository and the dependency rule of the package"
---

# The layout of the repository

The repository holds three things: two skills that an agent reads, one Python package that holds every command, and the docs. This page gives the place of each part at a high level, and the rule for the direction of imports in the package. It describes the repository as it is today.

## The top level

| Path | What it holds |
| --- | --- |
| `skills/agent-delegation/` | The [**protocol**](../glossary.md#protocol) skill: the roles, the [**brief**](../glossary.md#brief) template, blind verification, [**fix-ups**](../glossary.md#fix-up) and [**hotspots**](../glossary.md#hotspot), in words that an agent reads. |
| `skills/agent-definitions/` | The definitions skill: the rules of the [**renderer**](../glossary.md#renderer) and the [**validator**](../glossary.md#validator), and the neutrality [**allowlist**](../glossary.md#allowlist). |
| `src/delegate/` | The Python package. It holds the commands `delegate`, `agent-definitions` and `verifier-brief`. |
| `tests/` | The pytest suite of the package and of the docs, with the recorded [**harness**](../glossary.md#harness) streams in `tests/fixtures/`. |
| `docs/` | The source of the docs site. `docs/adr/` holds the decision records, and `docs/agents/` holds the agent rules of this repository. The site excludes both. |
| `examples/` | A sample [**declaration**](../glossary.md#declaration), and the sample repository of the tutorial. |
| `scripts/` | The release script. |
| `pyproject.toml`, `package.nix`, `flake.nix` | The package build for `pip` and for Nix. Nix is optional. |
| `zensical.toml`, `overrides/`, `includes/` | The configuration, the theme and the generated abbreviations of the docs site. |
| `design/` | A snapshot of the design of the docs theme. |

## The package

The modules of `src/delegate/` fall into six groups. Each group does one job.

| Group | Modules | Job |
| --- | --- | --- |
| Definitions | `declaration.py`, `render.py`, `validate.py`, `bootstrap.py`, `templates/` | Load a declaration, render an [**agent pair**](../glossary.md#agent-pair) for each harness, validate the rendered files, and [**bootstrap**](../glossary.md#bootstrap) a repository. |
| Briefs | `brief.py` | Build a [**verifier**](../glossary.md#verifier) brief from the [**ticket**](../glossary.md#ticket), the diff and the [**gates**](../glossary.md#gate), and never from the [**report**](../glossary.md#report). |
| Run | `run.py`, `workflow.py`, `engine.py`, `guards.py`, `journal.py`, `lock.py`, `reports.py`, `runs.py`, `tiers.py`, `tiers.toml`, `status.py`, `watch.py` | Check a [**workflow**](../glossary.md#workflow), build its tickets, hold the guards, write the [**journal**](../glossary.md#journal), and report the state of a [**run**](../glossary.md#run). |
| [**Adapters**](../glossary.md#adapter) | `adapters/__init__.py`, `adapters/streams.py`, `adapters/claude_code.py`, `adapters/opencode.py` | Implement the harness port. Each one drives one harness through its [**headless**](../glossary.md#headless) command line. The package also holds the registry of adapters by name. |
| Ports | `ports/harness.py` | The interface that the [**engine**](../glossary.md#engine) drives to run one agent: the request, the result, the [**end states**](../glossary.md#end-state), and the error. |
| Commands and checks | `delegate.py`, `cli.py`, `reference.py`, `neutrality.py` | The command-line entry points, the generator of the reference sections, and the [**neutrality check**](../glossary.md#neutrality-check). |

## The dependency rule

Dependencies point inward. Run and Definitions are the two bounded contexts, and they hold the domain. The ports are the interfaces that the domain drives. The adapters implement the ports, and the command line drives the domain.

```mermaid
flowchart LR
  cli[Command line] --> run[Run]
  cli --> definitions[Definitions]
  adapters[Adapters] --> ports[Ports]
  run --> ports
  adapters --> run
```

Five rules follow, and `tests/test_architecture.py` holds them:

- `run` never imports `adapters` or `cli`.
- `definitions` never imports `adapters` or `cli`.
- `ports` never imports `adapters` or `cli`.
- `adapters` never imports `cli`.
- `engine.py` and `workflow.py` never import `adapters`. They are flat modules of the Run context until a later step moves them. The engine knows the harness port only.

A layer is a subpackage of `src/delegate/`. Today `definitions/`, `ports/` and `adapters/` exist, and `definitions/` is empty. The other modules are flat. A flat module joins its layer when it moves into the subpackage, and from then on the test checks it. The test names the two flat modules of the Run context that it checks already, `engine.py` and `workflow.py`. The test reads the imports with the `ast` module of the standard library, so it runs no code of the package.

The composition point is the command-line driver. `delegate run` reads the name of the adapter from the workflow, looks it up in `adapters`, and passes the adapter to the engine. The engine never chooses an adapter.

## Where to change what

- A new harness: an adapter module in `adapters/`, beside `claude_code.py`, a [**contract test**](../glossary.md#contract-test) in `tests/`, and recorded streams in `tests/fixtures/`. See [how to add a harness adapter](../how-to/add-a-harness-adapter.md).
- A new flag, exit code, guard command or [**finding code**](../glossary.md#finding-code): change the code, then run `delegate docs`. See [`delegate docs`](docs.md).
- A new decision: an [**ADR**](../glossary.md#adr) in `docs/adr/`, and a line in [the decision records](../explanation/decision-records.md).
- The gates and the hotspots of this repository: `docs/agents/delegation.md`.
