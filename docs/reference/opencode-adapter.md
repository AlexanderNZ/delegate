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
| `--agent <agent>` | The handle of the [**sealed agent**](../glossary.md#sealed-agent), which is the agent name from the [**stack**](../glossary.md#stack): the [**`specialist`**](../glossary.md#specialist) field, or the [**`verifier`**](../glossary.md#verifier) field for the verifier. See [the sealed agent](#the-sealed-agent). |
| `--dir <directory>` | The working directory of the request. See [the working directory](#the-working-directory). |
| `--session <session id>` | Only for a [**continuation**](../glossary.md#continuation). See [the resume](#the-resume). |

The prompt goes on standard input. It is never in the arguments of the command, so a long brief meets no limit on arguments and shows in no process list.

The adapter passes no permission option. The permission rules of the repository and of the user decide which tools the agent can use. The variables `DELEGATE_AGENT_SETTINGS` and `DELEGATE_PERMISSION_MODE` of the `claude-code` adapter have no effect here: OpenCode has no option that takes a settings file or a [**permission mode**](../glossary.md#permission-mode). When either is set, the adapter writes one line for each to standard error, which names the variable and `OPENCODE_CONFIG`. A host that must give every agent its own permission rules sets `OPENCODE_CONFIG` to the path of an OpenCode configuration file with a `permission` block. The environment of the command is the environment of the [**engine**](../glossary.md#engine), so OpenCode reads that variable itself. Measured on OpenCode 1.15.10 on 2026-10-18: with `OPENCODE_CONFIG` set, `opencode debug config` shows the `permission` block of that file, and with no variable it shows none. The adapter does not read refused tools from the OpenCode stream.

The adapter does not give the `added_directories` of the request to the [**session**](../glossary.md#session). For a verifier in a `delegate run`, that is the proof copy that the engine made beside the [**temporary copy**](../glossary.md#temporary-copy) (see [the verifier step](run.md#the-verifier-step)). OpenCode has no option that adds a directory to a session: measured on OpenCode 1.15.10 on 2026-10-20, `opencode run --help` shows none. The `external_directory` map of a rendered verifier permits a temp directory, which holds the proof copy, but its `bash` map has no `cd` rule and no `cp` rule, so a test cannot run there. So an OpenCode verifier cannot use the proof copy, and it puts the [**red proof**](../glossary.md#red-proof) in `unverified`, with the refused command and its message (#152, #153). For the same reason, the adapter does not add the scratch directory of a session to the session, but the session gets `TMPDIR` set to it in the entries of `environment` (#227). See [the scratch directory of a session](run.md#the-scratch-directory-of-a-session).

The environment of the command is the environment of the [**engine**](../glossary.md#engine) with the sealed agent, and the MCP servers when the stack names some, added to `OPENCODE_CONFIG_CONTENT`. See [the sealed agent](#the-sealed-agent) and [what the agent gets](#what-the-agent-gets). Without a sealed agent and without a server, the environment is the environment of the engine.

## The permission rules

OpenCode flattens the `permission` map of an agent into rules in key order, and it keeps the last rule that matches the permission and the pattern. A rule before the catch-all `"*"` of the same map is therefore shadowed. The source is `evaluate` in `packages/core/src/permission.ts` of OpenCode at tag v1.15.10, a `findLast` over the rules, read on 2026-10-20. A pattern is an anchored glob: `*` is zero or more characters, and a trailing ` *` also matches the bare command (`packages/core/src/util/wildcard.ts` at the same tag).

For the bash tool, OpenCode parses the line and checks each simple command on its own. The text that it matches is the command with the redirection that follows it (`source` in `packages/opencode/src/tool/shell.ts` at the same tag). One deny for one command denies the line. A redirection after a group, a subshell, a loop, or the last command of a pipeline is not in the text of a command, so no pattern can deny it. Measured on OpenCode 1.15.10 on 2026-10-20 with `opencode debug agent <verifier> --tool bash` on a rendered verifier, which runs the tool with no model: `echo x > <file>` and `grep x f | tee <file>` were a deny, and `{ cat f; } > <file>` and `cat f | grep h > <file>` wrote the file. The `bash` map of a rendered verifier ends with the deny rules `*>*`, `*| tee *` and `*|tee *`, so they win over the read allows. The `*>*` rule also denies `2>&1` and `2>/dev/null`, because a glob cannot tell a file from a file descriptor. The rendered verifier map and its limits are in [the enforcement model and its limits](../explanation/the-enforcement-model-and-its-limits.md#the-limits-of-the-guards) (#123).

The `bash` map of a rendered verifier has no `git grep` rule (#140). `git grep -O<cmd>` and `git grep --open-files-in-pager[=<cmd>]` run a command on the files that match. A pattern matches the text of the command, and the shell makes the same option from other text: `-iOcat` and `-'O'cat` are `-O` with the command `cat`, `-eFOO` (the pattern `FOO`) holds no `-O`, and git accepts `--op=cat` as the long option. A deny pattern that matches each of these forms also matches most searches, so the map leaves `git grep` out, as it leaves out `echo` and `printf`. The verifier searches with its grep tool. The map keeps `git diff *`, `git log *` and `git show *`, and a pattern cannot examine the path of `--output=<file>`, so the map permits that write. The rendered OpenCode verifier states this, and tells the verifier not to use that option.

The `bash` map of a rendered verifier has no `env` rule (#158). `env` runs the command after its options and assignments, and a pattern `env *` also matches `env touch <file>` and `env -i rm <file>`. A pattern cannot examine the words after `env`, so the map leaves `env` out, and the catch-all deny denies each `env` command. The Claude Code guard removes `env` and its options, and it tests the command after them.

The `bash` map of a rendered verifier permits each plain [**gate**](../glossary.md#gate) of the [**declaration**](../glossary.md#declaration) only as its exact text: the rule is `<gate>`, with no ` *` (#163). A word after a gate can make the gate write: `npm run lint -- --fix` rewrites source files, `npm test -- -u` updates snapshots, and `./gradlew test spotlessApply` runs a task that formats the source. The Claude Code guard permits after a gate only a path that exists, the separator `--`, and a flag of `gateExtraFlags`. A pattern cannot tell a path from a word that writes, so the map permits no word after a gate, and it does not use `gateExtraFlags`. To run a gate with fixed arguments on OpenCode, declare the full command as a gate, for example `npm test -- --run`. A gate that ends with ` *` takes free arguments (#169): its rule is `<gate> *`, so `./gradlew *` permits `./gradlew test -x processTestAot`. The map permits each word after such a gate, also a word that the shell can expand, and the rendered verifier names each such gate and tells the verifier not to put a word there that can make the gate write.

The `bash` map of a rendered verifier has no `file` rule, and it permits `mktemp` only as `mktemp` and `mktemp -d` (#161). `file -C` writes a compiled magic file, and `mktemp` makes the file that its template names, or a file in the directory of `-p`. A pattern cannot examine a cluster of short options, a template or a directory, so the map denies each other form of `mktemp` and each `file` command. The Claude Code guard examines their words, and it also permits `file` without `-C`, `mktemp -t <name>`, and a template or a `-p` directory that is a temp path.

The maps of a rendered verifier cannot follow the runtime value of `$TMPDIR` (#166). The `external_directory` map allows only the fixed temp prefixes `/tmp/*`, `/private/tmp/*` and `/var/folders/*`, and the `bash` map has no rule for a temp write. An OpenCode pattern holds no environment variable. The [**renderer**](../glossary.md#renderer) does not write the `$TMPDIR` of the host that renders into the map, because the rendered file goes to other hosts. The Claude Code guards accept a path under a safe runtime `$TMPDIR`, for example `/home/agent/.tmp/tmp.X` from `mktemp -d`. On a host whose `$TMPDIR` is not under a fixed prefix, an OpenCode verifier cannot write there. The write of the [**verdict**](../glossary.md#verdict) is then a deny, and the engine reads the verdict from the final text of the verifier.

## What the agent gets

The agent gets:

- **MCP servers**: the servers of its [**stack**](../glossary.md#stack), and also the MCP servers of the user and of the project. A stack names its servers in its `mcp-servers` table (see [the workflow reference](workflow.md#mcp-servers)). The adapter writes them in the `mcp` field of the inline configuration `OPENCODE_CONFIG_CONTENT`: a local server as `type` `local` and `command` (and `environment`, when the server has an `env` table), a remote server as `type` `remote` and `url`. The harness merges that configuration with its other configuration. When the environment of the engine already holds `OPENCODE_CONFIG_CONTENT`, the adapter keeps its fields and adds the servers. When it is not a JSON object, the adapter raises `AdapterError`.
- **Tools and permissions**: the tools and the permission map of the sealed agent. The adapter removes no tool. The `"*": deny` rule of a rendered verifier also disables each MCP tool. So the seal of a verifier adds `<server>_<tool>: allow` at the end of its `permission` map for each tool of the `verifier-tools` of its stack (see [MCP servers](workflow.md#mcp-servers)).
- [**Skills**](../glossary.md#skill): every skill that the harness finds. The prompt of a new session holds the text of each skill of its definition. See [the skills of the agent](#the-skills-of-the-agent).

The harness gives no way to start an agent without the MCP servers of the user and of the project. Measured on OpenCode 1.15.10 on 2026-10-18: `opencode --help` and `opencode run --help` show no MCP option, and `--pure` removes only the external plugins. The configuration can turn off a server only by its name, and the adapter does not know the names of the servers of the user. With `OPENCODE_CONFIG_CONTENT` that named one server, `opencode mcp list` listed that server and the three servers of the user.

The harness also gives no option that loads only the skills of the agent definition. The skills of an agent definition are in the same skill directories as the other skills, so a variable that turns off a skill directory also removes the skills of the agent. The adapter sets none.

### The skills of the agent

The harness does not preload a skill for the agent of `opencode run`. Measured on OpenCode 1.15.10 on 2026-11-01 (#215), with a `qwen` model of a [**gateway**](../glossary.md#gateway) and a project skill that holds a code word: `opencode run --format json --model <model> --agent <agent> --dir <directory>` with the question on standard input gave `UNKNOWN`, and a prompt that starts with the line `/<skill>` gave `UNKNOWN`. With `--command <skill>`, the agent stated the code word with no tool call, but the option takes one skill only. So the adapter puts the text of each skill of the agent at the start of the prompt of a new session, before the [**brief**](../glossary.md#brief), as the [`claude-code` adapter](claude-code-adapter.md#the-skills-of-the-agent) does, with the same helper (#234):

- **The list of skills.** A rendered OpenCode agent has no `skills` field. Its body lists the skills in the sentence "load each of these with the skill tool: <skill>, <skill>.", and the adapter reads the list from that sentence of the sealed `prompt`. A body with no such sentence, or with the list `none declared`, gives no skill, and the prompt is the brief with no change.
- **Where the adapter finds a skill.** When it seals the agent, the adapter copies each skill of the list that the main checkout holds into `skills/<skill>/` of the seal directory. It looks in `.claude/skills/`, `.agents/skills/`, `.opencode/skills/` and `.opencode/skill/`, in this order: measured with `opencode debug skill` on OpenCode 1.15.10 on 2026-11-02, the harness finds a skill in each of these directories, and with a skill of the same name in more than one, it uses the first of this order. A skill that the seal does not hold comes from the user directory: `~/.claude/skills/`, `~/.agents/skills/`, `~/.config/opencode/skills/` or `~/.config/opencode/skill/`, the first that holds `<skill>/SKILL.md`. A skill that the adapter does not find is named in a line that tells the agent to load it with the skill tool.
- **The prompt and the load-time blocks.** The prompt and the load-time blocks are as for the `claude-code` adapter: see [the skills of the agent of that adapter](claude-code-adapter.md#the-skills-of-the-agent). Each plain load-time block runs in the working directory of the session.
- **A resumed session.** A [**continuation**](../glossary.md#continuation) with `--session` gets the brief only, because the session has the skills already.

The skill text is read when the session starts. A project skill comes from the seal, so a change of the skill in the main checkout after the seal, or in a tree, does not reach the session. The engine hashes each file of the seal, also the copied skills, before each session. The body of a rendered OpenCode agent still tells the agent to load its skills with the skill tool, and the prompt tells it that the text is there already.

## The model

The adapter reads the `opencode` column of the tier table, so a `--tiers` file changes the model for this adapter. Two options change the column from the command line: `--opencode-model` and `--opencode-allow`. Put them after `run`. Both can be repeated. [The options of the run reference](run.md#options) list them.

`--opencode-model` sets the model of one [**tier**](../glossary.md#tier) in the `opencode` column. A tier that the option does not name keeps its value. The column `claude-code` does not change. An unknown tier name exits 1. `--opencode-allow` extends the set of allowed `opencode` models; the default models stay allowed.

A model that `--opencode-model` names must be in the allowed set, or the command exits 1 and names the model. A [**run**](../glossary.md#run) has no validation step for the rendered agents, so this check stops a model that no provider serves. The options are the same as the options of `render` and `validate`.

## The sealed agent

An agent session of a run starts only from the [**sealed agent**](../glossary.md#sealed-agent) of the run (see [the agent check](run.md#the-agent-check)). The adapter seals an agent as an OpenCode configuration. At the start of the run, it finds the agent file in `.opencode/agents/` of the main checkout first, then in `~/.config/opencode/agents/`, and it writes `<agent>.json` in the seal directory, `<run directory>/agents/<agent>/`. The file is a JSON object with one entry of `agent`:

- the fields of the frontmatter of the agent file, for example `description`, `model`, `steps` and `permission`;
- `mode`, always `all`, whatever the file says (see [the mode of the agent](#the-mode-of-the-agent));
- `prompt`, the body of the agent file.

The seal directory also holds `skills/<skill>/`, a copy of each project skill that the body of the agent lists (see [the skills of the agent](#the-skills-of-the-agent)).

For a verifier whose stack names `verifier-tools` (see [MCP servers](workflow.md#mcp-servers)), the `permission` map of the entry has one rule `<server>_<tool>: allow` at its end for each of these tools. The seal replaces each character of the server name and of the tool name that is not a letter, a digit, `_` or `-` with `_`. A rule of the same key that the map already holds moves to the end. The seal adds no `permission` map to an agent file that has none.

The form of the rule comes from the program of OpenCode 1.15.10, read on 2026-11-05:

- The harness gives a tool of an MCP server the id `<server>_<tool>`, with the same replacement of characters.
- The harness disables a tool of a session when the last rule whose key matches the id of the tool has the action `deny` for each pattern. So `"*": deny` disables each MCP tool, and a later rule `<server>_<tool>: allow` makes that tool available again.

With a local server in `OPENCODE_CONFIG_CONTENT`, `opencode mcp list` showed the server as connected. `opencode debug agent <agent>` lists only the built-in tools, and `opencode debug agent <agent> --tool <server>_<tool>` gave `Tool <server>_<tool> not found` for each permission map. So no measurement shows the MCP tools of a live session. That needs a session with a model.

The handle of the sealed agent is the agent name. A file with no usable frontmatter is not sealed: the adapter raises `AdapterError` that names the file.

For the session, the adapter gives that configuration to the harness in `OPENCODE_CONFIG_CONTENT`, the variable that also holds the MCP servers (see [what the agent gets](#what-the-agent-gets)). The configuration of the variable keeps its other fields, its own servers, and its other agents. The sealed agent replaces an agent of the same name in it. Measured with `opencode debug agent` on OpenCode 1.15.10 (#213): the `prompt` of an agent in that variable replaces the prompt of a project agent of the same name in the tree, and its `permission` rules come after the rules of the agent file of the tree and of the `opencode.json` of the tree. So the tree does not change the instructions of the sealed agent, and the sealed rules win a conflict.

The engine hashes each file of the seal before each session, and a difference fails the [**step**](../glossary.md#step). The seal is of the agent file as it is when the run starts, so a later change of the agent file in the main checkout or in a tree does not reach the session.

The command names the agent with the handle, `--agent <handle>`. A request that has no sealed agent names the agent as it is, and the harness finds the agent file itself. The engine seals each agent that has a file, so this case is a test or a harness with no file.

**What was not measured.** The enforcement of the `permission` map of a sealed agent in a live session was not measured, because no model answered (owner decision D-e of #213). The adapter judges a command against the map with the matcher of the [**validator**](../glossary.md#validator) (`permits`), and the measurement of `opencode debug agent` shows that the rules are in the resolved agent. Whether the harness refuses a tool call that the map denies is not shown. So the adapter has no [guard probe](#the-guard-probe).

## The mode of the agent

`opencode run --agent <name>` refuses an agent with `mode: subagent`. Measured on OpenCode 1.15.10 on 2026-10-31 (#214): the harness writes `! agent "<name>" is a subagent, not a primary agent. Falling back to default agent` on standard error, runs its default agent, and exits 0. The prompt and the `permission` map of the agent do not apply, and the stream shows no sign. An agent with `mode: all` or `mode: primary` gives no warning. The rule is that an agent that the engine starts with `opencode run` is the agent that the workflow names, or the session fails. Three parts keep it:

- The [**renderer**](../glossary.md#renderer) writes `mode: all` for each OpenCode agent, so the agent works with `opencode run` and as a subagent of a [**coordinator**](../glossary.md#coordinator).
- The seal writes `mode: all` in the configuration of the sealed agent, also for an agent file that an older render made with `mode: subagent`.
- `agent-definitions validate` gives the [**finding**](../glossary.md#finding) `SUBAGENT_MODE` for an OpenCode agent file with `mode: subagent`. See [the finding codes](commands.md#the-finding-codes).

Three cases need care:

- **An agent that the harness does not find.** The harness does not fail. It writes `agent "<name>" not found. Falling back to default agent` on standard error, runs its default agent, and exits 0. The rules of the pair would not apply. The adapter finds this text and raises `AdapterError` that names the agent.
- **An agent that the harness falls back from.** The adapter reads the standard error of each session. When it holds a line `agent "<name>" ... Falling back to default agent`, the session ends as `failed`, with no session id, so a continuation does not resume it. The `reason` of the result names the agent of the warning, says that the harness ran its default agent, and says that the agent needs `mode: all`. The engine puts it in the reason of the failed step. This covers a subagent that the sealed configuration did not change, and any other cause of a fall-back.
- **A project that the harness does not find.** See [the working directory](#the-working-directory).

## The guard probe

The adapter has no [guard probe](run.md#the-guard-probe): `probes_guard` is false. The probe needs a session whose model runs a command that the guard must deny, and nobody measured the enforcement of the `permission` map of a sealed agent in a live session (see [the sealed agent](#the-sealed-agent)). A run with this adapter starts as before, and the dry run prints no probe line for it.

## The working directory

The harness takes its project directory from the `PWD` variable that the caller inherits, not from the working directory of the process. A caller that sets the working directory and leaves `PWD` from its own shell makes the harness search the agents of the caller's directory, and run its tools there. Measured on OpenCode 1.15.10 on 2026-10-08: a run started in a scratch directory with the inherited `PWD` of another directory ended with the error `Agent not found`, and a run with `PWD` set to the scratch directory ran its tool in that directory. The adapter therefore always passes `--dir <directory>`, and a run with `--dir` ran its tool in the directory. The adapter also starts the process in that directory.

## The event stream

The adapter copies standard output to the file that the request names in `event_stream`. The [**engine**](../glossary.md#engine) chooses that file beside the [**report**](../glossary.md#report): `<report name>.stream.jsonl`. A later run with the same report path gets `<report name>.stream-2.jsonl`, then `-3`, and so on, so no run overwrites an earlier stream. The adapter writes standard error to no file; it uses standard error only in [the start failure](#the-start-failure) and for the agent that the harness does not find.

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

Four more rules apply:

- An API error gives an `error` event and exit status 0. The `error` event decides, not the exit status.
- The exit status does not change the end state. A `finished` stream with the exit status 143 (SIGTERM) stays `finished`, and the engine reads the report. See [the end of an agent session](run.md#the-end-of-an-agent-session).
- After a valid report, the adapter ends a session that writes no event for the `idle_timeout` of the request. It sends SIGTERM to the process group of the session, and SIGKILL to the group after 10 seconds more. The result then has `ended_idle` set, and the end state comes from the stream as before.
- The adapter ends a session that runs longer than the `agent_timeout` of the request, whatever the session does. It ends the session in the same way. The result then has `ended_timeout` set. A session that wrote no event gives the end state `failed` and no session id, and the adapter raises no error for its empty stream. See [the end of an agent session](run.md#the-end-of-an-agent-session).
- A harness that is killed can leave half a line at the end of the stream. The adapter ignores that line. Any other line that is not JSON raises an `AdapterError` that names the stream and the line number. A last `step_finish` event with no `part.reason` field also raises an `AdapterError`.

The end state `capped` means that the model reached its output limit (`reason` `length`). Measured with the limit set to 30 tokens in the project configuration. The cap on the steps of an agent is different: see [the step cap](#the-step-cap).

### The step cap

An agent file can set `steps`, the maximum number of steps. When the agent reaches it, the harness sends one more step that has no tools, and the model answers with text. That last step ends with `reason` `stop`, and the exit status is 0. Nothing in the stream or in the exit status marks the cap. Measured on OpenCode 1.15.10 on 2026-10-08, with `steps` set to 1: the stream has the fixture `step-cap.jsonl`, and the end state is `finished`.

The [**engine**](../glossary.md#engine) then reads the report. A step that reached the cap before the agent wrote a valid report fails on the report check, and the engine starts no continuation. The adapter does not parse the text of the model to find the cap, because that text is not a contract of the harness. A repository that uses the adapter should not set `steps` on its agents.

## The activity

Each form of `delegate status`, and the [live view](status-and-watch.md#the-live-view) of `delegate watch`, read each stream through the method `activity` of the adapter. The stream can be the partial stream of an agent that still runs. The method gives the tokens so far, as two counts, and the time of the newest event:

- The time is the `timestamp` field of the newest event (epoch milliseconds). Every event has it.
- The tokens come from the field `part.tokens` of each `step_finish` event. It has `input`, `output`, `reasoning`, and `cache.read` and `cache.write`. The new tokens (`tokens_new`) are the sum of `input`, `output`, `reasoning`, and `cache.write` of each step. The cache tokens (`tokens_cache`) are the sum of `cache.read` of each step. The adapter does not use `total`.
- A step with no `cache` field has 0 cache reads and 0 cache writes. A `step_finish` event with no `part.tokens.input`, `part.tokens.output` or `part.tokens.reasoning` field raises an `AdapterError`. `delegate status` then shows no tokens for that agent. See [a stream that cannot be read](status-and-watch.md#a-stream-that-cannot-be-read).
- Half a line at the end of the stream is ignored, as in the end state. A step that has not finished has no tokens yet.

## The resume

The adapter has `supports_resume` set to true. For a continuation, the engine sets `resume_session` in the request, and the adapter adds `--session <session id>`. The session keeps its first context. The result has the same session id. The prompt of a resumed session holds no skill text (see [the skills of the agent](#the-skills-of-the-agent)).

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
