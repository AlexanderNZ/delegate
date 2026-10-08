# Reference: `delegate run`

`delegate run <workflow>` builds the tickets of a workflow. For each ticket, the engine makes a worktree, spawns the specialist of the stack through a harness adapter, checks the specialist report, runs the gates itself, and records each event in a journal. In `assure` mode, the engine then verifies the branch with a blind verifier, and moves the run branch to the branch only on ACCEPT. A REJECT starts a fix-up round, up to two rounds. A specialist that ends capped or failed, or whose gates are red, continues in the same worktree, up to the limit of the mode. A ticket that passes all of these is in the built state.

A workflow can hold many tickets. The engine takes them in dependency order. A failed step does not end the run: a ticket whose blocker failed is skipped, and the independent tickets still run.

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
5. For each ticket in dependency order, the engine does the step below, or skips the ticket. See [the skip rule](#the-skip-rule). A failed step does not end the run.
6. The engine writes `run-end`.

A step has these parts:

1. Make a worktree and the branch `<run-branch>-<ticket id>` from `base-branch`.
2. Spawn the specialist through the adapter. The prompt is the specialist brief.
3. Read the report from the report path and check it against the schema.
4. Check that the branch holds at least one commit beyond `base-branch`.
5. Run each gate of the stack in the worktree. The engine runs all gates, also after a red gate. A specialist that ends `failed` or `capped`, and a red gate, start a continuation. See [the continuation](#the-continuation).
6. In `assure` mode, rebase the branch onto the run branch, and run the gates again. See [the rebase](#the-rebase-onto-the-run-branch).
7. In `assure` mode, verify the branch. See [the verifier step](#the-verifier-step).

The engine does not trust the report for the gates. A gate that is red is recorded red when the report says `gates_green` is true.

The engine never merges to `base-branch`, never pushes, and never closes a ticket. A worktree and a branch stay after a failed step.

## The continuation

A specialist that ends `capped` or `failed`, or whose gates are red, continues in the same worktree. The commits that it made stay on the branch. The engine does not make a new worktree or a new branch.

- A specialist ends `capped` or `failed` when the adapter result has the end state `capped` or `failed`, or an exit status other than 0. The engine does not read the report of that run.
- A specialist whose gates are red has a valid report with the status `committed`, and at least one gate of the stack exits with a status other than 0.
- A report that is missing, is not valid, or has a status other than `committed` fails the step. It does not start a continuation.
- The continuation brief (`## Continuation`) holds the ticket id, the reason that the last run stopped, the commits on the branch so far, and, for a red gate, the output of the red gates. The specialist continues from these commits, and adds each change as a new commit.
- If the adapter has `supports_resume` set to true, and the last result has a `session_id`, the engine resumes that session. It sets `resume_session` in the request to the `session_id` of the last result, so each continuation resumes the newest session. The session keeps the context of the first brief, so the prompt holds only the continuation brief. The journal records the mode `resume`.
- Otherwise the engine starts a new agent, with `resume_session` set to `None`. A new agent has no context, so its prompt is the full specialist brief with the continuation section added. The journal records the mode `brief`. This is also the mode when the adapter supports resume but the harness gave no session id.
- The tier and the model are those of the first specialist run.
- The engine journals `continuation` before each continuation, with the count, the trigger, the commits, and the reason.

One count covers the three triggers. Each mode has a continuation limit:

| Mode | Limit |
|---|---|
| `assure` | 2 |
| `economy` | 1 |

When the specialist is not done after the limit, the step fails. The journal records `continuation-limit` with the count, and the reason of the step ends with `the continuation limit of <n> is reached`. The branch and the worktree stay, with all commits. The fix-up specialist of an `assure` round is not continued: its failure fails the step.

## The skip rule

A ticket is skipped when at least one of its blockers is not built. A blocker is not built when its step failed or when it was skipped, so a skip passes down the chain. The engine makes no worktree and no branch for a skipped ticket. The journal records `skip` with the blockers and the reason, for example `blocked by a, which failed`. The command prints one line `delegate run: ticket <id>: skipped: <reason>` on stderr. A ticket with no unbuilt blocker still runs, also after a failed step of another ticket.

## The rebase onto the run branch

In `assure` mode, the engine rebases the ticket branch onto the run branch before the verifier starts, and runs the gates again on the rebased tree. The first gate results have the phase `build`. The gate results after the rebase have the phase `rebase`. So the journal shows a gate result before and after the rebase for each ticket.

- The engine writes `rebase` with the run branch tip (`onto_commit`) and the branch tip before (`from_commit`) and after (`to_commit`) the rebase.
- A rebase that stops with a conflict fails the step. The engine aborts the rebase, so the branch keeps its commits and the worktree is clean. The journal records `rebase` with the result `conflict` and the conflicting files in `files`. The independent tickets go on.
- A rebase that fails for another cause fails the step. The result is `failed`, and the reason holds the message of git.
- A branch with no commit beyond the run branch after the rebase fails the step. The run branch holds its work already.
- A red gate after the rebase fails the step. No verifier starts.

The engine does not rebase in `economy` mode.

## The verifier step

In `assure` mode, the engine verifies each ticket branch that has green gates. In `economy` mode, the engine does not verify, and it does not move the run branch.

1. The engine makes a temporary copy of the branch. The copy is a clone with its own git directory and no remote. It holds the run branch and the ticket branch, and the ticket branch is checked out. The verifier can break the copy, and cannot reach the real repository through it. The engine removes the copy when the verifier ends.
2. The engine makes the verifier brief with the brief generator (`full_brief`). The brief holds the task (the ticket text), the diff `git diff <run-branch>...<ticket branch>`, the gates of the stack, the path of the copy, and the report path. The brief never holds a line of the specialist report.
3. The engine spawns the verifier of the stack through the adapter. The working directory is the copy. The tier is `verifier`, whatever the tier of the specialist is. The `verifier` entry of `tier-overrides` replaces it. The model comes from the tier column of the adapter.
4. The engine reads the verdict report and checks it against the schema.
5. On ACCEPT, the engine moves the run branch to the commit that the verifier saw. It moves the branch only by fast-forward. The rebase puts the run branch under the ticket branch, so a fast-forward is possible. If the run branch holds a commit that the ticket branch does not hold, the step fails and the run branch does not change.
6. On REJECT, the run branch does not change, and the engine starts a fix-up round. See [the fix-up round](#the-fix-up-round). After the last round, the step fails, the journal holds the findings, and the command prints them on stderr and exits 1.

The engine keeps the verdict report and the event stream of the verifier in `runs/<run id>/verifier/<ticket id>/`.

## The fix-up round

A REJECT in `assure` mode starts a fix-up round. The limit is 2 rounds for each ticket. A round has these parts:

1. The engine writes the findings of the rejected verdict to `runs/<run id>/findings/<ticket id>.fixup-<round>.md`, one finding on each line, with the prefix `- `.
2. The engine sends the specialist a fix-up brief in the same worktree. The brief holds the ticket, the findings verbatim (`## Findings to fix`), the rejected commit (`## Rejected commit`), the file boundary, the gates, and the report path. The tier and the model are those of the first specialist run.
3. The engine reads the report of the fix-up specialist and checks it, as for the first run. A specialist that ends `failed` or `capped`, a missing or invalid report, and a status other than `committed` fail the step.
4. The fix must be a new commit on top of the rejected commit. The engine asks the brief generator (`fixup_brief`) for the fix-up verifier brief. The generator refuses when the rejected commit is not an ancestor of the branch (the specialist amended or rewrote history), and when the branch tip is the rejected commit (the specialist added no commit). A refusal fails the step, and the journal records the reason in `fixup-refused`.
5. The engine runs the gates of the stack itself. A red gate fails the step.
6. The engine spawns a fresh verifier in a new temporary copy. The brief starts with `## Findings under verification`, so the verifier works in the mode `fix-up`. The brief holds the findings verbatim, the delta `git diff <rejected commit>..<ticket branch>` (not the full diff), the gates, the path of the copy, and the report path. It never holds a line of a specialist report. The verdict report must name the mode `fix-up`.
7. On ACCEPT, the engine moves the run branch to the tip that the verifier saw, by fast-forward only. On REJECT, the next round starts. Its rejected commit is the tip that the last verifier saw, so its delta holds only the newest fix-up.

Each round spawns a new verifier with a new session. The engine never resumes a verifier session. After the second round, a REJECT fails the step with the reason `the verifier rejected the branch after 2 fix-up rounds: <findings>`. The independent tickets go on.

## Where the engine writes

All files are in `<git common dir>/delegate/`. The working tree of the repository stays clean.

| Path | Content |
|---|---|
| `runs/<run id>/journal.jsonl` | The journal of the run. |
| `runs/<run id>/reports/<ticket id>.specialist.json` | The report path that the brief gives the specialist. |
| `runs/<run id>/findings/<ticket id>.fixup-<round>.md` | The findings that start a fix-up round. |
| `runs/<run id>/reports/<ticket id>.fixup-<round>.specialist.json` | The report path that the fix-up brief gives the specialist. |
| `runs/<run id>/verifier/<ticket id>/` | The verdict report (`verdict.json`) and the files that the verifier run wrote beside it. |
| `runs/<run id>/verifier/<ticket id>/fixup-<round>/` | The same files for the verifier of a fix-up round. |
| `worktrees/<run id>/<ticket id>/` | The worktree of the ticket. |

The run id is the UTC time and four hexadecimal characters, for example `20261008T093837Z-e9dc`.

## Output and exit codes

On stdout, the command prints `run <run id>` and `journal <path>`. On stderr, it prints one line `delegate run: ticket <id>: <reason>` for each failed step, and one line `delegate run: ticket <id>: skipped: <reason>` for each skipped ticket.

| Code | Meaning |
|---|---|
| 0 | Every ticket is built. In `assure` mode, the verifier accepted every ticket. |
| 1 | The workflow or the tier file is not valid, the run cannot start, a step failed, a ticket was skipped, or a verifier rejected a ticket. The message names the input. No traceback is shown. |
| 2 | A usage error. |

## Why a step fails

| Reason in the journal | Cause |
|---|---|
| `the specialist ended <state> with exit status <n>` | The adapter result is `failed` or `capped`, or the exit status is not 0. The engine does not read the report. |
| `report missing: ...` | The specialist wrote no file at the report path. |
| `report ... is not valid JSON`, `must be a JSON object`, `invalid: <field>: ...` | The report does not match the schema. The message names the field. |
| `the specialist reported status 'blocked'` (or `'partial'`) | The report is valid, but its `status` is not `committed`. |
| `branch ... holds no commit beyond ...` | The report says `committed`, but the branch has no new commit. |
| `gates red: <commands>` | At least one gate command exited with a status other than 0. After the rebase, the reason ends with `(after the rebase onto <run branch>)`. |
| `<reason>; the continuation limit of <n> is reached` | The specialist was continued `<n>` times, and it still ended `failed` or `capped`, or its gates were still red. `<reason>` is the reason of the last run. |
| `rebase onto <run branch> stopped with a conflict in <files>` | The rebase of the ticket branch onto the run branch had a conflict. The engine aborted the rebase. |
| `rebase onto <run branch> failed: ...` | The rebase failed with no conflict. The message holds the cause from git. |
| `branch ... holds no commit beyond <run branch> after the rebase; ...` | The run branch holds the work of the ticket already, so no commit is left. |
| `the verifier could not start: ...` | The engine could not make the copy or the verifier brief. The message holds the cause. |
| `the verifier ended <state> with exit status <n>` | The verifier result is `failed` or `capped`, or the exit status is not 0. The engine does not read the verdict report. |
| `report missing: ...`, `report ... is invalid: <field>: ...` | The verdict report does not match the schema. The message names the field. A report that names another mode than the mode of the run (`full`, or `fix-up` in a fix-up round) is invalid. |
| `the verifier rejected the branch after 2 fix-up rounds: <findings>` | The verdict is REJECT after the last fix-up round. |
| `the fix-up specialist ended <state> with exit status <n>` | The adapter result of the fix-up specialist is `failed` or `capped`, or the exit status is not 0. |
| `the fix-up specialist reported status 'blocked': ...`, and the report errors above | The report of the fix-up specialist is missing, is invalid, or has a status other than `committed`. |
| `the fix-up of round <n> is refused: ...` | The branch does not hold the rejected commit, or holds no commit beyond it. The message holds the cause from the brief generator. |
| `run branch <name> cannot fast-forward to <branch>: ...` | The verdict is ACCEPT, but the run branch holds a commit that the ticket branch does not hold. |

## The adapter interface

An adapter is a Python object. It registers by name with `agent_definitions.adapters.register(name, adapter)`. The workflow field `adapter` names it. The built-in names `claude-code`, `opencode`, and `cursor` are valid workflow values, but a run with one of them exits 1 until an implementation registers under that name.

The adapter has the attribute `supports_resume`. It is true when the harness can resume a session. See [the continuation](#the-continuation).

The adapter has the attribute `tier_column`, which names the column of the tier table with its models. The engine resolves the model from that column. The specialist tier is `strong` in `assure` mode and `standard` in `economy` mode. The `specialist` entry of `tier-overrides` replaces it. The verifier tier is `verifier` in every mode. The `verifier` entry of `tier-overrides` replaces it.

The adapter has the method `run(request)`.

| `request` field | Meaning |
|---|---|
| `agent` | The agent name, from the `specialist` field of the stack, or from the `verifier` field for the verifier. |
| `model` | The model from the tier column of the adapter. |
| `prompt` | The specialist brief, or the verifier brief. |
| `cwd` | The worktree path for the specialist. The path of the temporary copy for the verifier. |
| `report_path` | The path where the agent writes its report. |
| `resume_session` | The session id of the last run, when the engine resumes that session for a continuation. Otherwise `None`. The engine sets it only for an adapter with `supports_resume` set to true. |

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
| `mode` | string | The mode of the verifier. It must be `full` in the first pass and `fix-up` in a fix-up round. |
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
| `gate-result` | `ticket`, `round` (0 for the first build, else the fix-up round), `phase` (`build`, `rebase`, or `fixup`), `command`, `exit_status`, `green`, `output_tail` (the last 4000 characters) |
| `continuation` | `ticket`, `count` (1 for the first continuation), `limit`, `trigger` (`capped`, `failed`, or `gates-red`), `mode` (`resume` or `brief`), `resume_session` (the session id that the request carries, or `null`), `commits` (the commits on the branch so far, each as `<sha> <subject>`), `reason` |
| `continuation-limit` | `ticket`, `count` (the continuations made), `limit`, `trigger` (the cause of the last stop) |
| `skip` | `ticket`, `blockers` (the ids of the blockers that are not built), `reason` |
| `rebase` | `ticket`, `result` (`rebased`, `conflict`, or `failed`), `onto_commit` (the run branch tip), `from_commit`, `to_commit` (`null` when the rebase stopped), `files` (the conflicting files) |
| `verify-start` | `ticket`, `round`, `agent`, `tier`, `model`, `commit` (the tip of the branch that the verifier sees), `copy` (the path of the temporary copy) |
| `verify-result` | `ticket`, `round`, `exit_status`, `end_state`, `session_id`, `event_stream` |
| `verify-report` | `ticket`, `round`, `valid`, `path`, `reason` (`null` when valid) |
| `verdict` | `ticket`, `round`, `mode`, `verdict`, `findings`, `unverified`, `report` (the path of the verdict report) |
| `fixup-start` | `ticket`, `round` (1 or 2), `rejected_commit`, `findings_file`, `agent`, `tier`, `model` |
| `fixup-result` | `ticket`, `round`, `exit_status`, `end_state`, `session_id`, `event_stream` (the adapter result of the fix-up specialist) |
| `fixup-report` | `ticket`, `round`, `valid`, `path`, `reason` (`null` when valid) |
| `fixup-refused` | `ticket`, `round`, `reason` (the refusal of the brief generator) |
| `run-branch-advance` | `ticket`, `run_branch`, `from_commit`, `to_commit` |
| `step-end` | `ticket`, `state` (`built` or `failed`), `reason` (`null` when built) |
| `run-end` | `result` (`built` or `failed`), `built`, `failed`, `skipped` (lists of ticket ids) |

## Limits of this version

- Every ticket branch starts from `base-branch`, not from the work of the tickets that block it. In `assure` mode, the rebase puts the accepted work under the branch before the gates run again and before the verifier starts. The specialist itself does not see the work of its blockers.
- The engine does not resume a run.
- `economy` mode does not verify, and it does not move the run branch.
- A gate has no time limit.
