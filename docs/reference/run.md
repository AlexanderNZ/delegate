# Reference: `delegate run`

`delegate run <workflow>` builds the tickets of a workflow. For each ticket, the engine makes a worktree, spawns the specialist of the stack through a harness adapter, checks the specialist report, runs the gates itself, and records each event in a journal. In `assure` mode, the engine then verifies the branch with a blind verifier, and moves the run branch to the branch only on ACCEPT. A ticket that passes all of these is in the built state.

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
3. The engine creates the run branch at `base-branch`.
4. The engine writes `run-start` to a new journal.
5. For each ticket in dependency order, the engine does the step below. The first failed step ends the run.
6. The engine writes `run-end`.

A step has these parts:

1. Make a worktree and the branch `<run-branch>-<ticket id>` from `base-branch`.
2. Spawn the specialist through the adapter. The prompt is the specialist brief.
3. Read the report from the report path and check it against the schema.
4. Check that the branch holds at least one commit beyond `base-branch`.
5. Run each gate of the stack in the worktree. The engine runs all gates, also after a red gate.
6. In `assure` mode, verify the branch. See [the verifier step](#the-verifier-step).

The engine does not trust the report for the gates. A gate that is red is recorded red when the report says `gates_green` is true.

The engine never merges to `base-branch`, never pushes, and never closes a ticket. A worktree and a branch stay after a failed step.

## The verifier step

In `assure` mode, the engine verifies each ticket branch that has green gates. In `economy` mode, the engine does not verify, and it does not move the run branch.

1. The engine makes a temporary copy of the branch. The copy is a clone with its own git directory and no remote. It holds the run branch and the ticket branch, and the ticket branch is checked out. The verifier can break the copy, and cannot reach the real repository through it. The engine removes the copy when the verifier ends.
2. The engine makes the verifier brief with the brief generator (`full_brief`). The brief holds the task (the ticket text), the diff `git diff <run-branch>...<ticket branch>`, the gates of the stack, the path of the copy, and the report path. The brief never holds a line of the specialist report.
3. The engine spawns the verifier of the stack through the adapter. The working directory is the copy. The tier is `verifier`, whatever the tier of the specialist is. The `verifier` entry of `tier-overrides` replaces it. The model comes from the tier column of the adapter.
4. The engine reads the verdict report and checks it against the schema.
5. On ACCEPT, the engine moves the run branch to the commit that the verifier saw. It moves the branch only by fast-forward. If the run branch holds a commit that the ticket branch does not hold, the step fails and the run branch does not change.
6. On REJECT, the run branch does not change. The step fails, the journal holds the findings, and the command prints them on stderr and exits 1.

The engine keeps the verdict report and the event stream of the verifier in `runs/<run id>/verifier/<ticket id>/`.

## Where the engine writes

All files are in `<git common dir>/delegate/`. The working tree of the repository stays clean.

| Path | Content |
|---|---|
| `runs/<run id>/journal.jsonl` | The journal of the run. |
| `runs/<run id>/reports/<ticket id>.specialist.json` | The report path that the brief gives the specialist. |
| `runs/<run id>/verifier/<ticket id>/` | The verdict report (`verdict.json`) and the files that the verifier run wrote beside it. |
| `worktrees/<run id>/<ticket id>/` | The worktree of the ticket. |

The run id is the UTC time and four hexadecimal characters, for example `20261008T093837Z-e9dc`.

## Output and exit codes

On stdout, the command prints `run <run id>` and `journal <path>`. On stderr, it prints one line `delegate run: ticket <id>: <reason>` for each failed step.

| Code | Meaning |
|---|---|
| 0 | Every ticket is built. In `assure` mode, the verifier accepted every ticket. |
| 1 | The workflow or the tier file is not valid, the run cannot start, a step failed, or a verifier rejected a ticket. The message names the input. No traceback is shown. |
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
| `the verifier could not start: ...` | The engine could not make the copy or the verifier brief. The message holds the cause. |
| `the verifier ended <state> with exit status <n>` | The verifier result is `failed` or `capped`, or the exit status is not 0. The engine does not read the verdict report. |
| `report missing: ...`, `report ... is invalid: <field>: ...` | The verdict report does not match the schema. The message names the field. A report that names a mode other than `full` is invalid. |
| `the verifier rejected the branch: <findings>` | The verdict is REJECT. |
| `run branch <name> cannot fast-forward to <branch>: ...` | The verdict is ACCEPT, but the run branch holds a commit that the ticket branch does not hold. |

## The adapter interface

An adapter is a Python object. It registers by name with `agent_definitions.adapters.register(name, adapter)`. The workflow field `adapter` names it. The built-in names `claude-code`, `opencode`, and `cursor` are valid workflow values, but a run with one of them exits 1 until an implementation registers under that name.

The adapter has the attribute `tier_column`, which names the column of the tier table with its models. The engine resolves the model from that column. The specialist tier is `strong` in `assure` mode and `standard` in `economy` mode. The `specialist` entry of `tier-overrides` replaces it. The verifier tier is `verifier` in every mode. The `verifier` entry of `tier-overrides` replaces it.

The adapter has the method `run(request)`.

| `request` field | Meaning |
|---|---|
| `agent` | The agent name, from the `specialist` field of the stack, or from the `verifier` field for the verifier. |
| `model` | The model from the tier column of the adapter. |
| `prompt` | The specialist brief, or the verifier brief. |
| `cwd` | The worktree path for the specialist. The path of the temporary copy for the verifier. |
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

## The verdict report

The verifier writes a JSON object to the report path. A field that this table does not list is ignored.

| Field | Type | Meaning |
|---|---|---|
| `mode` | string | The mode of the verifier. It must be `full`. |
| `verdict` | string | `ACCEPT` or `REJECT`. |
| `criteria` | list of objects | One object for each acceptance criterion, with the strings `criterion` and `evidence`. An ACCEPT needs at least one. |
| `gate_output` | string | The output of the gates that the verifier ran. |
| `findings` | list of strings | The defects. A REJECT needs at least one. |
| `unverified` | list of strings | What the verifier could not verify. |

## The journal

The journal is a JSONL file. The engine only appends to it. Each line is a JSON object with `seq` (the position, from 1), `time` (UTC, ISO 8601), and `event`, plus the fields below.

| `event` | Fields |
|---|---|
| `run-start` | `run_id`, `workflow`, `mode`, `adapter`, `base_branch`, `run_branch`, `tickets` (the ids in plan order) |
| `step-start` | `ticket`, `stack`, `branch`, `worktree`, `base_commit`, `agent`, `tier`, `model` |
| `adapter-result` | `ticket`, `exit_status`, `end_state`, `session_id`, `event_stream` |
| `report-validation` | `ticket`, `valid`, `path`, `reason` (`null` when valid) |
| `gate-result` | `ticket`, `command`, `exit_status`, `green`, `output_tail` (the last 4000 characters) |
| `verify-start` | `ticket`, `agent`, `tier`, `model`, `commit` (the tip of the branch that the verifier sees), `copy` (the path of the temporary copy) |
| `verify-result` | `ticket`, `exit_status`, `end_state`, `session_id`, `event_stream` |
| `verify-report` | `ticket`, `valid`, `path`, `reason` (`null` when valid) |
| `verdict` | `ticket`, `mode`, `verdict`, `findings`, `unverified`, `report` (the path of the verdict report) |
| `run-branch-advance` | `ticket`, `run_branch`, `from_commit`, `to_commit` |
| `step-end` | `ticket`, `state` (`built` or `failed`), `reason` (`null` when built) |
| `run-end` | `result` (`built` or `failed`), `built`, `failed` (lists of ticket ids) |

## Limits of this version

- Every ticket branch starts from `base-branch`, not from the work of the tickets that block it. In `assure` mode, a ticket that follows an accepted ticket cannot fast-forward the run branch, and its step fails. The engine does not rebase the branch onto the run branch yet.
- The first failed step ends the run. No later ticket starts.
- The engine does not continue a capped or failed specialist, does not resume a run, and does not start a fix-up round after a REJECT.
- `economy` mode does not verify, and it does not move the run branch.
- A gate has no time limit.
