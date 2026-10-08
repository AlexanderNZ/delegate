# Reference: the workflow file

A workflow file is a TOML file that describes one delegation run. The coordinator writes it. The engine reads it.

Check a workflow file before a run:

```bash
delegate run <workflow> --dry-run
```

The command validates the file and prints the plan in dependency order. The command creates no branch, no worktree, and no journal. It changes nothing.

Options of `delegate run`:

| Option | Meaning |
|---|---|
| `<workflow>` | The path of the workflow file. |
| `--dry-run` | Validate the file and print the plan. This option is required. A run without it is not available yet, and the command exits 2. |
| `--tiers <file>` | The path of a tier file. The default is the bundled tier table. The valid tier names are the tier names of this table. Put this option after `run`. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The workflow is valid. The plan is on stdout. |
| 1 | The workflow or the tier file is not valid, or it cannot be read. Each problem is on stderr, one for each line. The message names the field, the ticket, or the stack. |
| 2 | A usage error, for example a missing `--dry-run`. |

The command reports all problems of a file in one run, not only the first.

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
| `base-branch` | string | The branch that the run starts from. |
| `run-branch` | string | The branch that the engine owns and accepted work lands on. It must differ from `base-branch`. |
| `mode` | string | `assure` or `economy`. |
| `adapter` | string | The name of the harness adapter: `claude-code`, `opencode`, or `cursor`. |
| `tier-overrides` | table | Optional. The tier of a role, by tier name. See [Tier overrides](#tier-overrides). |
| `stacks` | table | One sub-table for each stack. See [Stacks](#stacks). |
| `tickets` | array of tables | At least one `[[tickets]]` table. See [Tickets](#tickets). |

All fields except `tier-overrides` are required. A field that this reference does not list is an error. This catches a misspelled field.

## Modes

| Mode | Meaning |
|---|---|
| `assure` | The engine verifies each branch at once, after the specialist finishes it. |
| `economy` | The engine builds a chain, and verifies once for each stack at the end. |

## Adapters

| Adapter | Harness |
|---|---|
| `claude-code` | Claude Code |
| `opencode` | OpenCode |
| `cursor` | Cursor `agent` CLI |

## Tier overrides

The workflow names strengths, never models. The table `tier-overrides` sets the tier of one role, by tier name. A role that has no entry uses the tier that the mode gives it.

| Field | Type | Meaning |
|---|---|---|
| `specialist` | string | The tier name for the specialist role. |
| `verifier` | string | The tier name for the verifier role. |

The valid tier names are the tier names of the tier table. The bundled table holds `strong`, `standard`, `cheap`, and `verifier`.

A model identifier in place of a tier name is an error. The message names the field, for example `tier-overrides.specialist`. A role other than `specialist` and `verifier` is also an error.

## Stacks

Each `[stacks.<name>]` table describes one stack. All four fields are required.

| Field | Type | Meaning |
|---|---|---|
| `specialist` | string | The name of the specialist agent for this stack. |
| `verifier` | string | The name of the verifier agent for this stack. |
| `gates` | list of strings | The gate commands. The engine runs them. At least one is required. |
| `hotspots` | list of strings | The path patterns that only the coordinator can change. The list can be empty (`hotspots = []`), but the field is required. |

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

Ticket text is about behaviour and holds no file paths. The engine adds the file boundary to the brief when it starts a step.

## Problems that the check reports

| Problem | What the message names |
|---|---|
| A field is missing, has the wrong type, or is empty. | The field, for example `stacks.python.gates`. |
| A field is not in this reference. | The field. |
| `mode` or `adapter` has an unknown name. | The field, the value, and the valid names. |
| A tier override is not a tier name. | The field `tier-overrides.<role>`, the value, and the valid tier names. |
| A ticket names a stack that does not exist. | The ticket and the stack. |
| A ticket is blocked by an id that no ticket has. | The ticket and the blocker. |
| Tickets wait on each other. | Every ticket in the cycle. A ticket that blocks itself is a cycle. |
| Two tickets have one id. | The id. |
| A `text-file` does not exist. | The ticket and the path. |
