# ADR 0007: The engine runs each agent from a sealed agent, outside the tree under review

- Status: accepted
- Date: 2026-11-01
- Applies to: the harness sessions of `delegate run`, and the harness adapters

## Context

A rendered pair is committed in the repository, in `.claude/agents/` and `.opencode/agents/`. The engine used to start each session with `--agent <name>` and a tree as the working directory. The harness then found the agent file in the tree. The tree is the work under review, and a specialist can change it.

Two measurements on 2026-10-31, in issue 187, showed a gap. Claude Code 2.1.287 and OpenCode 1.15.10, on macOS. A project agent replaces a user agent of the same name. On Claude Code, a session with `--print` does not run the hooks in the frontmatter of a project agent, also under a directory with `hasTrustDialogAccepted`. So the guard of a committed verifier did not run. The journals of this repository hold 6 verifier guard denials, all in run `20261016T032643Z-a910`. That run started before the committed pair (commit `54eed32`, 2026-10-16). After that commit, no verifier stream holds a denial.

The hooks are the second line of the [enforcement model](../explanation/the-enforcement-model-and-its-limits.md). The first line is git and the file system, and it was not affected. The gap lost the second line: a verifier could write outside the worktree under review, and run a command that its guard denies.

## The options

On 2026-11-01, issue 213 measured the options on Claude Code 2.1.287 with the model `haiku`. Each clone held a committed project agent with a hook in its frontmatter. Each guard under test was a `PreToolUse` hook on `Bash` that logs its name and exits 2. The base command was the command of the adapter, `claude --print --output-format stream-json --verbose --model haiku --strict-mcp-config --disallowedTools Monitor,TaskStop --allowedTools 'Bash(pwd)' <options> --agent <name>`. "Poisoned" means that the clone also held a committed `.claude/settings.json` with `{"disableAllHooks": true}`, a file that a specialist can commit.

1. **The project agent of the tree (the base).** No option. The project agent loaded, and no hook ran.
2. **`--agents <json>` with `hooks`.** The `--agents` agent loaded, and the guard denied `pwd`. In the poisoned clone, no hook ran.
3. **`--settings <file>` with `hooks`.** The project agent loaded, and the guard denied `pwd`. In the poisoned clone, no hook ran. With `--setting-sources user --settings <file>` and `--agents <file without hooks>`, the `--agents` agent loaded and the guard denied. With the same options and the agent from the tree, the harness wrote `--agent 'm187-probe' not found`. Alone, this option keeps the prompt of the project agent from the tree, so the tree controls the instructions of the verifier.
4. **`--setting-sources user --agents <file>` with the hooks in the agent.** The agent and its guard held in the poisoned clone. The context skill needs another path, for example its text in the prompt. `--agents` takes a file path under `--print`, so the definition does not go in argv.
5. **`--setting-sources user --plugin-dir <dir>` with a plugin.** The agent loaded as `<plugin>:<agent>`. A plugin agent ignores `hooks` in its frontmatter, so no hook ran. The hooks in `hooks/hooks.json` of the plugin ran, also in the poisoned clone. The plugin carries the agent, its skills (listed as `<plugin>:<skill>`) and the guard. Its hooks apply to the whole session, so each role needs its own plugin.

Two facts hold for each option. `--setting-sources user` is necessary: without it, a committed `disableAllHooks` turns off each guard, from `--agents` and from `--settings` too. And `--setting-sources user` removes the project agents and the project skills of the tree, so the agent and its skills must come from outside the tree.

## Decision

At the start of a run, the engine seals each agent that a role names. It copies the agent from the main checkout into `<run directory>/agents/<agent>/`, outside each tree. This copy is the sealed agent. Each session of the run starts only from the seal. Before each session, the engine hashes each file of the seal, and a difference fails the step.

On Claude Code, the seal is a plugin, option 5. It is the only measured option that brings the guard and the skills of the agent with it. The command is `claude --print ... --setting-sources user --plugin-dir <seal directory> --agent <plugin>:<agent>`. On OpenCode, the seal is an OpenCode configuration with the agent as an entry of `agent`, with `mode: all`. The session gets it in `OPENCODE_CONFIG_CONTENT`.

A guard probe checks that the guard works. Before the first agent session, the engine starts one session for each verifier, from its seal, on the tier `cheap`. The session must run `touch delegate-guard-probe`, and the guard must deny it. A result other than `denied` ends the run. A guard that does not run writes no event, so only a probe can show it. The `claude-code` adapter has the probe. The `opencode` adapter has none, because of the limit below.

The `claude-code` adapter refuses a version before 2.1.287, the version of the measurements.

### What is dropped on purpose

`--setting-sources user` also stops the session from loading the project settings and the project `CLAUDE.md`. The engine drops both on purpose. A committed settings file is the file that turns off a guard, so the session cannot read it. The seal holds the instructions of the repository in place of the project `CLAUDE.md`: the text of `CLAUDE.md` and `.claude/CLAUDE.md` of the main checkout, with their imports. A host that needs settings gives them in `DELEGATE_AGENT_SETTINGS`.

## Rejected alternatives

- **Options 1, 2 and 3 alone.** The tree controls the agent, or the hooks do not run, or a committed settings file turns them off.
- **Option 4.** It is the second choice. It has fewer parts, but the context skill needs its own path, and the plugin carries the skills in the seal.
- **A trust dialog.** The measurement shows that trust does not change the result with `--print`.

## Consequences

- A change of the agent file in the main checkout or in a tree after the seal does not reach a session. `delegate run --resume <run-id> --retry <ticket> --agents-changed` seals again.
- The seal is a copy, and the specialist runs as the person who started the run. The specialist can write the seal, as it can write the state directory. The agent check detects a change before the next session. It does not prevent it.
- The project settings of a repository do not apply to an engine session.
- A committed pair stays in the repository for the Agent tool of a coordinator. A `delegate run` session does not use it.
- On OpenCode, the resolution of the sealed agent was measured, and the enforcement of its permission map in a session was not measured. Measured on OpenCode 1.15.10 with `opencode debug agent`: the prompt of the sealed agent replaces the prompt of a project agent of the same name, and its permission rules come after the rules of the tree. No model answered in 90 seconds, so no session showed that the harness refuses a tool call that the map denies. This is decision D-e of issue 213. Measure it again when a model answers, and add the date and the version here.
- The rule depends on the version of the harness. Measure it again when a new version changes how it reads a plugin, a setting or a hook.
- The preload of the `skills` of an agent did not work under `--agent` in any case that issue 213 measured, so the load-time blocks of a context skill do not run in an engine session. Issue 215 follows this.
