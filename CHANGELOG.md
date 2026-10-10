# Changelog

This file lists the changes of each release of `delegate`. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The version follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The tier table moved from `src/delegate/tiers.toml` to `src/delegate/shared/tiers.toml`. A consumer that reads the file by its path, such as a Nix configuration, must use the new path. The renderer, the validator, the bootstrap and the agent templates moved into `src/delegate/definitions/`, and the reference generator and the neutrality check moved into `src/delegate/docs/`. The commands and their output do not change.
- The command modules moved into `src/delegate/cli/`, and the argument parsing of every command is there. The console scripts `delegate`, `agent-definitions` and `verifier-brief` do not change. A module that you start with `python -m` has a new name: `delegate.cli.delegate`, `delegate.cli.agent_definitions` and `delegate.cli.brief`. A git failure in a guard names the directory where git ran, as it did before the restructure.
- The engine runs a gate command through a new port, `delegate.ports.gates`, and the shell backend `delegate.adapters.shell` implements it. `run_workflow` takes the gate runner as a new argument after the version-control backend. The run context imports no `subprocess`, and the two contexts, `run` and `definitions`, import each other in neither direction. The names `CONTINUATION_LIMIT`, `FIXUP_ROUND_LIMIT`, `SPECIALIST_TIER`, `VERIFIER_TIER` and `MODES` are only in `delegate.run.domain`: the re-exports in `delegate.run.engine` and `delegate.run.workflow` are gone. The gates run as before, with `bash -c` in the worktree of the ticket.

## [1.0.0] - 2026-10-09

### Added

- A docs site, built from `docs/` with Zensical (pinned in `.zensical-version`). `delegate docs` also writes `includes/abbreviations.md` from the glossary, for the term tooltips of the site.
- A theme for the docs site: serif body text, numbered sections and a numbered table of contents, and a man-page header with NAME and SYNOPSIS on the reference pages. The spec and the prototype are in `design/`.
- The overview and the README open with the problem (models differ the way people do), credit the work that the kit builds on (Bassim Eledath's ladder and Matt Pocock's planning skills), and draw the kit as one slice through the ladder.
- An explanation page, "Why the model should not matter": the lineage of the kit, the thesis as a table, five lessons, and a recommendation (not a rule) for which model verifies.
- The prior art page has a "Built on" part (the ladder of Bassim Eledath and the planning skills of Matt Pocock, with what each gave) before an "Alongside" part. The page "Why each specialist has a verifier twin" is now "Why each specialist has its own verifier", and its old address redirects.
- A "Future ideas" page: ideas to investigate, not promises, each with the idea, why it matters, and the open questions.
- Every use of a glossary term on the site shows its definition on hover and links its glossary entry. `delegate docs` writes the lowercase and plural forms of each term, and `docs/javascripts/glossary.js`, a map from each form to its entry. On every page, the first use of each term is in bold and links the glossary, and a test holds this.
- A reference page, "The layout of the repository": the place of each part, the groups of modules in the package, and the dependency rule.
- A code of conduct (Contributor Covenant 2.1) and a security policy. Both take reports through the private reporting form of the repository.
- CONTRIBUTING says what the Nix flake gives, which issue template to use for what, and where to report conduct and security problems.

### Changed

- The package, its tests and `pyproject.toml` are now in the repository root, with the code in `src/`. The install command is `uv tool install git+https://github.com/AlexanderNZ/delegate`, with no `#subdirectory=` part. The three commands behave as before.
- The ADRs are now in `docs/adr/`, and not inside the `agent-definitions` skill. A test fails on a link to an ADR that does not exist.
- From 1.0 the project follows semantic versioning. The public interface is the workflow file, the commands with their flags and exit codes, and the format of an agent declaration.
- The README covers the repository side: a short description, a link to the docs site, the status of the project, the install, the layout, development, and contributing. The docs site holds the rest.

## [0.1.0] - 2026-10-09

The first release.

### Added

- Two agent skills as plain Markdown directories: `agent-delegation` (the protocol of a coordinator, a specialist and a verifier) and `agent-definitions` (the renderer and the validator).
- `delegate render` writes a specialist and its verifier twin for Claude Code and OpenCode from one declaration. A tier table gives the model for each harness.
- `delegate validate` checks the rendered agent files against the schema of each harness. Each finding has a code.
- `delegate bootstrap` writes the context skill, the declaration and the agent pair for a repository. It supports a monorepo with one pair for each stack.
- `delegate brief full` and `delegate brief fixup` build the brief of a blind verifier.
- `delegate run` is the workflow engine. It makes the worktrees, drives a harness through a headless adapter, runs the gates, spawns the verifiers, and records each step in a journal. It has two modes: `assure` verifies each branch at once, and `economy` verifies at the end of a chain.
- Adapters for `claude-code` and `opencode`, each with recorded streams and a documented live smoke run. An interface lets a contributor add an adapter.
- `delegate status` and `delegate watch` show the state of a run and follow its journal. A stopped run resumes.
- `delegate docs` writes the generated sections of the reference pages from the code. A test fails when a committed page differs from the code.
- Guards: a pre-push hook in each worktree, a check of the diff against the hotspot paths, and a check that a verifier leaves the real worktree unchanged.
- A neutrality check that fails when a term of a private denylist appears in the tree.
- Documentation: a tutorial, seven how-to pages, six explanation pages with the decision records, and the reference pages.
- `CONTRIBUTING.md`, with the setup with `pip` and `pytest`, the test-first rule, the regeneration command, and the checklist for a new adapter.
