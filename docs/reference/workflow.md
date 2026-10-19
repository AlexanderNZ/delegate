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

The command validates the file and prints the plan in dependency order. With `--resume <run-id>`, the dry run compares the file with the newest plan of the run instead. See [change the workflow between resumes](#change-the-workflow-between-resumes). The command creates no branch, no [**worktree**](../glossary.md#worktree), and no [**journal**](../glossary.md#journal). It changes nothing.

The options of `delegate run` are in [the options of the run reference](run.md#options). Without `--dry-run`, the command builds the [**tickets**](../glossary.md#ticket). A [**tier file**](../glossary.md#tier-file) from `--tiers` sets the valid [**tier**](../glossary.md#tier) names. The options `--opencode-model` and `--opencode-allow` change the `opencode` column of the [**tier table**](../glossary.md#tier-table). See [the adapter reference](opencode-adapter.md#the-model).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The workflow is valid. The plan is on stdout. |
| 1 | The workflow or the tier file is not valid, or it cannot be read. Each problem is on stderr, one for each line. The message names the field, the ticket, or the [**stack**](../glossary.md#stack). With `--resume`, also: the run is unknown, the file changes a field that a [**resume**](../glossary.md#resume) keeps, or a rule of the plan change refuses the change. See [change the workflow between resumes](#change-the-workflow-between-resumes). |
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
| [**`adapter`**](../glossary.md#adapter) | string | The name of the [**harness**](../glossary.md#harness) adapter: `claude-code`, `opencode`, or `cursor`, or the name of an adapter that is registered. A run with `cursor` exits 1 until an implementation registers under that name. See [the run reference](run.md#the-adapter-interface). |
| `ticket-start` | string | Optional. The branch that each ticket branch of `assure` mode with no `blocked-by` starts from: `base-branch` (the default) or `run-branch`. See [Ticket start](#ticket-start). |
| `tier-overrides` | table | Optional. The tier of a role, by tier name. See [Tier overrides](#tier-overrides). |
| `stacks` | table | One sub-table for each stack. See [Stacks](#stacks). |
| `tickets` | array of tables | At least one `[[tickets]]` table. See [Tickets](#tickets). |

All fields except `ticket-start` and `tier-overrides` are required. A field that this reference does not list is an error. This catches a misspelled field.

## Modes

| Mode | Meaning |
|---|---|
| `assure` | The engine verifies each branch at once, after the [**specialist**](../glossary.md#specialist) finishes it. |
| `economy` | The engine builds a [**chain**](../glossary.md#chain). It runs no [**gate**](../glossary.md#gate) for one ticket. At the end, for each stack, it runs the gates once on the chain tip, and verifies once. Each ticket branch starts from the previous ticket. The specialists run on `standard`. Red gates and a [**REJECT**](../glossary.md#reject) each start a [**fix-up**](../glossary.md#fix-up) round, up to two rounds together for each stack. A fix-up commit of one stack that changes a file of a later stack is in the [**brief**](../glossary.md#brief) of the [**verifier**](../glossary.md#verifier) of the later stack. A stack that the engine verified before the fix-up does not see it. See [the run reference](run.md#the-chain-verification) and [the fix-ups and the other stacks](run.md#the-fix-ups-and-the-other-stacks). |

## Ticket start

The field `ticket-start` sets the branch that each ticket branch with no `blocked-by` starts from in `assure` mode.

| Value | Meaning |
|---|---|
| `base-branch` | Each ticket branch starts from the base of the run: the commit of `base-branch` at the start of the run. This is the default. |
| `run-branch` | Each ticket branch starts from the tip of `run-branch` at the start of its [**step**](../glossary.md#step). The branch then holds the accepted work of each ticket before it. |

A ticket with `blocked-by` starts from the tip of `run-branch` with both values. Its [**blockers**](../glossary.md#blocker) are accepted before its step starts, so its branch holds their commits, and its specialist sees their work.

No ticket branch starts from the tip of `base-branch`. Other work can land on `base-branch` while the run goes on. See [the base of the run](run.md#the-base-of-the-run).

Use `run-branch` for a sequential chain of tickets that change the same files. With `base-branch`, a ticket with no `blocked-by` that changes a file that an accepted ticket changed has a conflict at [the rebase onto the run branch](run.md#the-rebase-onto-the-run-branch), and its step fails. Give such a ticket `blocked-by`, or put the tickets in separate runs. With `run-branch`, the rebase has no conflict, because the ticket branch holds the [**run branch**](../glossary.md#run-branch) already. The engine still verifies each ticket at once, after its specialist finishes it.

For each ticket, the commits of the ticket are the commits beyond the start commit, which `step-start` records in `base_commit`. The check for a commit, the [**hotspot**](../glossary.md#hotspot) guard and the [**continuation**](../glossary.md#continuation) brief use this commit, so the commits of earlier tickets do not count for the ticket.

A ticket that failed or was skipped did not move the run branch, so the next ticket does not hold its work. Use `blocked-by` when a ticket needs the work of another ticket: a ticket whose [**blocker**](../glossary.md#blocker) failed is skipped.

In `economy` mode, each ticket branch starts from the previous ticket. `ticket-start = "run-branch"` in `economy` mode is an error. `ticket-start = "base-branch"` is permitted in every mode.

`delegate run --dry-run` prints the line `each ticket starts from the run branch <run-branch>` when the value is `run-branch`.

## Adapters

| Adapter | Harness |
|---|---|
| `claude-code` | Claude Code. See [the adapter reference](claude-code-adapter.md). |
| `opencode` | OpenCode. See [the adapter reference](opencode-adapter.md). |
| `cursor` | Cursor `agent` CLI. The name is valid, and `--dry-run` accepts it, but no adapter implements it yet: a run with `cursor` exits 1. |

## Tier overrides

The workflow names strengths, never models. The table `tier-overrides` sets the tier of one role, by tier name. A role that has no entry uses the tier that the mode gives it.

| Field | Type | Meaning |
|---|---|---|
| `specialist` | string | The tier name for the specialist role. |
| [**`verifier`**](../glossary.md#verifier) | string | The tier name for the verifier role. |

The valid tier names are the tier names of the tier table. The bundled table holds `strong`, `standard`, `cheap`, and `verifier`.

A model identifier in place of a tier name is an error. The message names the field, for example `tier-overrides.specialist`. A role other than `specialist` and `verifier` is also an error.

The verifier tier never goes down, in any mode. The `verifier` entry must name a tier that is as strong as the tier `verifier`: `strong` or `verifier`. A weaker tier (`standard` or `cheap`) is an error, and so is a tier that is not in the strength order of the bundled table. The message names the field, `tier-overrides.verifier`. The engine finds this error when it loads the file, so `--dry-run` reports it too.

## Stacks

Each `[stacks.<name>]` table describes one stack. The fields `specialist`, `verifier`, `gates`, and `hotspots` are required. The field `mcp-servers` is optional.

| Field | Type | Meaning |
|---|---|---|
| `specialist` | string | The name of the specialist agent for this stack. |
| `verifier` | string | The name of the verifier agent for this stack. |
| [**`gates`**](../glossary.md#gate) | list of strings | The gate commands. The engine runs them. At least one is required. |
| [**`hotspots`**](../glossary.md#hotspot) | list of strings | The path patterns that only the coordinator can change. The list can be empty (`hotspots = []`), but the field is required. The engine stops a [**step**](../glossary.md#step) that changes a path which matches. See [the pattern rules](run.md#the-hotspot-guard). |
| `mcp-servers` | table | The MCP servers that the agents of this stack get. See [MCP servers](#mcp-servers). With no table, the agents get no MCP server. |

### MCP servers

The table `[stacks.<name>.mcp-servers]` holds one table for each MCP server. The name of the table is the name of the server. The specialist and the verifier of the stack get these servers, and the agents of the other stacks do not. Each server has exactly one of these fields:

| Field | Type | Meaning |
|---|---|---|
| `command` | list of strings | A local server: the program and its arguments. At least one entry is required. The harness starts the program with its own environment. |
| `url` | string | A remote server: the URL of its endpoint. |

```toml
[stacks.web.mcp-servers.browser]
command = ["npx", "@playwright/mcp@latest"]

[stacks.web.mcp-servers.tracker]
url = "https://mcp.example.com/mcp"
```

The adapter gives the servers to the harness in the form of that harness. Without the table, an agent gets no MCP server where the harness allows that. See [what the agent gets](claude-code-adapter.md#what-the-agent-gets) for the `claude-code` adapter, and [what the agent gets](opencode-adapter.md#what-the-agent-gets) for the `opencode` adapter.

## Tickets

Each `[[tickets]]` table describes one ticket. The tickets can be in any order in the file. The plan puts each ticket after all tickets that block it. Tickets that are ready at the same time keep the order of the file.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | The identifier of the ticket. It must be unique in the file. |
| `text` | string | The ticket text, inline. Give exactly one of `text` and `text-file`. |
| `text-file` | string | The path of a local file that holds the ticket text. The path is relative to the directory of the workflow file. The file must exist. |
| `stack` | string | The name of a stack in `stacks`. |
| `blocked-by` | list of strings | The ids of the tickets that must be done first. Use `[]` for none. |

The engine never contacts an issue tracker. The agent that invokes the engine writes any tracker content to disk first.

Ticket text is about behaviour and holds no file paths. The engine adds the [**file boundary**](../glossary.md#file-boundary) to the [**brief**](../glossary.md#brief) when it starts a step.

## Change the workflow between resumes

A resume reads the workflow file again. With no workflow argument, `delegate run --resume <run-id>` reads the file that the run started from.

- `mode`, `adapter`, `base-branch` and `run-branch` must not change. If one of them changes, the resume exits 1, and the message names the field.
- The tickets can change. This is a plan change. The built part of the run is fixed. A new ticket goes at the end, or before a ticket that has not started. A ticket that has not started can change, move or go. A new ticket before built work, and a change to a built ticket, are refused, and the message names the ticket and the field.
- A later ticket has not started, and comes after an added or changed ticket. Read each later ticket against the new work, then give `--reviewed <ids>` or `--reviewed all`. Without it, the resume is refused.

`delegate run --resume <run-id> --dry-run` prints the old order, the new order, the [**verdict**](../glossary.md#verdict) of each rule and the tickets to review, and changes nothing. See [the plan change](run.md#the-plan-change).

## Problems that the check reports

| Problem | What the message names |
|---|---|
| A field is missing, has the wrong type, or is empty. | The field, for example `stacks.python.gates`. |
| A field is not in this reference. | The field. |
| `mode` or `adapter` has an unknown name. | The field, the value, and the valid names. |
| `ticket-start` has an unknown value. | The field, the value, and the valid values. |
| `ticket-start` is `run-branch` in `economy` mode. | The field and the value. |
| A tier override is not a tier name. | The field `tier-overrides.<role>`, the value, and the valid tier names. |
| A ticket names a stack that does not exist. | The ticket and the stack. |
| A ticket is blocked by an id that no ticket has. | The ticket and the blocker. |
| Tickets wait on each other. | Every ticket in the cycle. A ticket that blocks itself is a cycle. |
| Two tickets have one id. | The id. |
| A `text-file` does not exist. | The ticket and the path. |
| An MCP server has both `command` and `url`, or neither. | The server, for example `stacks.web.mcp-servers.browser`. |
