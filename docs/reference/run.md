# Reference: `delegate run`

`delegate run <workflow>` builds the tickets of a workflow. For each ticket, the engine makes a worktree, spawns the specialist of the stack through a harness adapter, checks the specialist report, runs the gates itself, and records each event in a journal. A ticket that passes all of these is in the built state.

To check a workflow file without a build, use `--dry-run`. See [the workflow reference](workflow.md).

## Options

| Option | Meaning |
|---|---|
| `<workflow>` | The path of the workflow file. |
| `--dry-run` | Validate the file and print the plan. Create nothing. |
| `--repo <dir>` | The git repository to build in. The default is the current directory. |
| `--tiers <file>` | The path of a tier file. The default is the bundled tier table. |

## What a run does

1. The engine validates the workflow. A problem exits 1 and creates nothing.
2. The engine checks that `base-branch` is a branch of the repository and that no ticket branch exists. A problem exits 1 and creates nothing.
3. The engine writes `run-start` to a new journal.
4. For each ticket in dependency order, the engine does the step below. The first failed step ends the run.
5. The engine writes `run-end`.

A step has these parts:

1. Make a worktree and the branch `<run-branch>-<ticket id>` from `base-branch`.
2. Spawn the specialist through the adapter. The prompt is the specialist brief.
3. Read the report from the report path and check it against the schema.
4. Check that the branch holds at least one commit beyond `base-branch`.
5. Run each gate of the stack in the worktree. The engine runs all gates, also after a red gate.

The engine does not trust the report for the gates. A gate that is red is recorded red when the report says `gates_green` is true.

The engine does not create the run branch. It never merges, never pushes, and never closes a ticket. A worktree and a branch stay after a failed step.

## Where the engine writes

All files are in `<git common dir>/delegate/`. The working tree of the repository stays clean.

| Path | Content |
|---|---|
| `runs/<run id>/journal.jsonl` | The journal of the run. |
| `runs/<run id>/reports/<ticket id>.specialist.json` | The report path that the brief gives the specialist. |
| `worktrees/<run id>/<ticket id>/` | The worktree of the ticket. |

The run id is the UTC time and four hexadecimal characters, for example `20261008T093837Z-e9dc`.

## Output and exit codes

On stdout, the command prints `run <run id>` and `journal <path>`. On stderr, it prints one line `delegate run: ticket <id>: <reason>` for each failed step.

| Code | Meaning |
|---|---|
| 0 | Every ticket is built. |
| 1 | The workflow or the tier file is not valid, the run cannot start, or a step failed. The message names the input. No traceback is shown. |
| 2 | A usage error. |

## Why a step fails

| Reason in the journal | Cause |
|---|---|
| `the specialist ended <state> with exit status <n>` | The adapter result is `failed` or `capped`, or the exit status is not 0. The engine does not read the report. |
| `report missing: ...` | The specialist wrote no file at the report path. |
| `report ... is not valid JSON`, `must be a JSON object`, `invalid: <field>: ...` | The report does not match the schema. The message names the field. |
| `the specialist reported status 'blocked'` (or `'partial'`) | The report is valid, but its `status` is not `committed`. |
| `branch ... holds no commit beyond ...` | The report says `committed`, but the branch has no new commit. |
| `gates red: <commands>` | At least one gate command exited with a status other than 0. |

## The adapter interface

An adapter is a Python object. It registers by name with `agent_definitions.adapters.register(name, adapter)`. The workflow field `adapter` names it. The built-in names `claude-code`, `opencode`, and `cursor` are valid workflow values, but a run with one of them exits 1 until an implementation registers under that name.

The adapter has the attribute `tier_column`, which names the column of the tier table with its models. The engine resolves the model from that column. The specialist tier is `strong` in `assure` mode and `standard` in `economy` mode. The `specialist` entry of `tier-overrides` replaces it.

The adapter has the method `run(request)`.

| `request` field | Meaning |
|---|---|
| `agent` | The agent name, from the `specialist` field of the stack. |
| `model` | The model from the tier column of the adapter. |
| `prompt` | The specialist brief. |
| `cwd` | The worktree path. |
| `report_path` | The path where the agent writes its report. |

`run` returns a result.

| `result` field | Meaning |
|---|---|
| `exit_status` | The exit status of the harness process. |
| `end_state` | `finished`, `failed`, or `capped`. |
| `session_id` | The session id, or `None` when the harness gives none. |
| `event_stream` | The path of the captured event stream. |

## The specialist brief

The brief has four sections. `## Task` holds the ticket id and the ticket text. `## File boundary` holds the worktree path, the hotspot patterns of the stack, and the no-push rule. `## Gates` holds the gate commands. `## Report` holds the report path and the report fields.

## The specialist report

The specialist writes a JSON object to the report path. A field that this table does not list is ignored.

| Field | Type | Meaning |
|---|---|---|
| `ticket` | string | The ticket id. It must equal the id of the step. |
| `status` | string | `committed`, `blocked`, or `partial`. |
| `branch` | string | The branch with the work. |
| `head_sha` | string | The tip of the branch. |
| `commits` | list of strings | The commits that the specialist added. |
| `gates_green` | boolean | The claim of the specialist. The engine runs the gates itself. |
| `summary` | string | A short account of the work. |
| `worktree` | string | Optional. The worktree path. |
| `blocked_reason` | string | Optional. The reason, when `status` is not `committed`. |
| `judgement_calls` | string | Optional. The decisions that the specialist made. |

## The journal

The journal is a JSONL file. The engine only appends to it. Each line is a JSON object with `seq` (the position, from 1), `time` (UTC, ISO 8601), and `event`, plus the fields below.

| `event` | Fields |
|---|---|
| `run-start` | `run_id`, `workflow`, `mode`, `adapter`, `base_branch`, `run_branch`, `tickets` (the ids in plan order) |
| `step-start` | `ticket`, `stack`, `branch`, `worktree`, `base_commit`, `agent`, `tier`, `model` |
| `adapter-result` | `ticket`, `exit_status`, `end_state`, `session_id`, `event_stream` |
| `report-validation` | `ticket`, `valid`, `path`, `reason` (`null` when valid) |
| `gate-result` | `ticket`, `command`, `exit_status`, `green`, `output_tail` (the last 4000 characters) |
| `step-end` | `ticket`, `state` (`built` or `failed`), `reason` (`null` when built) |
| `run-end` | `result` (`built` or `failed`), `built`, `failed` (lists of ticket ids) |

## Limits of this version

- Every ticket branch starts from `base-branch`, not from the work of the tickets that block it.
- The first failed step ends the run. No later ticket starts.
- The engine does not continue a capped or failed specialist, does not resume a run, and does not verify.
- A gate has no time limit.
