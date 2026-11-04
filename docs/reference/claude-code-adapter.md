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
claude --print --output-format stream-json --verbose --model <model> --setting-sources user --plugin-dir <seal directory> --agent <handle> --strict-mcp-config [--settings <path>] [--permission-mode <mode>] [--mcp-config <servers>] [--add-dir <proof copy>] --add-dir <scratch directory> --disallowedTools Monitor,TaskStop [--resume <session id>]
```

The environment of the command is the environment of the [**engine**](../glossary.md#engine), with `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` added.

| Part | Meaning |
|---|---|
| `--print` | Run without a terminal. The [**harness**](../glossary.md#harness) writes its output and exits. |
| `--output-format stream-json` | The harness writes one JSON event on each line of standard output. |
| `--verbose` | The harness needs it to write the events with `--print` and `--output-format stream-json`. |
| `--model <model>` | The model from the `claude-code` column of the [**tier table**](../glossary.md#tier-table). The adapter never chooses a model. A `--tiers` file changes it. |
| `--setting-sources user` | Load the settings of the user only. The [**session**](../glossary.md#session) ignores the project settings (`.claude/settings.json` and `.claude/settings.local.json`), the project agents, and the project skills of its tree. A file that a [**specialist**](../glossary.md#specialist) commits in a tree cannot turn off a hook with `disableAllHooks`, or replace the agent. A host gives its settings with `--settings`. See [the sealed agent](#the-sealed-agent). |
| `--plugin-dir <seal directory>` | Load the [**sealed agent**](../glossary.md#sealed-agent) as a plugin. The directory is the seal directory of the agent, `<run directory>/agents/<agent>/`. |
| `--agent <handle>` | The agent of the session: the handle of the sealed agent, `<plugin>:<agent>`. The agent name comes from the [**stack**](../glossary.md#stack): the [**`specialist`**](../glossary.md#specialist) field, or the [**`verifier`**](../glossary.md#verifier) field for the verifier. |
| `--strict-mcp-config` | Ignore every MCP configuration other than `--mcp-config`: the servers of the user, of the project, and of the plugins. See [what the agent gets](#what-the-agent-gets). |
| `--mcp-config <servers>` | Only when the stack names MCP servers in its `mcp-servers` table. One JSON object, `{"mcpServers": {...}}`: a local server as `command` and `args`, a remote server as `type` `http` and `url`. |
| `--add-dir <proof copy>` | Only for a verifier: the proof copy that the engine made for a [**red proof**](../glossary.md#red-proof). See [the proof copy of a verifier](#the-proof-copy-of-a-verifier). |
| `--add-dir <scratch directory>` | For each agent session of a [**ticket**](../glossary.md#ticket): the scratch directory of the session, in the directory of the [**run**](../glossary.md#run). See [the scratch directory](#the-scratch-directory). |
| `--disallowedTools Monitor,TaskStop` | Remove the monitor tool and the tool that stops a background task, for every agent. See [the foreground rule](#the-foreground-rule). |
| `--settings <path>` | Only when the host sets `DELEGATE_AGENT_SETTINGS`. See [the settings of a host](#the-settings-of-a-host). |
| `--permission-mode <mode>` | Only when the host sets `DELEGATE_PERMISSION_MODE`. See [the settings of a host](#the-settings-of-a-host). |
| `--resume <session id>` | Only for a [**continuation**](../glossary.md#continuation). See [the resume](#the-resume). |
| `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` | An environment variable, not an argument. It removes the `run_in_background` parameter of the Bash tool and of the subagent tool. That parameter is not a tool name, so `--disallowedTools` cannot remove it. |

The prompt goes on standard input. It is never in the arguments of the command, so a long brief meets no limit on arguments and shows in no process list.

With no setting of the host, the adapter passes no permission option other than `--disallowedTools`. The options `--mcp-config` and `--add-dir` take more than one value, so the adapter puts `--disallowedTools` after them to end their values. The permission rules of the repository and of the user decide which of the other tools the agent can use. See [the settings of a host](#the-settings-of-a-host) and [what the adapter does not set](#what-the-adapter-does-not-set).

## The settings of a host

A host can give every agent of every [**run**](../glossary.md#run) a settings file and a permission mode, with no change to a workflow file. Two environment variables of the engine set them:

| Variable | Effect |
|---|---|
| `DELEGATE_AGENT_SETTINGS` | The absolute path of a Claude Code settings file. The adapter adds `--settings <path>` to the command of each specialist, each continuation, each [**fix-up**](../glossary.md#fix-up) specialist, and each verifier. A relative path, and a path that is not a file, stop the adapter before it starts the agent: the adapter raises `AdapterError`, and the engine records a crash. |
| `DELEGATE_PERMISSION_MODE` | A permission mode of Claude Code, for example `acceptEdits`. The adapter adds `--permission-mode <mode>` to the same commands. |

With neither variable set, the command is the same as in 1.2.0. The [**verifier guard**](../glossary.md#verifier-guard) hook of the agent file still applies on top of the settings file, so a verifier stays read-only. The guard is a hook, so the settings file must not turn off hooks: do not set `disableAllHooks` in it.

A host needs these variables when its user settings refuse each tool that is not read-only. An example is `defaultMode` `"dontAsk"` with an empty allow list. Measured on the agent host on 2026-10-18: with no settings file, each `Bash` call of a specialist gave the tool result `Permission to use Bash has been denied because Claude Code is running in don't ask mode.`, so the run did no work.

The adapter reads each tool result of the stream. An error result (`is_error` true) that says that a permission mode refused a tool goes into the `refused` field of the result of the agent, with the tool and the mode. The engine writes it into the result event of the agent, and `delegate watch` shows it as a problem. See [the problem events](status-and-watch.md#the-problem-events).

## The proof copy of a verifier

To prove a test red, a verifier breaks the check in a copy of the tree with no `.git` entry, because the [**verifier guard**](../glossary.md#verifier-guard) denies a write inside a git tree. In a `delegate run`, the [**engine**](../glossary.md#engine) makes that copy, the proof copy, before the verifier starts. The adapter gives each directory of `added_directories` of the request to the [**session**](../glossary.md#session) with `--add-dir <directory>`. For a verifier, that is the proof copy. See [the verifier step](run.md#the-verifier-step).

The engine makes the copy because a host in `dontAsk` mode refuses the commands that a verifier needs to make it. Measured on the agent host on 2026-10-20 (Claude Code 2.1.283, `claude -p --permission-mode dontAsk`, the unattended settings of the host): Claude Code refused each recursive `cp` (`cp -r`, `cp -R`, `cp --recursive`, `cp -a`) with any settings, the deny list of the host refused `rm -r`, `rm -rf` and `rm -fr`, and a copy outside the working directory could not be read. With `--add-dir <proof>` and a proof directory with no `.git` entry, `ls`, `cat`, `cd <proof> && grep`, a test command after the `cd`, a Write, a Read and an Edit in the proof directory were each permitted, and the `cd` persisted (#153).

A verifier that a person spawns outside a `delegate run` keeps the recipe of the rendered verifier: it makes the copy itself with `cp -R <tree> <copy> && rm -r <copy>/.git`. On a `dontAsk` host, Claude Code refuses the recursive `cp`, so such a verifier cannot make its own proof copy. It then follows the rule for a copy that it cannot make, and puts the red proof in `unverified` (#152). To make a red proof possible there, the person makes the copy with no `.git` entry before the verifier starts, and adds it to the session with `--add-dir`.

## The scratch directory

In a `delegate run`, each agent session of a ticket gets a scratch directory, `runs/<run id>/scratch/<ticket id>/<role>/`, and the [**brief**](../glossary.md#brief) tells the agent to write each scratch file there and never to `/tmp` or to its tree (#227). See [the scratch directory of a session](run.md#the-scratch-directory-of-a-session). The adapter adds the directory to the session with `--add-dir`, as it adds the proof copy, and the session gets `TMPDIR` set to it.

Measured on Claude Code 2.1.287 on 2026-11-02, with `claude -p --permission-mode <mode> --setting-sources project --settings <rules> --add-dir <scratch>`, in a working directory with no project settings, and with a scratch directory outside the working directory:

| Mode | Allow rules | `echo x` | `echo x > <working directory>/out.txt` | `echo x > <scratch>/out.txt` | `echo x > /tmp/<file>` |
|---|---|---|---|---|---|
| `dontAsk` | `Bash(echo:*)` | permitted | refused | refused | refused |
| `dontAsk` | `Bash(echo:*)`, `Edit`, `Write` | not measured | refused | refused | refused |
| `acceptEdits` | `Bash(echo:*)` | not measured | permitted | permitted | refused |

So in `dontAsk` mode, Claude Code refuses a shell redirect to a file also in the added directory, also with the allow rules `Edit` and `Write`: a redirect is a write, and the mode refuses it. The added directory does not make a redirect possible there. In `acceptEdits` mode, a redirect into the added directory is permitted, as in the working directory, and a redirect to `/tmp` is refused. So on a `dontAsk` host an agent writes a scratch file with a tool that the allow rules of the host permit, for example the Write tool, and a tool that makes a temporary file uses `TMPDIR`. The measurement did not test a Write tool call into the added directory in `dontAsk` mode.

## The foreground rule

Each agent runs every command in the foreground. Under `--print`, the notification of a background task or of a monitor does not come to the agent. An agent that waits for one polls with sleep commands and uses its turns. A monitor that stays open keeps the [**session**](../glossary.md#session) alive after the agent writes its [**report**](../glossary.md#report), and the engine waits for the session to end.

The adapter therefore removes these tools for every agent, and the specialist brief and the verifier brief tell the agent to run every command in the foreground. Measured on Claude Code 2.1.287 on 2026-10-17: with `--disallowedTools Monitor,TaskStop`, the `init` event lists neither tool. With `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, the Bash tool has no `run_in_background` parameter.

## What the agent gets

The adapter starts each specialist and each verifier with a small context. The agent gets:

- **MCP servers**: only the servers of its [**stack**](../glossary.md#stack). By default that is none. `--strict-mcp-config` makes the harness ignore the MCP servers of the user, of the project, and of the installed plugins. A stack that needs a server names it in its `mcp-servers` table (see [the workflow reference](workflow.md#mcp-servers)), and the adapter passes it with `--mcp-config`.
- **Tools**: the built-in tools that the agent definition allows (all of them when the definition has no `tools` field), less `Monitor` and `TaskStop`. The adapter removes no other tool. An agent with a `tools` field gets only the MCP tools that the field names. So the seal of a verifier adds `mcp__<server>__<tool>` for each tool of the `verifier-tools` of its stack (see [MCP servers](workflow.md#mcp-servers)).
- **Hooks**: the hooks of the agent definition, for example the Bash guard of the verifier. They are in `hooks/hooks.json` of the seal, and the plugin runs them. `--strict-mcp-config` does not change them.
- [**Skills**](../glossary.md#skill): every skill that the harness finds, not only the skills of the agent definition. See below.

The harness gives no way to load only the skills of the agent definition. Measured on Claude Code 2.1.287 on 2026-10-18:

- `--disable-slash-commands` removes every skill and the `Skill` tool. An agent then cannot load the skills of its own definition. With it, the first turn was larger, not smaller. The adapter does not use it.
- `--bare` and `--safe-mode` also skip the hooks, so the guard of the verifier would not run. The adapter does not use them.
- `--setting-sources` without `user` drops the user settings, and the permission rules and the environment in them. The adapter does not use it.

The measurement: one `claude --print` call with model `haiku`, agent `delegate-specialist` of this repository, and the prompt "Reply with the single word OK. Do not use a tool.", in a setup with five user MCP servers. The first turn is the input, the cache writes, and the cache reads of its `usage`.

| Command | Tools | MCP tools | MCP servers | Skills | First turn (tokens) |
|---|---|---|---|---|---|
| Before: no `--strict-mcp-config` | 92 | 64 | 5 | 104 | 20023 |
| After: `--strict-mcp-config` | 28 | 0 | 0 | 104 | 17655 |
| `--strict-mcp-config --disable-slash-commands` | 27 | 0 | 0 | 0 | 19806 |

The harness of this version loads the tools of an MCP server on demand, so their names, not their schemas, are in the first turn. A call with `--strict-mcp-config` and the agent `delegate-verifier` still refused the command `whoami` with the message of the [**verifier guard**](../glossary.md#verifier-guard), so the hooks of the agent definition still apply. A call with `--strict-mcp-config` and `--mcp-config` that named two servers listed those two servers in its `init` event, and no other.

## The sealed agent

An agent session of a run starts only from the [**sealed agent**](../glossary.md#sealed-agent) of the run (see [the agent check](run.md#the-agent-check)). The adapter seals an agent as a plugin. The layout is private to the adapter, and today it is:

| File of the seal | Content |
|---|---|
| `.claude-plugin/plugin.json` | The plugin name, `delegate-seal-<agent>`. The name always starts with `delegate-seal-`. |
| `agents/<agent>.md` | The agent file with no `hooks` field, and at the end of its body the section `## Instructions of the repository` (see [the instructions of the repository](#the-instructions-of-the-repository)). Each project skill that its `skills` list names becomes `<plugin>:<skill>`. A skill that is not in `.claude/skills/` of the main checkout is a user skill: the session loads the user skills, so its name does not change. For a verifier whose stack names `verifier-tools`, the `tools` field has `mcp__<server>__<tool>` for each of these tools at its end. A `tools` field of the text form `Read, Grep` stays text, and a list stays a list. The seal adds no `tools` field to an agent file that has none. |
| `hooks/hooks.json` | The `hooks` field of the agent file, with no change, as `{"hooks": ...}`. The file is absent when the agent file has no `hooks`. |
| `skills/<skill>/` | A copy of each skill that the `skills` list names and that is in `.claude/skills/` of the main checkout. |

The handle of the sealed agent is `<plugin>:<agent>`. The files of the seal also hold `~/.claude/settings.json` (the path and the hash) when it exists, so that a change of the user settings fails the next step. The adapter finds the agent file in `.claude/agents/` of the main checkout first, then in `~/.claude/agents/`.

The command uses `--setting-sources user --plugin-dir <seal directory> --agent <handle>`. A resumed session gets the same options. Measured on Claude Code 2.1.287 (issue 213): under `--print --agent <name>`, a project agent in the tree replaces a user agent of the same name, and the harness does not run the hooks of a project agent. A committed `.claude/settings.json` with `disableAllHooks: true` turns off a guard that comes from `--agents` or from `--settings` too. With `--setting-sources user`, the session ignores the project settings, the project agents and the project skills of the tree. A plugin from `--plugin-dir` gives its agent as `<plugin>:<agent>`. The plugin ignores `hooks` in the frontmatter of its agent, and it runs the hooks of `hooks/hooks.json`, also in a tree with `disableAllHooks`.

### The guard probe

The adapter has `probes_guard`, so the engine runs [the guard probe](run.md#the-guard-probe) of each verifier with it. The probe session uses the same command as any session: `--setting-sources user --plugin-dir <seal directory> --agent <handle>`, with the model of the [**tier**](../glossary.md#tier) `cheap`. The working directory is a temporary git repository outside the repository under review. The guard of the sealed verifier must deny the command `touch delegate-guard-probe`. The adapter gives the engine the command and the text of each `Bash` call whose `tool_result` has `is_error` true, in `command_errors` of the result: a `tool_result` goes with the `tool_use` whose `id` is its `tool_use_id`. The probe is `denied` only when one of them is the probe command with a text of a denial. A final text that tells of a denial, with no such call, gives `inconclusive`. A host whose permission mode refuses the Bash tool gives `inconclusive`, because the guard did not decide.

### The instructions of the repository

`--setting-sources user` also stops the session from loading the `CLAUDE.md` of the project. So the seal holds the instructions of the repository as the main checkout has them when the engine seals. The seal adds the section `## Instructions of the repository` at the end of the body of the sealed agent file. The section holds the text of `CLAUDE.md` and then the text of `.claude/CLAUDE.md` of the main checkout, each one that exists.

- **Imports.** A line that is one import, `@<path>`, with no other word on the line, is replaced by the text of that file. The path counts from the directory of the file that holds the line. An import in an imported file is replaced too, to a depth of 5: the imports of the fifth imported file keep their lines. A path that gives no readable file keeps its import line. An import line in a code block (between two lines that start with three backticks or three tildes) is not replaced.
- **`CLAUDE.local.md`.** The seal does not hold it: it holds personal settings.
- **No `CLAUDE.md`.** A repository with neither file gets no section, and the sealed file is as it was before.
- **A change after the seal.** The text is in the sealed agent file, so a change of `CLAUDE.md` or of an imported file after the seal fails no [**step**](../glossary.md#step) and does not reach the session. [`--agents-changed`](run.md#the-agent-check) seals the new text.
- **An unreadable file.** A `CLAUDE.md` or `.claude/CLAUDE.md` that exists and cannot be read as UTF-8 text makes `seal` raise an `AdapterError`.

The project settings of a repository therefore no longer apply to an engine session. A host gives its settings with `DELEGATE_AGENT_SETTINGS` (see [the settings of a host](#the-settings-of-a-host)).

The adapter refuses to seal when `claude --version` names a version before 2.1.287, with an `AdapterError` that names the version. It does not refuse when the `claude` command is missing, or when its output has no version: the session then fails with its own error.

After a session, the adapter reads the `init` event of the stream. When the agents that the event lists do not hold the handle, the harness did not load the sealed agent. The session ends `failed`, with no session id and with the reason `the harness did not load the sealed agent <handle>`. The engine puts the reason in the reason of the failed step. A stream with no `init` event is not checked.

A request with no sealed agent starts no session: the adapter raises `AdapterError`.

### The skills of the agent

The harness does not preload a skill for the main agent of a session. Measured on Claude Code 2.1.287 on 2026-11-01 (#215): under `claude --print --agent <name>`, the `skills` list of the agent put the skill in the skill list of the session, but its text was not in the context. A prompt that starts with the line `/<skill>` loads that skill, but only the first such line. So the adapter puts the text of each skill of the `skills` list of the sealed agent file at the start of the prompt of a new session, before the [**brief**](../glossary.md#brief):

- **The prompt.** The prompt starts with the heading `# The skills of this session` and a paragraph that names the skills. Then each skill is between the lines `<skill name="<skill>">` and `</skill>`, with the line `Base directory for this skill: <directory>` and the text of its `SKILL.md` with no frontmatter. Then the heading `# The brief`, and the brief with no change.
- **Where the adapter finds a skill.** A skill `<plugin>:<skill>` of the seal is in `skills/<skill>/` of the seal, and the prompt names it `<skill>`. A skill with no colon is a user skill, in `~/.claude/skills/<skill>/`. A skill of another plugin, and a skill whose `SKILL.md` is not there, is named in a line that tells the agent to load it with the skill tool.
- **The load-time blocks.** A load-time block is a fenced block that opens with `` ```! ``, or an inline command `` !`<command>` ``. The adapter runs the command in the working directory of the session, with the environment of the session, and the output goes in the place of the block, as the harness does when it loads a skill. A command that exits with a status other than 0 gives a line with its status, its output and its error. The adapter runs a command only when it is one plain command: one line, with no `$`, backtick, `;`, `&`, `|`, `<`, `>`, parenthesis, brace, bracket, `*`, `?`, `~`, `!` or `#` outside quotes, and with no `$` or backtick in double quotes. It runs the command with no shell, for at most 30 seconds. A block of another form does not run: its text stays, with a line that tells the agent to run the command.
- **A resumed session.** A [**continuation**](../glossary.md#continuation) with `--resume` gets the brief only, because the session has the skills already.
- **No skill.** An agent whose `skills` list is empty or absent gets the brief with no change.

The skill text is read when the session starts, not when the engine seals. A project skill comes from the seal, so a change of the skill in a tree does not reach the session. A user skill comes from the user directory.

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

- The exit status does not change the end state. A `finished` stream with the exit status 143 (SIGTERM) stays `finished`, and the engine reads the report. See [the end of an agent session](run.md#the-end-of-an-agent-session).
- After a valid report, the adapter ends a session that writes no event for the `idle_timeout` of the request. It sends SIGTERM to the process group of the session, and SIGKILL to the group after 10 seconds more. The result then has `ended_idle` set, and the end state comes from the stream as before.
- The adapter ends a session that runs longer than the `agent_timeout` of the request, whatever the session does. It ends the session in the same way. The result then has `ended_timeout` set. A session that wrote no event gives the end state `failed` and no session id, and the adapter raises no error for its empty stream. See [the end of an agent session](run.md#the-end-of-an-agent-session).
- A harness that is killed can leave half a line at the end of the stream. The adapter ignores that line. Any other line that is not JSON raises an `AdapterError` that names the stream and the line number.

An API error (for example, a model that does not exist) gives `is_error` true with `subtype` `success`. The `is_error` field decides, not `subtype`.

## The activity

Each form of `delegate status`, and the [live view](status-and-watch.md#the-live-view) of `delegate watch`, read each stream through the method `activity` of the adapter. The stream can be the partial stream of an agent that still runs. The method gives the tokens so far, as two counts, and the time of the newest event:

- The time is the `timestamp` field of the newest event that has one (ISO 8601, UTC). The `assistant` and `user` events have it. The `system`, `rate_limit_event`, `tool_progress` and `result` events do not.
- Some events with no `timestamp` show that the agent works now. While a tool runs, the harness writes a `tool_progress` event with `"heartbeat": true` every 30 seconds. While the model thinks, it writes `system` events with the subtype `thinking_tokens`. When the newest event of the stream is one of these, the time is the modification time of the stream file: the time that the harness last wrote to it. Measured on Claude Code 2.1.287 on 2026-10-16.
- When the newest event is another event with no `timestamp` (`init`, `rate_limit_event`, or `result`), the time stays the `timestamp` of the newest event that has one. A stream with no event that has a `timestamp` and no event of work gives no time.
- A `usage` object gives two counts. The new tokens (`tokens_new`) add `input_tokens`, `cache_creation_input_tokens`, and `output_tokens`. The output holds the thinking tokens. The cache tokens (`tokens_cache`) are `cache_read_input_tokens`. Each turn reads the whole context again from the cache, so the cache count grows with the turns times the size of the context.
- The harness writes one `result` event at the end of each session. Its `usage` holds the true counts of the session, so the adapter uses it. A resumed session writes a second `result` event in the same stream: the adapter adds the counts of each `result` event.
- The `message.usage` of an `assistant` event is partial: its `output_tokens` is not final. For the part of the stream after the last `result` event (a session that runs now), the adapter adds the turns: one turn for each message id. The harness writes one event for each content block of a message, and each of these events repeats the usage of the message, so the adapter keeps the newest usage of each message id.
- Half a line at the end of the stream is ignored, as in the end state. A stream with no `result` event and no `assistant` event gives 0 new tokens and 0 cache tokens.

For example, the recorded stream of a session that finished has one message with the partial new count 10 + 19176 + 6 = 19192, and a `result` event with the new count 10 + 19176 + 140 = 19326. Its cache count is 0. The counts of the stream are 19326 new and 0 cache. While a session runs, its new count is therefore lower than its final count. The recorded stream of a capped session has a `result` event with the input 10, the cache creation 11808, the cache read 13970, and the output 234: 12052 new and 13970 cache.

## The resume

The adapter has `supports_resume` set to true. For a continuation, the [**engine**](../glossary.md#engine) sets `resume_session` in the request, and the adapter adds `--resume <session id>`. The session keeps its first context and its agent. The result has the same session id. The prompt of a resumed session holds no skill text (see [the skills of the agent](#the-skills-of-the-agent)).

## The start failure

When the harness exits and writes no event, no end state can describe the [**run**](../glossary.md#run). An unknown agent, an unknown option, and a missing login do this. The adapter raises `AdapterError` with the exit status, the agent name, and the standard error text of the harness. The engine records the error as a crash and ends the run, because the same cause stops every later agent. See [a crash in a step](run.md#a-crash-in-a-step).

## What the adapter does not set

A headless session cannot ask for a permission. A tool use that the rules do not allow is denied, and the result event lists it in `permission_denials`. Measured on Claude Code 2.1.287 on 2026-10-08: a `Write` to a file was denied in a headless run with no allow rule, and it was still denied with a project rule `Write` in `.claude/settings.json`. The adapter therefore does not decide the permission posture. The adopter sets the allow rules of the repository, the host sets [the settings of a host](#the-settings-of-a-host), or the [**coordinator**](../glossary.md#coordinator) changes the adapter. A run that needs a tool without a rule ends with the agent reporting the denial, and the [**step**](../glossary.md#step) fails on the report check.

The report path is in the [**state directory**](../glossary.md#state-directory) of the repository, outside the [**worktree**](../glossary.md#worktree). The agent must be able to write there.

## The recorded streams

The directory `tests/fixtures/claude-code/` holds one recorded stream for each end state and `manifest.json`. The manifest names the harness version, the date, the command, and what the redaction removed.

| Fixture | End state | How it was made | Exit status |
|---|---|---|---|
| `finished.jsonl` | `finished` | Model `haiku`, agent `smoke-agent`, a prompt for one sentence. | 0 |
| `capped.jsonl` | `capped` | Model `haiku`, `--max-turns 1`, a prompt that needs two tool calls. | 1 |
| `failed.jsonl` | `failed` | Model `no-such-model-xyz`, which the API refuses with a 404. | 1 |

Version: Claude Code 2.1.287. Recorded: 2026-10-08 (UTC). In the `init` event of each stream, the list of agents holds the handles of the two sealed agents of the tests, `delegate-seal-python-specialist:python-specialist` and `delegate-seal-python-verifier:python-verifier`. The recording listed no agent, so the lists were set for the tests of the sealed agent. A request that seals another agent gets the failure `the harness did not load the sealed agent`.

The directory `tests/fixtures/claude-code-progress/` holds parts of a stream of a real run, with their own `manifest.json`: `heartbeats.jsonl` holds four `tool_progress` heartbeat events, and `thinking.jsonl` holds two `thinking_tokens` events. These events have no `timestamp`. A test puts a part after the timed events of a recorded stream, to check [the activity](#the-activity) of a stream that ends with them. Version: Claude Code 2.1.287. Recorded: 2026-10-16 (UTC).

The directory `tests/fixtures/claude-code-probe/` holds `denied.jsonl`, a stream that a session of the [**guard probe**](../glossary.md#guard-probe) writes when the guard denies the command, with its own `manifest.json`. It is written by hand in the form of the recorded streams, and it is not a recording. It has no `init` event.

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

Not measured: a run in which the agent writes files and commits. The permission posture is open. See [what the adapter does not set](#what-the-adapter-does-not-set). The hooks in the frontmatter of a rendered agent under `--agent` were measured later, on 2026-10-18: see [what the agent gets](#what-the-agent-gets).

The command shape comes from mattpocock/sandcastle `src/AgentProvider.ts` (MIT), with `--verbose` and `--agent` added after the measurement.
