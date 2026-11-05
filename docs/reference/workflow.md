---
man: DELEGATE-WORKFLOW(5)
man_name: "workflow.toml — the workflow file of a delegation run"
---

# Reference: the workflow file

A [**workflow**](../glossary.md#workflow) file is a TOML file that describes one delegation run. The [**coordinator**](../glossary.md#coordinator) writes it. The [**engine**](../glossary.md#engine) reads it.

Check a workflow file before a [**run**](../glossary.md#run):

```bash
delegate run <workflow> --dry-run
```

The command validates the file and prints the plan in dependency order. Then it prints the quality check: one `ok` line for each feature that the workflow uses, and one `warn` line for each missing setting that lowers the quality of the run or wastes time. See [the quality check](run.md#the-quality-check). With `--resume <run-id>`, the dry run compares the file with the newest plan of the run instead. See [change the workflow between resumes](#change-the-workflow-between-resumes). The command creates no branch, no [**worktree**](../glossary.md#worktree), and no [**journal**](../glossary.md#journal). It changes nothing.

The options of `delegate run` are in [the options of the run reference](run.md#options). Without `--dry-run`, the command builds the [**tickets**](../glossary.md#ticket). A [**tier file**](../glossary.md#tier-file) from `--tiers` sets the valid [**tier**](../glossary.md#tier) names. The options `--opencode-model` and `--opencode-allow` change the `opencode` column of the [**tier table**](../glossary.md#tier-table). See [the adapter reference](opencode-adapter.md#the-model).

## The setup check of the dry run

Two errors of setup show late in a [**run**](../glossary.md#run). An agent file that is missing fails the first agent of its [**stack**](../glossary.md#stack). A [**gate**](../glossary.md#gate) that the [**verifier guard**](../glossary.md#verifier-guard) does not permit shows only when the [**verifier**](../glossary.md#verifier) tries to run it, for example on a [**red proof**](../glossary.md#red-proof), on a single-test probe or in a [**fix-up**](../glossary.md#fix-up) round, and that can be an hour into the run. The gates of a stack come from two places: the workflow (`gates`, which the engine runs) and the [**declaration**](../glossary.md#declaration) of the agents (`gateCommands`, which became the allow list of the verifier guard). Nothing else compares them.

`delegate run <workflow> --dry-run` makes this check after it prints the plan and the [quality check](run.md#the-quality-check). It uses the [**adapter**](../glossary.md#adapter) of the workflow, and the check is the same for each adapter: it seals each agent of the stack into a temporary directory (`seal`), and it asks the adapter whether the guard of the sealed verifier permits each gate (`permits`). The temporary directory goes away when the check ends.

1. For each stack, the [**specialist**](../glossary.md#specialist) and the verifier must have an agent file, named `<agent>.md`. The check looks in the project directory (the `--repo` directory) first, and then in the user directory, where the [**harness**](../glossary.md#harness) looks. For `claude-code` the two directories are `<repo>/.claude/agents/` and `~/.claude/agents/`. For `opencode` they are `<repo>/.opencode/agents/` and `~/.config/opencode/agents/`. For each missing file, the message names the stack, the agent and the paths that the check looked in.
2. For each gate of each stack, the verifier file must permit the gate. For `claude-code`, the check runs the Bash hook of the verifier file with the gate as the command, as the harness does, in `bash`. A hook that exits 0 permits the gate. A hook that exits with another status denies it, and the message gives the text that the hook wrote on stderr. For `opencode`, the check resolves the gate against the `permission.bash` map of the verifier file with the matcher of `delegate validate`. Only the action `allow` permits the gate. The map leaves out a gate with a command substitution, so the check refuses such a gate for `opencode`. For each gate that the verifier denies, the message names the gate, the stack and the agent file.

The check prints `stack <name>: checked` on stdout for each stack with no problem. It prints each problem on stderr, one for each line, and the command exits 1. A stack with a missing agent file gets no gate check for that file.

3. For each stack with no problem, the check runs the [**guard probe**](../glossary.md#guard-probe) of its verifier, when the adapter has one. `claude-code` has one. `opencode` has none, because nobody measured that the harness enforces the permission map of a [**sealed agent**](../glossary.md#sealed-agent) while the agent runs (decision D-e of #213, in [**ADR**](../glossary.md#adr) 0007). See [the guard probe of the adapter](opencode-adapter.md#the-guard-probe). The probe is a short [**session**](../glossary.md#session) of the sealed verifier on the tier `cheap`, in a temporary repository outside the repository under check. It must show that the guard denies the command `touch delegate-guard-probe`. The check prints `stack <name>: guard probe denied` on stdout when it does. It prints each other result, `ran` or `inconclusive`, on stderr with the stack, the verifier and the limits page, and the command exits 1. A verifier that two stacks name gets one probe. The probe starts a session of the harness, so it costs a little, and no option turns it off. See [the guard probe](run.md#the-guard-probe).

An adapter with no check prints one line on stdout: `setup check skipped: ...`, with the reason, and it gets no probe. Only `claude-code` and `opencode` have a check. The check starts the hook of the verifier, and the probe session of a `claude-code` stack. It changes nothing on disk except its temporary directories, which it removes. `--resume <run-id> --dry-run` makes no setup check.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The workflow is valid, and the setup check found no problem. The plan and the quality check are on stdout. A `warn` line of the quality check does not change the exit code. |
| 1 | The workflow or the tier file is not valid, or it cannot be read, or the [setup check](#the-setup-check-of-the-dry-run) found a missing agent file, a gate that the verifier denies, or a guard probe that did not show a denial. Each problem is on stderr, one for each line. The message names the field, the ticket, or the [**stack**](../glossary.md#stack). With `--resume`, also: the run is unknown, the file changes a field that a [**resume**](../glossary.md#resume) keeps, or a rule of the [**plan change**](../glossary.md#plan-change) refuses the change. See [change the workflow between resumes](#change-the-workflow-between-resumes). |
| 2 | A usage error. |

The command reports the problems of a file in one run, not only the first. A ticket or a stack with a problem is left out of the later checks, so correct the problems and check the file again.

## Example

```toml
base-branch = "main"
run-branch = "run/demo"
mode = "economy"
adapter = "claude-code"

[tier-overrides]
specialist = "cheap"

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = ["python3 -m pytest -rs"]
# The engine also reserves .claude/agents/ and .opencode/agents/ in every stack.
hotspots = ["LICENSE", ".github/workflows/*"]

[[tickets]]
id = "2"
text = "The export command writes a CSV file."
stack = "python"
blocked-by = ["1"]

[[tickets]]
id = "1"
text = "The export command reads the data set."
stack = "python"
blocked-by = []
```

The plan for this example lists ticket `1` first, and ticket `2` second, because ticket `2` is blocked by ticket `1`.

## Top-level fields

| Field | Type | Meaning |
|---|---|---|
| `base-branch` | string | The branch that the run starts from. The run takes the commit of this branch at its start, and records it as the base of the run. A commit that lands on `base-branch` later is not part of the run. See [the base of the run](run.md#the-base-of-the-run). |
| `run-branch` | string | The branch that the engine owns and accepted work lands on. It must differ from `base-branch`. |
| [**`mode`**](../glossary.md#mode) | string | [**`assure`**](../glossary.md#assure) or [**`economy`**](../glossary.md#economy). |
| [**`adapter`**](../glossary.md#adapter) | string | The name of the [**harness**](../glossary.md#harness) adapter: `claude-code` or `opencode`, or the name of an adapter that is registered. See [the run reference](run.md#the-adapter-interface). |
| `ticket-start` | string | Optional. The branch that each ticket branch of `assure` mode with no `blocked-by` starts from: `run-branch` (the default in `assure` mode) or `base-branch`. See [Ticket start](#ticket-start). |
| `keep-worktrees` | boolean | Optional. `true` keeps the worktrees and the ticket branches of a run that ends `built`. The default is `false`. See [Keep worktrees](#keep-worktrees). |
| `tier-overrides` | table | Optional. The tier of a role, by tier name. See [Tier overrides](#tier-overrides). |
| `stacks` | table | One sub-table for each stack. See [Stacks](#stacks). |
| `tickets` | array of tables | At least one `[[tickets]]` table. See [Tickets](#tickets). |

All fields except `ticket-start`, `keep-worktrees` and `tier-overrides` are required. A field that this reference does not list is an error. This catches a misspelled field.

## Modes

| Mode | Meaning |
|---|---|
| `assure` | The engine verifies each branch at once, after the [**specialist**](../glossary.md#specialist) finishes it. |
| `economy` | The engine builds a [**chain**](../glossary.md#chain). It runs no [**gate**](../glossary.md#gate) for one ticket. At the end, for each stack, it runs the gates once on the chain tip, and verifies once. Each ticket branch starts from the previous ticket. The specialists run on `standard`. Red gates and a [**REJECT**](../glossary.md#reject) each start a [**fix-up**](../glossary.md#fix-up) round, up to two rounds together for each stack. A fix-up commit of one stack that changes a file of a later stack is in the [**brief**](../glossary.md#brief) of the [**verifier**](../glossary.md#verifier) of the later stack. A stack that the engine verified before the fix-up does not see it. See [the run reference](run.md#the-chain-verification) and [the fix-ups and the other stacks](run.md#the-fix-ups-and-the-other-stacks). |

## Ticket start

The field `ticket-start` sets the branch that each ticket branch with no `blocked-by` starts from in `assure` mode.

| Value | Meaning |
|---|---|
| `base-branch` | Each ticket branch starts from the base of the run: the commit of `base-branch` at the start of the run. Use it when each specialist must start from the base of the run. |
| `run-branch` | Each ticket branch starts from the tip of `run-branch` at the start of its [**step**](../glossary.md#step). The branch then holds the accepted work of each ticket before it. This is the default in `assure` mode. |

The default is `run-branch` in `assure` mode, for this reason: each ticket runs its gates once. The [**run branch**](../glossary.md#run-branch) moves only when the verifier of the current ticket accepts it, so the ticket branch holds the run branch at its step start, the rebase keeps the commit, and the engine writes `gate-skip` for the gates that are green on that commit. With `base-branch`, the rebase of a ticket after an accepted ticket changes the commit, and the gates run again. See [one green gate run for each commit](run.md#one-green-gate-run-for-each-commit). In `economy` mode the default is `base-branch`, because each ticket starts from the previous ticket.

A ticket with `blocked-by` starts from the tip of `run-branch` with both values. Its [**blockers**](../glossary.md#blocker) are accepted before its step starts, so its branch holds their commits, and its specialist sees their work.

No ticket branch starts from the tip of `base-branch`. Other work can land on `base-branch` while the run goes on. See [the base of the run](run.md#the-base-of-the-run).

Use `base-branch` only for a workflow that needs each specialist to start from the base of the run. With `base-branch`, a ticket with no `blocked-by` that changes a file that an accepted ticket changed has a conflict at [the rebase onto the run branch](run.md#the-rebase-onto-the-run-branch), and its step fails. Give such a ticket `blocked-by`, or put the tickets in separate runs. With `run-branch`, the rebase has no conflict, because the ticket branch holds the [**run branch**](../glossary.md#run-branch) already. The engine still verifies each ticket at once, after its specialist finishes it.

For each ticket, the commits of the ticket are the commits beyond the start commit, which `step-start` records in `base_commit`. The check for a commit, the [**hotspot**](../glossary.md#hotspot) guard and the [**continuation**](../glossary.md#continuation) brief use this commit, so the commits of earlier tickets do not count for the ticket.

A ticket that failed or was skipped did not move the run branch, so the next ticket does not hold its work. Use `blocked-by` when a ticket needs the work of another ticket: a ticket whose [**blocker**](../glossary.md#blocker) failed is skipped.

In `economy` mode, each ticket branch starts from the previous ticket. `ticket-start = "run-branch"` in `economy` mode is an error. `ticket-start = "base-branch"` is permitted in every mode.

`delegate run --dry-run` prints the line `each ticket starts from the run branch <run-branch>` when the value is `run-branch`. With `ticket-start = "base-branch"` in `assure` mode and two or more tickets with no `blocked-by`, the [quality check](run.md#the-quality-check) prints a `warn ticket-start` line.

## Keep worktrees

When a run ends with the result `built`, the engine removes the worktree of each ticket and deletes each ticket branch that the run branch holds. It keeps the run branch, the journal, the [**reports**](../glossary.md#report) and the gate logs. See [the clean-up](run.md#the-clean-up).

`keep-worktrees = true` turns the clean-up off for the run. The worktrees and the ticket branches then stay, as after a failed run. Use it when you want to examine the worktree of a ticket after the run. A run that ends `failed`, a paused run and a stopped run keep everything with each value.

The field can change between two resumes of a run. The value of the workflow at the `run-end` decides.

## Adapters

| Adapter | Harness |
|---|---|
| `claude-code` | Claude Code. See [the adapter reference](claude-code-adapter.md). |
| `opencode` | OpenCode. See [the adapter reference](opencode-adapter.md). |

## Tier overrides

The workflow names strengths, never models. The table `tier-overrides` sets the tier of one role, by tier name. A role that has no entry uses the tier that the mode gives it.

| Field | Type | Meaning |
|---|---|---|
| `specialist` | string | The tier name for the specialist role. |
| [**`verifier`**](../glossary.md#verifier) | string | The tier name for the verifier role. |

The valid tier names are the tier names of the tier table. The bundled table holds `strong`, `standard`, `cheap`, and `verifier`.

A model identifier in place of a tier name is an error. The message names the field, for example `tier-overrides.specialist`. A role other than `specialist` and `verifier` is also an error.

The verifier tier never goes down, in any mode. The `verifier` entry must name a tier that is as strong as the tier `verifier`: `strong` or `verifier`. A weaker tier (`standard` or `cheap`) is an error, and so is a tier that is not in the strength order of the bundled table. The message names the field, `tier-overrides.verifier`. The engine finds this error when it loads the file, so `--dry-run` reports it too.

## The tier of a ticket

The field `tier` of a ticket sets the tier of the specialist of that ticket, by tier name. A ticket with no `tier` uses the tier of the specialist role: the tier that the mode gives it, or the `specialist` entry of `tier-overrides`.

- The tier applies to the first specialist of the ticket, to each [**continuation**](../glossary.md#continuation), and to each [**fix-up**](../glossary.md#fix-up) specialist of the ticket.
- The value is a tier name of the tier table, as for `tier-overrides`. A model identifier in place of a tier name is an error. The message names the ticket and the field, for example `ticket 'a': tier: 'opus' is a model identifier, not a tier name`.
- The `tier` of a ticket does not change the verifier. The verifier tier never goes down.
- In `economy` mode, a fix-up of the [chain verification](run.md#the-chain-verification) runs in the worktree of the last ticket of the stack. It uses the strongest specialist tier of the tickets of the stack that the chain verification covers. A ticket with no `tier` counts with the tier of the specialist role. The strength order is `cheap`, `standard`, then `strong` and `verifier`, which have the same strength. When two tiers have the same strength, the tier of the first ticket in plan order wins.
- In `economy` mode, each ticket `tier` must have a place in this order. When a ticket has a `tier`, the tier of the specialist role must have a place in it too. A tier of your own tier file that is not in the order is an error, because the engine cannot pick the strongest tier. `assure` mode has no such rule.
- `step-start` and `fixup-start` record the tier and the model of the specialist. `delegate run --dry-run` prints the specialist tier of each ticket at the end of the line of the ticket, for example `ticket a (1/2): stack python, blocked by none, specialist tier strong`.
- A plan change can set or change the `tier` of a ticket that has not started. The `tier` of a ticket that has started is fixed, as its other fields are. See [change the workflow between resumes](#change-the-workflow-between-resumes).

Give a ticket a stronger tier when other tickets build on its work, or when it has a measurement or a security rule. Give the tickets that depend on it the tier of the role.

## Stacks

Each `[stacks.<name>]` table describes one stack. The fields `specialist`, `verifier`, `gates`, and `hotspots` are required. The fields `mcp-servers`, `prepare`, `gate-timeout`, `agent-timeout`, `specialist-notes`, `specialist-notes-file`, `verifier-notes` and `verifier-notes-file` are optional.

| Field | Type | Meaning |
|---|---|---|
| `specialist` | string | The name of the specialist agent for this stack. |
| `verifier` | string | The name of the verifier agent for this stack. |
| [**`gates`**](../glossary.md#gate) | list of strings or tables | The gates. The engine runs them. At least one is required. A gate is a command string, or a table `{ name = "<name>", command = "<command>" }`. See [Gate names](#gate-names). |
| [**`hotspots`**](../glossary.md#hotspot) | list of strings | The path patterns that only the coordinator can change. The list can be empty (`hotspots = []`), but the field is required. The engine stops a [**step**](../glossary.md#step) that changes a path which matches. The engine adds the built-in hotspots `.claude/agents/` and `.opencode/agents/` to the list of each stack, also when the list is empty. See [the pattern rules](run.md#the-hotspot-guard). |
| `mcp-servers` | table | The MCP servers that the agents of this stack get. See [MCP servers](#mcp-servers). With no table, the agents get no MCP server. |
| `prepare` | list of strings | The commands that make a tree ready for the agents, for example the install of the dependencies. The engine runs them, not an agent. With the field, at least one command is required. With no field, the engine runs no command. See [Prepare](#prepare). |
| `gate-timeout` | number | The time limit of one gate run, in minutes. A fraction is permitted: `0.5` is 30 seconds. The number must be greater than 0. With no field, the engine waits for a gate without a limit. See [Time limits](#time-limits). |
| `agent-timeout` | number | The time limit of one run of an agent of this stack, in minutes, with the same rules as `gate-timeout`. With no field, the engine waits for an agent without a limit. See [Time limits](#time-limits). |
| `specialist-notes` | string | The text that the engine adds to each specialist brief of this stack. Give at most one of `specialist-notes` and `specialist-notes-file`. See [Notes](#notes). |
| `specialist-notes-file` | string | The path of a local file that holds the specialist notes. The path is relative to the directory of the workflow file. The file must exist, and it must not be empty. |
| `verifier-notes` | string | The text that the engine adds to each verifier brief of this stack. Give at most one of `verifier-notes` and `verifier-notes-file`. See [Notes](#notes). |
| `verifier-notes-file` | string | The path of a local file that holds the verifier notes. The path is relative to the directory of the workflow file. The file must exist, and it must not be empty. |

### Gate names

The views of a run show each gate by a short name: the feed of `delegate watch`, the live view, the gate that runs in the table, and the reason of a red gate. A gate that is a string has the short name of its command: the first word that is not a launcher (such as `nix develop -c`, `uvx`, `npx` or `python3 -m`) in the last part of a `&&` or `;` chain that is not `cd <dir>`, with no version. So `nix develop -c python3 -m pytest -q` is `pytest`, and `cd frontend && npx vitest run --project unit` is `vitest`.

A gate can be a table instead, and its `name` is then its short name in each place. The `command` runs as for a string. A workflow with strings only is valid, and a stack can hold both forms.

```toml
[stacks.web]
specialist = "react-specialist"
verifier = "react-verifier"
gates = [
  "nix develop -c python3 -m pytest -q",
  { name = "unit", command = "cd frontend && npx vitest run --project unit" },
  { name = "e2e", command = "cd frontend && npx vitest run --project e2e" },
]
hotspots = []
```

The `name` and the `command` are strings that are not empty, and a table has no other field. When two gates of a stack have the same short name, the [quality check](run.md#the-quality-check) of `delegate run --dry-run` prints a `warn gate-names` line on stdout that names the gates and says to give each one a `name`. The warning does not refuse the workflow, and the exit code is not changed by it. The journal gets the `name` in `gate-start`, `gate-result` and `gate-skip` of a gate that has one. The verifier guard and the `gateCommands` of the declaration use the commands only.

### Time limits

A gate or an agent that hangs stops a run for ever. A stack can set a time limit for each of the two. Each limit is optional, and each stack sets its own.

```toml
[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = ["pytest -q"]
hotspots = []
gate-timeout = 30     # minutes for one gate run
agent-timeout = 90    # minutes for one run of an agent
```

- `gate-timeout` is the time limit of each run of a gate of the stack. When a gate runs longer, the engine ends the process group of the gate. The journal gets `gate-result` with `green` false and `timed_out` true. The step goes on as for a red gate: the specialist continues with the trigger `gates-red`, and the output that it gets ends with a line that names the limit. The reason of a failed step names the gate, as `gates red: pytest -q (timed out)`. The limit does not apply to the [`prepare`](#prepare) commands.
- `agent-timeout` is the time limit of each run of the specialist, the fix-up specialist and the verifier of the stack. The time counts from the start of the run of the agent. When it runs longer, whatever it does, the adapter ends its process group. The journal gets `agent-timeout`, just before the result event of the agent. The engine takes the run as one with no valid report. The [continuation rules](run.md#the-continuation) then apply: the specialist continues, and the fix-up specialist and the verifier fail the step. The idle timeout of 900 seconds is a different rule, and it applies only after a valid report.
- Each limit is a number of minutes. The number is greater than 0, and it can be a fraction. The check refuses zero, a negative number, text, `true`, and `nan` or `inf`.
- For a stack with no `gate-timeout` or no `agent-timeout`, the [quality check](run.md#the-quality-check) prints a `warn` line.

### Prepare

A tree that git makes holds no ignored file. So the worktree of a ticket, the [**temporary copy**](../glossary.md#temporary-copy) of the verifier and the proof copy hold no installed dependencies, for example no `node_modules`. An agent cannot always install them: the [**verifier guard**](../glossary.md#verifier-guard) permits only the gate commands. Put the install commands in `prepare`, not in `gates`.

```toml
[stacks.web]
specialist = "web-specialist"
verifier = "web-verifier"
gates = ["npm test"]
hotspots = []
prepare = ["npm ci"]
```

The engine runs the commands in order, with the environment of a gate, in each tree before an agent starts in it:

- In the worktree of a ticket, after the engine makes it and before the specialist starts. When a resume or a retry makes the worktree again, the commands run again before the gates.
- In the temporary copy of each verifier, before the verifier starts. The engine makes the proof copy after the commands, so the proof copy holds what they made.

A command that fails ends the step `failed` with the reason `prepare failed: <command>`. It is not a gate, and it starts no fix-up round. See [the prepare commands](run.md#the-prepare-commands).

Each command also gets the environment entry `DELEGATE_REPO`: the absolute path of the main checkout of the repository of the run. The entry is the same in the worktree and in the temporary copy. The engine adds it after the entries of the worktree. The temporary copy is a clone with no remote, so a command cannot find the main checkout from it with `git rev-parse --git-common-dir`. The gates and the agent sessions do not get the entry. A stack can use it to copy an ignored cache or index that the main checkout holds, which can take much less time than to make it again in each tree. This example copies the index `.codegraph/codegraph.db` when the main checkout has it, and runs `codegraph sync`. It copies only the database, because the lock file and the pid files of the main checkout can name a live process, and a copy of them makes `codegraph sync` fail. It removes `.codegraph` first, so it is safe when `prepare` runs again in a reused worktree:

```toml
[stacks.web]
specialist = "web-specialist"
verifier = "web-verifier"
gates = ["npm test"]
hotspots = []
prepare = [
  "npm ci",
  "if [ -f \"$DELEGATE_REPO/.codegraph/codegraph.db\" ]; then rm -rf .codegraph && mkdir .codegraph && cp \"$DELEGATE_REPO/.codegraph/codegraph.db\" .codegraph/ && codegraph sync; else codegraph init -y .; fi",
]
```

Add `.codegraph/` to the `.gitignore` of the repository, so the worktree stays clean after the last command. The file `.codegraph/.gitignore` does not ignore itself.

The worktree of a ticket must be clean after the last command. When `prepare` leaves a changed file or an untracked file that `.gitignore` does not name, the step fails before the specialist starts, with the reason `prepare left <paths> in the worktree; add them to .gitignore, or make prepare leave the worktree clean`. Without this check, the engine would take the path for uncommitted work of the specialist and start a continuation. A file that `.gitignore` names, such as `node_modules`, is not a problem. The check does not apply to the temporary copy of a verifier. `delegate run --dry-run` prints the line `stack <name> prepare: <command>` for each command.

### Notes

Some text applies to each ticket of a stack, for example how the repository is set up, or that the host has no visual gate. Put this text in the notes of the stack, not in each ticket.

```toml
[stacks.web]
specialist = "web-specialist"
verifier = "web-verifier"
gates = ["npm test"]
hotspots = []
specialist-notes = "Run `npm run setup` once before the tests."
verifier-notes-file = "notes/web-verifier.md"
```

The engine adds the text as the section `## Notes of this stack` to each [**brief**](../glossary.md#brief) of the stack:

- The specialist notes go to the specialist brief, the continuation brief and the fix-up brief of the specialist.
- The verifier notes go to the full verifier brief, the fix-up verifier brief and the chain brief.

The specialist briefs do not hold the verifier notes, and the verifier briefs do not hold the specialist notes. The notes do not replace the ticket text, and they do not change the rules of the brief. The section says that the ticket and the rules of the brief win where they differ from the notes. See [the specialist brief](run.md#the-specialist-brief) and [the verifier step](run.md#the-verifier-step). `delegate run --dry-run` prints the line `stack <name> specialist-notes:` or `stack <name> verifier-notes:`, and then each line of the notes, with two spaces before it.

### MCP servers

See [how to give the agents an MCP server](../how-to/give-the-agents-an-mcp-server.md) for the steps in order.

The table `[stacks.<name>.mcp-servers]` holds one table for each MCP server. The name of the table is the name of the server. The adapter gives these servers to the session of the specialist and of the verifier of the stack, and not to the agents of the other stacks. A rendered specialist has no list of tools, so it can use every tool of a server. The verifier can use only the tools that `verifier-tools` names. Each server has exactly one of the fields `command` and `url`:

| Field | Type | Meaning |
|---|---|---|
| `command` | list of strings | A local server: the program and its arguments. At least one entry is required. The harness starts the program with its own environment, and `env` adds settings to it. |
| `url` | string | A remote server: the URL of its endpoint. |
| `env` | table of strings | Optional. Only a server with `command` takes it. The environment settings that the harness gives to the program, in addition to its own environment: each key is a variable name and each value is a string. A value that is not a string is an error of the workflow, and so is `env` on a server with `url`. |
| `verifier-tools` | list of strings | Optional. The tools of the server that the verifier of the stack can use. Give each tool by its name in the server, without a prefix such as `mcp__<server>__`. Each entry is one exact name: an empty name and a name with `*` are errors of the workflow. At least one entry is required. |

```toml
[stacks.web.mcp-servers.browser]
command = ["npx", "@playwright/mcp@latest"]
env = { PLAYWRIGHT_BROWSERS_PATH = "/opt/browsers" }
verifier-tools = ["browser_navigate", "browser_snapshot"]

[stacks.web.mcp-servers.tracker]
url = "https://mcp.example.com/mcp"
```

In this example, the verifier of the stack `web` can use two tools of the server `browser`, and no tool of the server `tracker`. The specialist can use each tool of the two servers. The program of the server `browser` starts with the variable `PLAYWRIGHT_BROWSERS_PATH` in its environment.

A server can need a setting in its environment. For example, `codegraph serve --mcp` starts a detached daemon that stays after the session ends, and `env = { CODEGRAPH_NO_DAEMON = "1" }` stops it. Before `env`, the only way was a wrapper such as `command = ["env", "NAME=value", "<program>"]`.

A rendered verifier has a list of the tools that it can use, and a server tool that is not in that list is not available to the verifier. The instructions of the server still go to the verifier. So, without `verifier-tools`, the verifier can read about the tools of a server but cannot use them. The [quality check](run.md#the-quality-check) of the dry run gives a `warn` line for each server with no `verifier-tools`. Measured on Claude Code 2.1.287 on 2026-11-05, from the `init` event of `--output-format stream-json`, with one local server in `--mcp-config` and `--strict-mcp-config`:

| `tools` of the agent | Status of the server | MCP tools of the session |
|---|---|---|
| `[Read]` | connected | none |
| `[Read, mcp__<server>__<tool>]` | connected | `mcp__<server>__<tool>` |
| no `tools` field | connected | each tool of the server |

The engine adds the tools of `verifier-tools` to the [**sealed agent**](../glossary.md#sealed-agent) of the verifier of the stack, and to no other agent. The agent file of the verifier in the repository does not change. When one agent is the verifier of more than one stack, its seal gets the `verifier-tools` of each of those stacks. The adapter writes each tool in the form of its harness:

- `claude-code`: `mcp__<server>__<tool>` at the end of the `tools` field of the sealed agent file. See [the sealed agent](claude-code-adapter.md#the-sealed-agent).
- `opencode`: `<server>_<tool>: allow` at the end of the `permission` map of the sealed agent. See [the sealed agent](opencode-adapter.md#the-sealed-agent).

An agent file with no list of tools (no `tools` field for `claude-code`, no `permission` map for `opencode`) gives the agent every tool of a server, so the seal does not change it.

The adapter gives the servers to the harness in the form of that harness. Without the table, an agent gets no MCP server where the harness allows that. See [what the agent gets](claude-code-adapter.md#what-the-agent-gets) for the `claude-code` adapter, and [what the agent gets](opencode-adapter.md#what-the-agent-gets) for the `opencode` adapter.

## Tickets

Each `[[tickets]]` table describes one ticket. The tickets can be in any order in the file. The plan puts each ticket after all tickets that block it. Tickets that are ready at the same time keep the order of the file.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | The identifier of the ticket. It must be unique in the file. The engine uses it as a directory name and as a part of the ticket branch name, so it must match `^[A-Za-z0-9][A-Za-z0-9._-]*$` and must not hold `..`. See [Names that git and the file system take](#names-that-git-and-the-file-system-take). |
| `text` | string | The ticket text, inline. Give exactly one of `text` and `text-file`. |
| `text-file` | string | The path of a local file that holds the ticket text. The path is relative to the directory of the workflow file. The file must exist. |
| `stack` | string | The name of a stack in `stacks`. |
| `blocked-by` | list of strings | The ids of the tickets that must be done first. Use `[]` for none. |
| `tier` | string | Optional. The tier of the specialist of this ticket, by tier name. See [The tier of a ticket](#the-tier-of-a-ticket). |

The engine never contacts an issue tracker. The agent that invokes the engine writes any tracker content to disk first.

Ticket text is about behaviour and holds no file paths. The engine adds the [**file boundary**](../glossary.md#file-boundary) to the [**brief**](../glossary.md#brief) when it starts a step.

## Change the workflow between resumes

A resume reads the workflow file again. With no workflow argument, `delegate run --resume <run-id>` reads the file that the run started from.

- `mode`, `adapter`, `base-branch` and `run-branch` must not change. If one of them changes, the resume exits 1, and the message names the field.
- The tickets can change. This is a plan change. The built part of the run is fixed. A new ticket goes at the end, or before a ticket that has not started. A ticket that has not started can change, move or go. A new ticket before built work, and a change to a built ticket, are refused, and the message names the ticket and the field.
- A later ticket has not started, and comes after an added or changed ticket. Read each later ticket against the new work, then give `--reviewed <ids>` or `--reviewed all`. Without it, the resume is refused.
- A change to the `tier` of a ticket that has not started changes no work. A ticket whose only change is its `tier` makes no later ticket, so it needs no `--reviewed`.

`delegate run --resume <run-id> --dry-run` prints the old order, the new order, the [**verdict**](../glossary.md#verdict) of each rule and the tickets to review, and changes nothing. See [the plan change](run.md#the-plan-change).

## Names that git and the file system take

The check refuses a name that the engine cannot use. It runs before the run starts, so a bad name makes no branch and no `run-start`. `delegate run --dry-run` lists each problem.

- A ticket `id` must match `^[A-Za-z0-9][A-Za-z0-9._-]*$` and must not hold `..`. An absolute path, a space, a leading `-` or `.`, and `/` are refused.
- `base-branch`, `run-branch` and each ticket branch must follow the ref-name rules of git. The ticket branch is `<run-branch>-<id>`. A name is refused when it:
    - holds a space, a control character, or one of `~ ^ : ? * [ \`;
    - holds `..`, `@{` or `//`, or is `@`;
    - starts with `-` or `/`, or ends with `/` or `.`;
    - has a part (between `/`) that starts with `.` or ends with `.lock`.

The check is in Python and starts no process. For example, the id `a.lock` is a valid path segment, but the ticket branch `run/demo-a.lock` ends with `.lock`, so the check refuses the ticket and names the branch.

## Problems that the check reports

| Problem | What the message names |
|---|---|
| A field is missing, has the wrong type, or is empty. | The field, for example `stacks.python.gates`. A gate table with no `name` or no `command` names the entry, for example `stacks.python.gates[1].name`. |
| A field is not in this reference. | The field. |
| `mode` or `adapter` has an unknown name. | The field, the value, and the valid names. |
| `ticket-start` has an unknown value. | The field, the value, and the valid values. |
| `ticket-start` is `run-branch` in `economy` mode. | The field and the value. |
| `keep-worktrees` is not `true` or `false`. | The field and the value. |
| A tier override is not a tier name. | The field `tier-overrides.<role>`, the value, and the valid tier names. |
| The `tier` of a ticket is not a tier name, for example a model identifier. | The ticket, the field `tier`, the value, and the valid tier names. |
| In `economy` mode, the `tier` of a ticket has no place in the strength order of the tiers. | The ticket, the field `tier`, the value, and the tiers of the order. |
| A ticket names a stack that does not exist. | The ticket and the stack. |
| A ticket is blocked by an id that no ticket has. | The ticket and the blocker. |
| Tickets wait on each other. | Every ticket in the cycle. A ticket that blocks itself is a cycle. |
| Two tickets have one id. | The id. |
| A ticket `id` is not a safe path segment, for example `/tmp/x`, `a b`, `-a` or `a..b`. | The ticket and the rule. |
| `base-branch` or `run-branch` is not a branch name that git takes. | The field and the value. |
| A ticket branch (`<run-branch>-<id>`) is not a branch name that git takes, for example `run/demo-a.lock`. | The ticket and the branch. |
| A `text-file` does not exist. | The ticket and the path. |
| A stack gives both `specialist-notes` and `specialist-notes-file`, or both `verifier-notes` and `verifier-notes-file`. | The stack and the two fields, for example `stacks.web: give at most one of verifier-notes and verifier-notes-file`. |
| A `specialist-notes-file` or a `verifier-notes-file` does not exist, or it is empty. | The stack, the field and the path. |
| An MCP server has both `command` and `url`, or neither. | The server, for example `stacks.web.mcp-servers.browser`. |
| An MCP server has `env` and `url`, or a value of `env` is not a string. | The field, for example `stacks.web.mcp-servers.tracker.env` or `stacks.web.mcp-servers.browser.env.NAME`. |
| `gate-timeout` or `agent-timeout` is zero, a negative number, text, or not a finite number. | The field, for example `stacks.python.gate-timeout: must be greater than 0 minutes, not 0`. |
