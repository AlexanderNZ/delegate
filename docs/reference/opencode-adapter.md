---
man: DELEGATE-OPENCODE(7)
man_name: "opencode — the adapter that drives OpenCode in headless mode"
synopsis: 'adapter = "opencode"'
---

# Reference: the `opencode` adapter

The `opencode` [**adapter**](../glossary.md#adapter) drives OpenCode in [**headless**](../glossary.md#headless) [**mode**](../glossary.md#mode). A [**workflow**](../glossary.md#workflow) selects it with `adapter = "opencode"`. It follows the adapter interface in [the run reference](run.md#the-adapter-interface).

## The command

For each agent run, the adapter starts this command in the working directory of the request:

```
opencode run --format json --model <model> --agent <agent> --dir <directory> [--session <session id>]
```

| Part | Meaning |
|---|---|
| `--format json` | The [**harness**](../glossary.md#harness) writes one JSON event on each line of standard output. |
| `--model <model>` | The model from the `opencode` column of the [**tier table**](../glossary.md#tier-table), in the form `<provider>/<model>`. The adapter never chooses a model. See [the model](#the-model). |
| `--agent <agent>` | The agent name from the [**stack**](../glossary.md#stack): the [**`specialist`**](../glossary.md#specialist) field, or the [**`verifier`**](../glossary.md#verifier) field for the verifier. |
| `--dir <directory>` | The working directory of the request. See [the working directory](#the-working-directory). |
| `--session <session id>` | Only for a [**continuation**](../glossary.md#continuation). See [the resume](#the-resume). |

The prompt goes on standard input. It is never in the arguments of the command, so a long brief meets no limit on arguments and shows in no process list.

The adapter passes no permission option. The permission rules of the repository and of the user decide which tools the agent can use.

## The model

The adapter reads the `opencode` column of the tier table, so a `--tiers` file changes the model for this adapter. Two options change the column from the command line: `--opencode-model` and `--opencode-allow`. Put them after `run`. Both can be repeated. [The options of the run reference](run.md#options) list them.

`--opencode-model` sets the model of one [**tier**](../glossary.md#tier) in the `opencode` column. A tier that the option does not name keeps its value. The column `claude-code` does not change. An unknown tier name exits 1. `--opencode-allow` extends the set of allowed `opencode` models; the default models stay allowed.

A model that `--opencode-model` names must be in the allowed set, or the command exits 1 and names the model. A [**run**](../glossary.md#run) has no validation step for the rendered agents, so this check stops a model that no provider serves. The options are the same as the options of `render` and `validate`.

## How the adapter selects the agent

The adapter selects the agent with `--agent <agent>`. The harness reads the agent file from the `.opencode/agents/` directory of the project (or from the user agents), and the body of that file becomes the system prompt of the [**session**](../glossary.md#session). The adapter does not put the agent body in the prompt. The [**live smoke run**](../glossary.md#live-smoke-run) below shows it: the reply of the agent follows a rule that only the agent body holds.

Two cases need care:

- **An agent that the harness does not find.** The harness does not fail. It writes `agent "<name>" not found. Falling back to default agent` on standard error, runs its default agent, and exits 0. The rules of the pair would not apply. The adapter finds this text and raises `AdapterError` that names the agent.
- **A project that the harness does not find.** See [the working directory](#the-working-directory).

## The working directory

The harness takes its project directory from the `PWD` variable that the caller inherits, not from the working directory of the process. A caller that sets the working directory and leaves `PWD` from its own shell makes the harness search the agents of the caller's directory, and run its tools there. Measured on OpenCode 1.15.10 on 2026-10-08: a run started in a scratch directory with the inherited `PWD` of another directory ended with the error `Agent not found`, and a run with `PWD` set to the scratch directory ran its tool in that directory. The adapter therefore always passes `--dir <directory>`, and a run with `--dir` ran its tool in the directory. The adapter also starts the process in that directory.

## The event stream

The adapter copies standard output to a file beside the [**report**](../glossary.md#report): `<report name>.stream.jsonl`. A later run with the same report path writes `<report name>.stream-2.jsonl`, then `-3`, and so on. The adapter never overwrites an earlier stream. The adapter writes standard error to no file; it uses standard error only in [the start failure](#the-start-failure) and for the agent that the harness does not find.

The stream holds these events. The field `type` names the event.

| Event | Meaning |
|---|---|
| `step_start` | A [**step**](../glossary.md#step) of the agent starts. |
| `text` | Text of the model. The field `part.text` holds it. |
| `tool_use` | A tool call with its input and output. |
| `step_finish` | A step ends. The field `part.reason` says why. |
| `error` | The harness reports an error, for example an API error. |

Every event has the field `sessionID`. The adapter takes the session id from the first event that has it.

The [**end state**](../glossary.md#end-state) comes from the events:

| Stream | End state |
|---|---|
| An `error` event, anywhere | `failed` |
| No `error` event, and the last `step_finish` has `reason` `stop` | `finished` |
| No `error` event, and the last `step_finish` has `reason` `length` | `capped` |
| No `error` event, and the last `step_finish` has another reason, for example `tool-calls` | `failed` |
| No `error` event and no `step_finish` event | `failed` |

Three more rules apply:

- An API error gives an `error` event and exit status 0. The `error` event decides, not the exit status.
- An exit status other than 0 turns `finished` into `failed`.
- A harness that is killed can leave half a line at the end of the stream. The adapter ignores that line. Any other line that is not JSON raises an `AdapterError` that names the stream and the line number. A last `step_finish` event with no `part.reason` field also raises an `AdapterError`.

The end state `capped` means that the model reached its output limit (`reason` `length`). Measured with the limit set to 30 tokens in the project configuration. The cap on the steps of an agent is different: see [the step cap](#the-step-cap).

### The step cap

An agent file can set `steps`, the maximum number of steps. When the agent reaches it, the harness sends one more step that has no tools, and the model answers with text. That last step ends with `reason` `stop`, and the exit status is 0. Nothing in the stream or in the exit status marks the cap. Measured on OpenCode 1.15.10 on 2026-10-08, with `steps` set to 1: the stream has the fixture `step-cap.jsonl`, and the end state is `finished`.

The [**engine**](../glossary.md#engine) then reads the report. A step that reached the cap before the agent wrote a valid report fails on the report check, and the engine starts no continuation. The adapter does not parse the text of the model to find the cap, because that text is not a contract of the harness. A repository that uses the adapter should not set `steps` on its agents.

## The resume

The adapter has `supports_resume` set to true. For a continuation, the engine sets `resume_session` in the request, and the adapter adds `--session <session id>`. The session keeps its first context. The result has the same session id.

## The start failure

When the harness exits and writes no event, no end state can describe the run. An invalid configuration file, an unknown option, and a missing login do this. The adapter raises `AdapterError` with the exit status, the agent name, and the standard error text of the harness. The engine records the error as a crash and ends the run, because the same cause stops every later agent. See [a crash in a step](run.md#a-crash-in-a-step).

## The recorded streams

The directory `tests/fixtures/opencode/` holds one recorded stream for each case and `manifest.json`. The manifest names the harness version, the date, the command, and what the redaction removed. The streams carry no version field, so the manifest is the record of the version.

| Fixture | End state | How it was made | Exit status |
|---|---|---|---|
| `finished.jsonl` | `finished` | Agent `smoke-agent`, a prompt for one sentence. | 0 |
| `capped.jsonl` | `capped` | The output limit of the model set to 30 tokens. The last step ends with `length`. | 0 |
| `failed.jsonl` | `failed` | A model that the credential refuses with a 403. The stream holds one `error` event. | 0 |
| `step-cap.jsonl` | `finished` | An agent with `steps: 1` and a prompt that needs two tool calls. See [the step cap](#the-step-cap). | 0 |
| `unknown-agent.jsonl` | none: the adapter raises | Agent `no-such-agent`. The harness runs its default agent and warns on standard error. | 0 |

Version: OpenCode 1.15.10. Recorded: 2026-10-08 (UTC).

The [**contract tests**](../glossary.md#contract-test) replay these streams through a stand-in `opencode` command and check the result fields. A test also checks that the manifest names the version and the date, and that each fixture has an entry.

## The live smoke run

| Item | Value |
|---|---|
| Harness | OpenCode 1.15.10 |
| Date | 2026-10-08 (UTC) |
| Model | `<gateway>/qwen`: a model of a [**gateway**](../glossary.md#gateway) provider in the user configuration |
| Agent | `smoke-agent`: a project agent file in `.opencode/agents/`. Its body says that a reply starts with the word `SMOKE-AGENT-ACTIVE`. |

1. The adapter ran with the prompt "Reply with the single word PINEAPPLE on the second line." The result was `finished`, exit status 0. The reply was `SMOKE-AGENT-ACTIVE` and then `PINEAPPLE`. The first word comes only from the agent body, so `--agent` selected the agent.
2. The adapter ran again with `resume_session` set to the session id of the first run, and the prompt "Which word did I ask for? Answer with that word only." The result was `finished`, exit status 0, with the same session id. The reply held `PINEAPPLE`, so the session kept its context.
3. The adapter ran with the agent `no-such-agent`. It raised `AdapterError`, which names the agent.

The first attempt at the smoke run used the adapter before `--dir` was in the command. It ended `failed`: the harness searched the agents of the directory that the caller started from. See [the working directory](#the-working-directory).

Not measured: a run in which the agent writes files and commits. Not measured: the hooks in the frontmatter of a rendered agent under `--agent`. Not measured: a run with a Claude model. The only model that the credential of the test machine allowed was `qwen`.

The command shape comes from mattpocock/sandcastle `src/AgentProvider.ts` (MIT), with `--agent` and `--dir` added after the measurement.
