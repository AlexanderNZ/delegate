# ADR 0001: Effort follows the model

- Status: accepted
- Date: 2026-09-19
- Applies to: Claude Code agent files, and the `effort` key of a declaration

## Context

One declaration renders a specialist and a verifier. On Claude Code, the spawn call can set the model of a subagent. The spawn call cannot set the effort level.

The first design wrote `effort` into each Claude Code agent file, with `high` as the default. A domain that needs more than one effort level then needs one file for each level. With three levels, a pair has six files, not two.

Claude Code also has a setting for each model: `modelSettings.<model>.effortLevel` in `settings.json`. Before the build, it was not known which value a subagent uses: the setting for its own model, or the effort of the parent session. An experiment had to answer the question before the build.

## Decision

- A rendered Claude Code agent file has no `effort` key, unless the declaration sets one.
- The configuration that writes `settings.json` sets `modelSettings.<model>.effortLevel` for each model in the tier table. When the coordinator selects a model at spawn, it also selects the effort.
- `effort` stays a declaration key. Use it only when the two halves of a pair need different effort on the same model.
- Do not start the coordinator session with `--effort`, and do not set `CLAUDE_CODE_EFFORT_LEVEL`. Each one has priority over the value for the model, so a subagent then does not use that value.

This decision is about Claude Code. The OpenCode render of a declared `effort` is in the skill, § Rendering rules.

## Rejected alternatives

- **Effort in each agent file, with `high` as the default.** This was the first design. The experiment showed that a file with no `effort` takes the value for its own model, so the model at spawn selects the strength and the effort together. A value in the file has priority over the value for the model, so a default in each file stops that.
- **One agent file for each domain and each effort level.** This gives three times more files: six for each pair, not two. The experiment showed that the setting for each model makes these files unnecessary.
- **One effort for the full session, with `--effort` or `CLAUDE_CODE_EFFORT_LEVEL`.** Each one has priority over the value for the model, so each one defeats the value for the model.

## Consequences

- The model that the coordinator selects at spawn sets the strength and the effort together. A brief names the model. It does not name an effort.
- The configuration must hold a `modelSettings` entry for each model in the tier table. A model with no entry gives its subagents the session default, `effortLevel`. A consumer can make its configuration check fail on a missing entry.
- The experiment did not test these items: the levels `xhigh` and `max`, `maxEffortLevel` as a limit for subagents, the `effort` of a preloaded skill, and agent teams.

## Evidence

Measured on Claude Code 2.1.267, 2026-09-19. Nine cases set different combinations of the effort sources, and each case spawned a subagent. The `SubagentStop` hook payload gives the effort of the subagent as `effort: {level}`. The transcript of the subagent gives the same value on each message. All nine cases agreed.

The priority order, strongest first:

1. the `CLAUDE_CODE_EFFORT_LEVEL` environment variable
2. the `effort` field of the agent file
3. `--effort` when the session starts
4. `modelSettings.<model of the subagent>.effortLevel`
5. `effortLevel`

The decisive case: the session effort was `low`, the `modelSettings` entry for the Sonnet model was `high`, and the agent file had no `effort`. The subagent on Sonnet ran at `high`. In a different case, the entry for the Sonnet model did not apply to an Opus subagent.

The decision was made on 2026-09-19, after the experiment.
