# Changelog

This file lists the changes of each release of `delegate`. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The version follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A docs site, built from `docs/` with Zensical (pinned in `.zensical-version`). `delegate docs` also writes `includes/abbreviations.md` from the glossary, for the term tooltips of the site.
- A theme for the docs site: serif body text, numbered sections and a numbered table of contents, and a man-page header with NAME and SYNOPSIS on the reference pages. The spec and the prototype are in `design/`.
- The overview and the README open with the problem (models differ the way people do), credit the work that the kit builds on (Bassim Eledath's ladder and Matt Pocock's planning skills), and draw the kit as one slice through the ladder.
- An explanation page, "Why the model should not matter": the lineage of the kit, the thesis as a table, five lessons, and a recommendation (not a rule) for which model verifies.

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
