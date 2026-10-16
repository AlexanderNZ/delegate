---
man: DELEGATE-CLAUDE-CODE(7)
man_name: "claude-code — the adapter that drives Claude Code in headless mode"
synopsis: 'adapter = "claude-code"'
---

# Reference: the `claude-code` adapter

The `claude-code` [**adapter**](../glossary.md#adapter) drives Claude Code in [**headless**](../glossary.md#headless) [**mode**](../glossary.md#mode). A [**workflow**](../glossary.md#workflow) selects it with `adapter = "claude-code"`. It follows the adapter interface in [the run reference](run.md#the-adapter-interface).

## The command

For each agent run, the adapter starts this command in the working directory of the request:

```
claude --print --output-format stream-json --verbose --model <model> --agent <agent> --disallowedTools Monitor,TaskStop [--resume <session id>]
```

The environment of the command is the environment of the [**engine**](../glossary.md#engine), with `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` added.

| Part | Meaning |
|---|---|
| `--print` | Run without a terminal. The [**harness**](../glossary.md#harness) writes its output and exits. |
| `--output-format stream-json` | The harness writes one JSON event on each line of standard output. |
| `--verbose` | The harness needs it to write the events with `--print` and `--output-format stream-json`. |
| `--model <model>` | The model from the `claude-code` column of the [**tier table**](../glossary.md#tier-table). The adapter never chooses a model. A `--tiers` file changes it. |
| `--agent <agent>` | The agent name from the [**stack**](../glossary.md#stack): the [**`specialist`**](../glossary.md#specialist) field, or the [**`verifier`**](../glossary.md#verifier) field for the verifier. |
| `--disallowedTools Monitor,TaskStop` | Remove the monitor tool and the tool that stops a background task, for every agent. See [the foreground rule](#the-foreground-rule). |
| `--resume <session id>` | Only for a [**continuation**](../glossary.md#continuation). See [the resume](#the-resume). |
| `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` | An environment variable, not an argument. It removes the `run_in_background` parameter of the Bash tool and of the subagent tool. That parameter is not a tool name, so `--disallowedTools` cannot remove it. |

The prompt goes on standard input. It is never in the arguments of the command, so a long brief meets no limit on arguments and shows in no process list.

The adapter passes no permission option other than `--disallowedTools`. The permission rules of the repository and of the user decide which of the other tools the agent can use. See [what the adapter does not set](#what-the-adapter-does-not-set).

## The foreground rule

Each agent runs every command in the foreground. Under `--print`, the notification of a background task or of a monitor does not come to the agent. An agent that waits for one polls with sleep commands and uses its turns. A monitor that stays open keeps the [**session**](../glossary.md#session) alive after the agent writes its [**report**](../glossary.md#report), and the engine waits for the session to end.

The adapter therefore removes these tools for every agent, and the specialist brief and the verifier brief tell the agent to run every command in the foreground. Measured on Claude Code 2.1.287 on 2026-10-17: with `--disallowedTools Monitor,TaskStop`, the `init` event lists neither tool. With `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, the Bash tool has no `run_in_background` parameter.

## How the adapter selects the agent

The adapter selects the agent with `--agent <agent>`. The harness reads the agent file from the `.claude/agents/` directory of the working directory (or from the user agents), and the body of that file becomes the system prompt of the [**session**](../glossary.md#session). The adapter does not put the agent body in the prompt. The [**live smoke run**](../glossary.md#live-smoke-run) below shows it: the reply of the agent follows a rule that only the agent body holds.

When the harness does not know the agent, it exits with status 1 and writes no event. See [the start failure](#the-start-failure).

## The event stream

The adapter copies standard output to the file that the request names in `event_stream`. The [**engine**](../glossary.md#engine) chooses that file beside the [**report**](../glossary.md#report): `<report name>.stream.jsonl`. A later run with the same report path gets `<report name>.stream-2.jsonl`, then `-3`, and so on, so no [**run**](../glossary.md#run) overwrites an earlier stream. The adapter writes standard error to no file; it uses standard error only in [the start failure](#the-start-failure).

The adapter reads the last `result` event. The `session_id` field of that event is the session id of the result.

| `result` event | [**End state**](../glossary.md#end-state) |
|---|---|
| `subtype` is `error_max_turns` | `capped` |
| `is_error` is true, or `subtype` starts with `error` | `failed` |
| Other | `finished` |
| No `result` event | `failed` |

Two more rules apply:

- An exit status other than 0 turns `finished` into `failed`.
- A harness that is killed can leave half a line at the end of the stream. The adapter ignores that line. Any other line that is not JSON raises an `AdapterError` that names the stream and the line number.

An API error (for example, a model that does not exist) gives `is_error` true with `subtype` `success`. The `is_error` field decides, not `subtype`.

## The activity

Each form of `delegate status` reads each stream through the method `activity` of the adapter. The stream can be the partial stream of an agent that still runs. The method gives the tokens so far and the time of the newest event:

- The time is the `timestamp` field of the newest event that has one (ISO 8601, UTC). The `assistant` and `user` events have it. The `system` and `result` events do not.
- The token count of a `usage` object adds `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, and `output_tokens`.
- The harness writes one `result` event at the end of each session. Its `usage` holds the true count of the session, so the adapter uses it. A resumed session writes a second `result` event in the same stream: the adapter adds the count of each `result` event.
- The `message.usage` of an `assistant` event is partial: its `output_tokens` is not final. For the part of the stream after the last `result` event (a session that runs now), the adapter adds the turns: one turn for each message id. The harness writes one event for each content block of a message, and each of these events repeats the usage of the message, so the adapter keeps the newest usage of each message id.
- Half a line at the end of the stream is ignored, as in the end state. A stream with no `result` event and no `assistant` event gives 0 tokens.

For example, the recorded stream of a session that finished has one message with the partial usage 10 + 19176 + 0 + 6 = 19192, and a `result` event with the usage 10 + 19176 + 0 + 140 = 19326. The count of the stream is 19326. While a session runs, its count is therefore lower than its final count.

## The resume

The adapter has `supports_resume` set to true. For a continuation, the [**engine**](../glossary.md#engine) sets `resume_session` in the request, and the adapter adds `--resume <session id>`. The session keeps its first context and its agent. The result has the same session id.

## The start failure

When the harness exits and writes no event, no end state can describe the [**run**](../glossary.md#run). An unknown agent, an unknown option, and a missing login do this. The adapter raises `AdapterError` with the exit status, the agent name, and the standard error text of the harness. The engine records the error as a crash and ends the run, because the same cause stops every later agent. See [a crash in a step](run.md#a-crash-in-a-step).

## What the adapter does not set

A headless session cannot ask for a permission. A tool use that the rules do not allow is denied, and the result event lists it in `permission_denials`. Measured on Claude Code 2.1.287 on 2026-10-08: a `Write` to a file was denied in a headless run with no allow rule, and it was still denied with a project rule `Write` in `.claude/settings.json`. The adapter therefore does not decide the permission posture. The adopter sets the allow rules of the repository, or the [**coordinator**](../glossary.md#coordinator) changes the adapter. A run that needs a tool without a rule ends with the agent reporting the denial, and the [**step**](../glossary.md#step) fails on the report check.

The report path is in the [**state directory**](../glossary.md#state-directory) of the repository, outside the [**worktree**](../glossary.md#worktree). The agent must be able to write there.

## The recorded streams

The directory `tests/fixtures/claude-code/` holds one recorded stream for each end state and `manifest.json`. The manifest names the harness version, the date, the command, and what the redaction removed.

| Fixture | End state | How it was made | Exit status |
|---|---|---|---|
| `finished.jsonl` | `finished` | Model `haiku`, agent `smoke-agent`, a prompt for one sentence. | 0 |
| `capped.jsonl` | `capped` | Model `haiku`, `--max-turns 1`, a prompt that needs two tool calls. | 1 |
| `failed.jsonl` | `failed` | Model `no-such-model-xyz`, which the API refuses with a 404. | 1 |

Version: Claude Code 2.1.287. Recorded: 2026-10-08 (UTC).

The [**contract tests**](../glossary.md#contract-test) replay these streams through a stand-in `claude` command and check the result fields. A test also checks that each fixture names the version of the manifest.

## The live smoke run

| Item | Value |
|---|---|
| Harness | Claude Code 2.1.287 |
| Date | 2026-10-08 (UTC) |
| Model | `haiku` |
| Agent | `smoke-agent`: a project agent file in `.claude/agents/`. Its body says that a reply starts with the word `SMOKE-AGENT-ACTIVE`. |

1. The adapter ran with the prompt "Reply with the single word PINEAPPLE on the second line." The result was `finished`, exit status 0. The reply was `SMOKE-AGENT-ACTIVE` and then `PINEAPPLE`. The first word comes only from the agent body, so `--agent` selected the agent.
2. The adapter ran again with `resume_session` set to the session id of the first run, and the prompt "Which word did I ask for? Answer with that word only." The result was `finished`, exit status 0, with the same session id. The reply was `PINEAPPLE`, so the session kept its context.

Not measured: a run in which the agent writes files and commits. The permission posture is open. See [what the adapter does not set](#what-the-adapter-does-not-set). Not measured: the hooks in the frontmatter of a rendered agent under `--agent`.

The command shape comes from mattpocock/sandcastle `src/AgentProvider.ts` (MIT), with `--verbose` and `--agent` added after the measurement.
