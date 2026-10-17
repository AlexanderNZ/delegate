# Changelog

This file lists the changes of each release of `delegate`. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The version follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A host can give every agent a Claude Code settings file and a permission mode, with no change to a workflow file. The `claude-code` adapter adds `--settings <path>` when `DELEGATE_AGENT_SETTINGS` is set, and `--permission-mode <mode>` when `DELEGATE_PERMISSION_MODE` is set, for each specialist and each verifier. A settings path that is relative or not a file stops the adapter before an agent starts. The settings file must not turn off hooks, because the verifier guard is a hook. With neither variable, the command does not change. For OpenCode, a host sets `OPENCODE_CONFIG`, which OpenCode reads itself; the `opencode` adapter writes a warning when either variable is set.
- A tool that a permission mode refused (an error tool result with the refusal text) is in the new `refused` field of the result events (`adapter-result`, `fixup-result`, `verify-result`), with the tool and the mode. `delegate watch` shows it as a problem that names them. The harness port has the new result field `refused`; an adapter outside the kit that does not set it still works.
- A host can move the state directory of every repository out of the git directory with `DELEGATE_STATE_ROOT`, an absolute path. Each repository gets `<root>/<name>-<key>/`, where the key comes from its git common directory, so the worktrees, the reports, the journals and the locks are on paths with no `.git` component. Claude Code in `dontAsk` mode refuses `Edit` and `Write` on such a path, also with an allow rule. `run`, `status`, `watch` and `--resume` all find the directory. With no value, the state stays in `<git common dir>/delegate/`.
- The package has a `test` extra: `pip install '.[test]'` installs `pytest` and `pytest-xdist`, so the suite can run in parallel with `python -m pytest -n auto`. The development shell of the flake also holds `pytest-xdist`.

### Changed

- In `economy` mode, the engine runs no gate for one ticket. A valid report with the status `committed`, on a branch with a commit of the ticket, builds the step. At the end of the chain, for each stack, the engine runs the gates once on the chain tip. Red gates start a fix-up round with the output of the red gates, and the gates then run again. Green gates go on to the verifier of the chain. Its brief holds the gate commands, the commit and the output of the green gates, and it tells the verifier not to run the gates again. A REJECT starts a fix-up round, then the gates, then a scoped verifier. The rounds of red gates and of a REJECT count together against the fix-up limit of `economy` mode, which goes from 1 to 2. Before, the engine ran the gates after each ticket, and the verifier of the chain ran them again. `assure` mode does not change.
- The gate events on the tip of an `economy` chain have the phase `chain`. The `fixup-start` event of a chain also has `stack`, `tickets`, and `trigger` (`gates-red` or `reject`).
- In `economy` mode, `delegate status` shows the phase `gates` for the run while the gates of the chain run. The percentage and the ETA count the gates as part of the verification of the chain.

## [1.2.0] - 2026-10-17

### Added

- `delegate status` shows the progress of a run: under the run line, the percentage, the tickets that ended, the time used, the ETA, and the tokens. Under the line of each ticket with a step, it shows the phase, the time used, the tokens, and the time since the last activity of its agent. Each form of `status` also works for a run whose adapter the process does not know, or whose adapter has no stream reader: it then shows no tokens and no activity, and the JSON gives `null` for them.
- The token count of a Claude Code stream uses the `usage` of each `result` event. For a session that has no `result` event yet, it adds the usage of each turn, one for each message id. The reference page of `status` documents the rule.
- `delegate status --json` prints one snapshot of a run as JSON. It gives the state, the time used, the tickets that ended, the percentage, the ETA, and the tokens of the run. For each ticket it gives the state, the phase, the time used, the tokens, and the seconds since the last activity of its agent. The reference page of `status` documents each field, and the rules of the percentage and the ETA.
- `delegate watch --raw` prints the raw line of each event also when standard output is a terminal.
- The journal event `agent-start` names the event stream of each agent before the agent starts: the specialist, each continuation, the fix-up specialist, and each verifier.
- The journal event `gate-start` names the gate command and a log file before each gate runs. The gate runner writes the output of the gate to that file while the gate runs. `delegate watch --stall-minutes` counts a change of a gate log as a change, so a long gate that writes output is not a stall. `delegate status` shows the gate that runs and the time since its log changed, in each form. The JSON has the new ticket fields `gate` and `gate_idle_seconds`.
- `delegate run --resume <run-id> --retry <ticket>` goes on with one failed step of `assure` mode, also in a run that ended. It spawns no specialist. It uses the worktree and the commits of the step again, and goes on at the first phase that did not complete: the gates, the verifier, or the scoped verifier. The journal records `retry`. A built step, an unknown ticket, and a step that needs a new specialist make the command exit 1 with a message.

- The workflow field `ticket-start` sets the branch that each ticket branch of `assure` mode starts from. With `run-branch`, a ticket branch starts from the tip of the run branch, so a run can hold a sequential chain of tickets that change the same files, and the engine still verifies each ticket at once. The default, `base-branch`, keeps the earlier behaviour. `run-branch` in `economy` mode is an error.

### Changed

- `delegate watch` on a terminal shows a live view. At the top, the live block shows the snapshot of `delegate status`: one line for the run, and one line for each ticket, drawn again in place at each poll. Below it, a feed shows one short line for each event that matters: the local time, the ticket, and what happened, with no path and no `None`. A problem and a stall are lines that start with `✗`. When standard output is not a terminal, or with `--raw`, `watch` prints the raw lines as before. The exit codes, the options, and the last line `position <seq>` do not change. `delegate status` prints the state once and exits: the live view is in `watch`, and `status` has no `--follow`.
- In `economy` mode, `delegate status` counts the verification of the chain as one more unit of the run, in the percentage and in the ETA. The percentage stays below 100 until `run-end`. While the verification runs, each form of `status` shows its phase, `verifier` or `fix-up`, and the JSON has the new run field `phase`. Before, `status` showed 100% and an ETA of 0 seconds while the verifier of the chain ran.
- The gate port changed, so a gate runner outside the kit must change too. `GateRunner.run` has the new argument `log`: the path of a log file that the engine makes for each gate run. The runner appends the output of the command to that file while the command runs.
- A verifier run that ended with no verdict, for example one that the time limit of a harness killed, does not count against the fix-up limit. A resume runs that round again with the same round number. Before, the resume started the next round, and the step lost one of its fix-up rounds.
- The harness port changed, so an adapter outside the kit must change too. `AdapterRequest` has the new field `event_stream`: the engine chooses the file of the stream, and the adapter writes the stream there. The helper `stream_path` of `delegate.adapters.streams` is gone. An adapter has the new method `activity(stream)`, which returns an `Activity` with the tokens so far and the time of the newest event. `read_events` raises `AdapterError` for a stream file that is missing.
- The stream of a verifier is in `runs/<run id>/verifier/<ticket id>/` from the start, not in the temporary copy. `delegate watch --stall-minutes` now sees the stream of an agent that runs, because the journal names it at the start.
- A valid report and a success result continue the step, whatever the exit status. Before, a session that a coordinator stopped with SIGTERM (exit status 143) after its valid report failed the step. The end state of an adapter result now comes from the stream only, and the engine weighs the exit status. `delegate watch` does not show a `finished` result with a bad exit status as a problem: the report event or the `step-end` decides.
- A specialist report that is not valid, on a branch with a commit, does not fail the step at once. The engine sends a continuation in the same session that asks only for a corrected report, and names each problem of the check. The journal records the trigger `report-invalid`, and the continuation counts against the continuation limit of the mode. A missing report, and a report that is not valid on a branch with no commit, still fail the step.
- After a valid report, the engine ends an agent session that writes no event for 900 seconds, and the step continues. The journal records `idle-timeout`. The harness port has the new request fields `idle_timeout` and `report_valid`, and the new result field `ended_idle`. The helper `run_harness` of `delegate.adapters.process` runs a harness process and ends an idle session. An adapter outside the kit that ignores the new fields still works, but the engine cannot end its idle sessions.

## [1.1.0] - 2026-10-10

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
