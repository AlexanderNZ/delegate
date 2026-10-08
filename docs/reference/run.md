# Reference: `delegate run`

`delegate run <workflow>` builds the tickets of a workflow. For each ticket, the engine makes a worktree, spawns the specialist of the stack through a harness adapter, checks the specialist report, runs the gates itself, and records each event in a journal. In `assure` mode, the engine then verifies the branch with a blind verifier, and moves the run branch to the branch only on ACCEPT. A REJECT starts a fix-up round, up to two rounds. In `economy` mode, the engine builds a chain of branches and verifies once for each stack at the end. See [the economy chain](#the-economy-chain) and [the chain verification](#the-chain-verification). A specialist that ends capped or failed, or whose gates are red, continues in the same worktree, up to the limit of the mode. A ticket that passes all of these is in the built state.

A coordinator can stop a run and go on later with `--resume <run-id>`. See [the resume](#the-resume). To see the state of a run, or to follow it, use `delegate status` and `delegate watch`. See [the reference of status and watch](status-and-watch.md).

A workflow can hold many tickets. The engine takes them in dependency order. A failed step does not end the run: a ticket whose blocker failed is skipped, and the independent tickets still run.

To check a workflow file without a build, use `--dry-run`. See [the workflow reference](workflow.md).

## Options

| Option | Meaning |
|---|---|
| `<workflow>` | The path of the workflow file. With `--resume`, it is optional: the default is the file that the run started from. |
| `--dry-run` | Validate the file and print the plan. Create nothing. |
| `--resume <run-id>` | Go on with the run `<run-id>`. See [the resume](#the-resume). It cannot go with `--dry-run`. |
| `--break-lock` | Remove the lock of the run branch when its process no longer exists. See [the run lock](#the-run-lock). |
| `--repo <dir>` | The git repository to build in. The default is the current directory. |
| `--tiers <file>` | The path of a tier file. The default is the bundled tier table. |

## What a run does

1. The engine validates the workflow. A problem exits 1 and creates nothing.
2. The engine checks that no other run holds the lock of the run branch. See [the run lock](#the-run-lock). A held lock exits 1 and creates nothing.
3. The engine checks that `base-branch` is a branch of the repository and that no ticket branch exists. A problem exits 1 and creates nothing.
4. The engine takes the lock.
5. The engine creates the run branch at `base-branch`, and writes `run-start` to a new journal.
6. For each ticket in dependency order, the engine does the step below, or skips the ticket. See [the skip rule](#the-skip-rule). A failed step does not end the run.
7. In `economy` mode, the engine verifies the chain. See [the chain verification](#the-chain-verification).
8. The engine writes `run-end`, and releases the lock.

A step has these parts:

1. Make a worktree and the branch `<run-branch>-<ticket id>` from `base-branch`, and install the push guard in it. See [the guards](#the-guards). In `economy` mode the branch starts from the tip of the previous ticket. See [the economy chain](#the-economy-chain).
2. Spawn the specialist through the adapter. The prompt is the specialist brief.
3. Read the report from the report path and check it against the schema.
4. Check that the branch holds at least one commit beyond the point where it started (`base-branch`, or the tip of the previous ticket in `economy` mode).
5. Compare the changed paths with the hotspot patterns of the stack. A match fails the step. See [the hotspot guard](#the-hotspot-guard).
6. Run each gate of the stack in the worktree. The engine runs all gates, also after a red gate. A specialist that ends `failed` or `capped`, and a red gate, start a continuation. See [the continuation](#the-continuation).
7. In `assure` mode, rebase the branch onto the run branch, and run the gates again. See [the rebase](#the-rebase-onto-the-run-branch).
8. In `assure` mode, verify the branch. See [the verifier step](#the-verifier-step).

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

## The resume

`delegate run --resume <run-id>` goes on with a run that stopped. A run stops when its process is killed, or is interrupted with Ctrl-C. Such a run has no `run-end` in its journal. The run id is on the line `run <run id>` that the command printed, and it is the name of the directory in `runs/`.

The engine rebuilds the state of the run from its journal, and starts at the first step that is not complete. It appends to the journal and never rewrites a line. It writes `resume` first, and `seq` goes on from the last line. The workflow must be the one that the run started from: `mode`, `adapter`, `base-branch`, `run-branch` and the ticket ids must be the same. If one differs, the command exits 1 and names the field. A run that has a `run-end` cannot be resumed.

The engine treats each ticket by what the journal holds for it:

| State in the journal | What the resume does |
|---|---|
| `step-end` (built, or failed) | The step is complete. The engine does not build it again, and it gives the adapter no invocation. |
| `skip` | The ticket stays skipped. |
| `run-branch-advance`, and no `step-end` | The run branch holds the commit already. The engine writes `step-end` with the state `built`, with no invocation. |
| `step-start`, and no `step-end` | The step is open. The engine uses its worktree and branch again. See below. |
| nothing | The ticket did not start. The engine builds it as in a new run. |

An open step goes on as follows:

- The worktree is used again as it is. A rebase that the stop left half done is aborted. If the worktree is gone, the engine makes it again on the same branch.
- If the journal shows a valid report after the last time the specialist was spawned, the engine does not spawn the specialist again. It runs the gates, and the step goes on from there.
- Otherwise the engine spawns the specialist in the same worktree. If the branch holds commits beyond `base-branch`, the prompt is a continuation brief with the reason `the run was interrupted`. If it holds none, the prompt is the first brief. A new agent has no context, so the engine never resumes a session across a stop.
- The continuations that the journal holds count against the limit of the mode.
- In `assure` mode, the verification starts again with a full pass. The first verifier of the resume has the round number equal to the number of verifier runs that the journal holds, and the limit of two fix-up rounds counts across the stop.

## The run lock

A lock prevents a second run on one run branch. A run takes the lock of its run branch before it changes anything, and releases it when it ends: when the run ends, when a step crashes, and when the process is interrupted with Ctrl-C. A resume takes the same lock.

- If another run holds the lock, and its process exists, the command exits 1. The message names the run branch, the run id, and the process id of the holder: `run branch <name> is in use by run <run id> (process <pid>)`. The run in progress is not changed. `--break-lock` does not remove the lock of a process that exists.
- A process that is killed cannot release its lock. The next run finds a lock whose process no longer exists. The command exits 1 and says so: `run branch <name> is locked by run <run id>, process <pid>, which no longer exists; pass --break-lock to remove the lock`. Nothing is changed.
- With `--break-lock`, the engine removes that lock, takes a new one, and goes on. It writes `lock-broken` with the run id and the process id of the old holder. The flag is for a lock of a process that no longer exists, so check that no other run uses the branch. A process id can be used again by an unrelated process. If so, the lock looks alive. Remove the lock file by hand.
- A lock file that cannot be read exits 1 and names the file. Remove it by hand when no run uses the branch.

The lock is a file for each run branch in `locks/`, and it holds the run id and the process id. The lock protects one machine: the state is in the git directory of the repository.

## A crash in a step

An exception that the engine did not plan for is a crash. Examples are an exception from the adapter, and a failure of git in the middle of a step. A crash leaves the state of the step unknown, so it ends the run. The engine does these things in order:

1. It writes `step-end` with the state `failed`. The `reason` is `crashed: <exception type>: <message>`.
2. It writes `skip` for each ticket that it did not reach. The `blockers` list is empty, and the reason is `the run ended after ticket <id> crashed`.
3. It writes `run-end` with the result `failed`.
4. The command prints one line `delegate run: ticket <id>: crashed: ...` on stderr, and exits 1.

The branch and the worktree of the ticket stay as the crash left them.

## The skip rule

A ticket is skipped when at least one of its blockers is not built. A blocker is not built when its step failed or when it was skipped, so a skip passes down the chain. The engine makes no worktree and no branch for a skipped ticket. The journal records `skip` with the blockers and the reason, for example `blocked by a, which failed`. The command prints one line `delegate run: ticket <id>: skipped: <reason>` on stderr. A ticket with no unbuilt blocker still runs, also after a failed step of another ticket.

## The guards

The guards use git only, so they hold in every harness, also in a headless run that skips the hooks of the harness.

### The push guard

The engine installs the push guard in each worktree that it makes, also in a worktree that a resume uses again.

- The engine sets `extensions.worktreeConfig` in the repository, and sets `core.hooksPath` in the configuration of that one worktree. The main checkout and the other worktrees keep their own hooks.
- The directory that `core.hooksPath` names is `hooks/<run id>/<ticket id>/` in the state directory. It holds a `pre-push` hook that prints `delegate: push refused` on stderr and exits 1, so every push from the worktree fails.
- The directory also holds a wrapper for each other hook of the repository (or of the host, when the host sets `core.hooksPath`). The wrapper runs the original hook with the same arguments and the same input, so those hooks still run for the commits of the specialist.
- The guard does not stop a push with the `--no-verify` option, because git skips every `pre-push` hook then. It also does not stop a push from a clone or from another directory. It holds against the plain push command of a specialist in the worktree.

### The hotspot guard

After the specialist reports `committed` and the branch holds a commit, and before the gates run, the engine compares the paths that the branch changed since the merge base with `base-branch` with the `hotspots` of the stack. After a fix-up specialist, the engine compares the paths that changed since the rejected commit. The engine does this in every mode.

- A changed path is a path that is added, changed, or deleted. A rename counts as its old path and its new path.
- A pattern that ends with `/` names a directory. It matches every path below the directory.
- Any other pattern is matched against the whole path with `fnmatch` rules: `*` matches any characters, also `/`. A pattern that names a directory also matches every path below it. `LICENSE` matches `LICENSE` and does not match `LICENSE.md` or `docs/LICENSE`.
- A match fails the step at once. The engine writes `hotspot-finding` and the step ends with a reason that names each path: `hotspot finding: <path> matches the hotspot '<pattern>'`. No gate runs, no continuation starts, and no verifier starts. The branch and the worktree stay as they are, and the run branch does not move.
- A change outside the hotspots goes on to the gates and the verification.

### The worktree invariant

The verifier works in a temporary copy. It must not change the real worktree of the ticket, which is the worktree where the specialist built the branch. The engine checks this in every harness, also in one with no command guard.

- Before each verifier run, the engine takes a snapshot of the worktree of the ticket: the HEAD commit, and for each path that `git status` reports, the status code and a hash of the content of the file. A file that git ignores is not in the snapshot. The engine takes the snapshot again when the verifier ends.
- A difference is an invariant violation. A verifier that commits, that adds or deletes a file, that edits a tracked file, or that rewrites a file which was already changed or untracked, makes the snapshots differ. The engine does this check for the first verifier of a ticket and for the verifier of each fix-up round.
- A violation halts the run. The engine writes `verify-result`, then `invariant-violation` with the worktree, the HEAD commit before and after, and one entry in `changes` for each path that differs. The engine writes `step-end` with the state `failed` and the reason `invariant violation: ...`. It writes `skip` for each ticket that it did not reach, with the reason `the run ended after ticket <id> halted the run`. It writes `run-end` with the result `failed`. The command prints one line `delegate run: ticket <id>: invariant violation: ...` on stderr, and exits 1.
- The verdict does not count. The run branch does not move, also when the report on disk says ACCEPT. The branch and the worktree stay as the verifier left them. A run that a violation halted cannot be resumed.
- A verifier that writes only in its temporary copy does not halt the run. The engine removes the copy after each verifier run, also when the run halts.
- The check covers the worktree of the ticket. It does not cover the main checkout of the repository, which the coordinator can change at any time, and it does not cover a path outside the repository.

## The rebase onto the run branch

In `assure` mode, the engine rebases the ticket branch onto the run branch before the verifier starts, and runs the gates again on the rebased tree. The first gate results have the phase `build`. The gate results after the rebase have the phase `rebase`. So the journal shows a gate result before and after the rebase for each ticket.

- The engine writes `rebase` with the run branch tip (`onto_commit`) and the branch tip before (`from_commit`) and after (`to_commit`) the rebase.
- A rebase that stops with a conflict fails the step. The engine aborts the rebase, so the branch keeps its commits and the worktree is clean. The journal records `rebase` with the result `conflict` and the conflicting files in `files`. The independent tickets go on.
- A rebase that fails for another cause fails the step. The result is `failed`, and the reason holds the message of git.
- A branch with no commit beyond the run branch after the rebase fails the step. The run branch holds its work already.
- A red gate after the rebase fails the step. No verifier starts.

The engine does not rebase in `economy` mode.

## The economy chain

In `economy` mode, the tickets form a chain. Each ticket branch starts from the tip of the previous ticket, so a dependent ticket sees the work it depends on.

- The previous ticket is the last ticket before it, in the order of the plan, that was built. A ticket that failed or was skipped is not part of the chain, and its branch is not a start point.
- The first ticket starts from `base-branch`.
- `step-start` records the start commit in `base_commit`. The commits of a ticket are the commits beyond `base_commit`. The check for a commit, the hotspot guard, and the continuation brief all use this point, so the commits of earlier tickets in the chain do not count for a ticket.
- The specialist runs on the tier `standard`. The `specialist` entry of `tier-overrides` replaces it.
- The engine does not rebase in `economy` mode.

## The chain verification

In `economy` mode, the engine verifies once for each stack, after the last ticket of the chain. The engine does not verify a ticket by itself, and it does not move the run branch before the end.

1. The chain holds the tickets that were built. The stacks are the stacks of these tickets, in the order of their first ticket.
2. For each stack, the engine spawns one verifier. The verifier tier is `verifier`, as in `assure` mode, whatever the tier of the specialists is. The verifier works in a temporary copy of the chain tip. The brief generator makes the brief (`chain_brief`). It holds the text of each ticket of the stack and, for each of them, the diff of the commits that the ticket added (`git diff <base>...<tip>`). It holds no diff of a ticket of another stack. It also tells the verifier to start each finding with the id of its ticket in square brackets, for example `[b] The header row is missing.`
3. On ACCEPT, the next stack is verified.
4. On REJECT, the stack gets one fix-up round. The limit is 1 round in `economy` mode. The fix-up is a new commit on the chain tip. It runs in the worktree of the last ticket of the chain, with the specialist of the stack and the findings verbatim. The engine runs the gates of the stack. A fresh verifier then gets the scoped brief: the findings, and the delta from the rejected commit. The steps of [the fix-up round](#the-fix-up-round) apply.
5. A second REJECT fails the stack, and the run. The engine does not verify the stacks after it.
6. When every stack is accepted, the engine moves the run branch to the chain tip by fast-forward, and writes `run-branch-advance`. The run branch moves only then.

The events of a stack carry the id of the last ticket of the stack in `ticket`. `verify-start` and `verdict` also carry `stack` and `tickets` (the ids that the verifier saw).

### The finding map

A finding maps to a ticket when it starts with `[<ticket id>]` and the id is a ticket of the stack. The `verdict` event holds `mapped` (a map from a ticket id to its findings, without the label) and `unmapped` (the findings that map to no ticket, unchanged). A finding with an unknown label, or with no label, is unmapped. The engine never drops it: the journal holds it, and the command prints `delegate run: unmapped finding: <finding>` on stderr when the stack fails.

When a stack fails after the fix-up round:

- Each ticket that a finding of the last verdict names fails. The reason is `the verifier rejected the chain after 1 fix-up round: <its findings>`.
- If no finding names a ticket, every ticket of the stack fails, with the reason `the verifier rejected the branch after 1 fix-up round: <all findings>`.
- The tickets of the stacks that were not verified fail with the reason `not verified: the chain ended when the verification of the stack '<name>' failed`.
- The engine writes a new `step-end` with the state `failed` for each of them. The newest `step-end` of a ticket wins, in `status` and in a resume.
- The run branch does not move.

A ticket that failed in the build is not in the chain. The engine still verifies the tickets that were built. If they are all accepted, the run branch moves to the chain tip, and the run ends `failed` because of the ticket that failed.

A resume that stops before the end of the chain verifies the stacks again. A stack with an ACCEPT verdict in the journal is not verified again. The verifier runs of the stack in the journal count against the fix-up limit.

## The verifier step

In `assure` mode, the engine verifies each ticket branch that has green gates. In `economy` mode, the engine verifies the chain at the end instead. See [the chain verification](#the-chain-verification). The steps below describe one ticket branch; the chain verification uses the same temporary copy, the same invariant check, and the same verdict report.

1. The engine makes a temporary copy of the branch. The copy is a clone with its own git directory and no remote. It holds the run branch and the ticket branch, and the ticket branch is checked out. The verifier can break the copy, and cannot reach the real repository through it. The engine makes the copy also when the HEAD of the repository is on the run branch. The engine removes the copy when the verifier ends.
2. The engine makes the verifier brief with the brief generator (`full_brief`). The brief holds the task (the ticket text), the diff `git diff <run-branch>...<ticket branch>`, the gates of the stack, the path of the copy, and the report path. The brief never holds a line of the specialist report.
3. The engine spawns the verifier of the stack through the adapter. The working directory is the copy. The tier is `verifier`, whatever the tier of the specialist is. The `verifier` entry of `tier-overrides` replaces it. The model comes from the tier column of the adapter.
4. The engine checks that the verifier left the real worktree as it was. See [the worktree invariant](#the-worktree-invariant). A difference halts the run before the engine reads the verdict.
5. The engine reads the verdict report and checks it against the schema.
6. On ACCEPT, the engine moves the run branch to the commit that the verifier saw. It moves the branch only by fast-forward. The rebase puts the run branch under the ticket branch, so a fast-forward is possible. If the run branch holds a commit that the ticket branch does not hold, the step fails and the run branch does not change.
7. On REJECT, the run branch does not change, and the engine starts a fix-up round. See [the fix-up round](#the-fix-up-round). After the last round, the step fails, the journal holds the findings, and the command prints them on stderr and exits 1.

The engine keeps the verdict report and the event stream of the verifier in `runs/<run id>/verifier/<ticket id>/`.

## The fix-up round

A REJECT starts a fix-up round. The limit is 2 rounds for each ticket in `assure` mode, and 1 round for each stack in `economy` mode. A round has these parts:

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
| `hooks/<run id>/<ticket id>/` | The hooks directory of the worktree. See [the push guard](#the-push-guard). |
| `locks/<run branch>.lock` | The lock of a run branch while a run holds it. The branch name is percent-encoded. |

The run id is the UTC time and four hexadecimal characters, for example `20261008T093837Z-e9dc`.

## Output and exit codes

On stdout, the command prints `run <run id>` and `journal <path>`. On stderr, it prints one line `delegate run: ticket <id>: <reason>` for each failed step, and one line `delegate run: ticket <id>: skipped: <reason>` for each skipped ticket.

| Code | Meaning |
|---|---|
| 0 | Every ticket is built. In `assure` mode, the verifier accepted every ticket. In `economy` mode, the verifier accepted every stack of the chain. |
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
| `hotspot finding: <path> matches the hotspot '<pattern>'` | A changed path matches a hotspot pattern. See [the hotspot guard](#the-hotspot-guard). |
| `gates red: <commands>` | At least one gate command exited with a status other than 0. After the rebase, the reason ends with `(after the rebase onto <run branch>)`. |
| `crashed: <exception type>: <message>` | An exception from the adapter or from git ended the step. See [a crash in a step](#a-crash-in-a-step). |
| `<reason>; the continuation limit of <n> is reached` | The specialist was continued `<n>` times, and it still ended `failed` or `capped`, or its gates were still red. `<reason>` is the reason of the last run. |
| `rebase onto <run branch> stopped with a conflict in <files>` | The rebase of the ticket branch onto the run branch had a conflict. The engine aborted the rebase. |
| `rebase onto <run branch> failed: ...` | The rebase failed with no conflict. The message holds the cause from git. |
| `branch ... holds no commit beyond <run branch> after the rebase; ...` | The run branch holds the work of the ticket already, so no commit is left. |
| `the verifier could not start: ...` | The engine could not make the copy or the verifier brief. The message holds the cause. |
| `invariant violation: the verifier changed the real worktree <path>: ...` | The worktree of the ticket differs after the verifier run. The run halts. See [the worktree invariant](#the-worktree-invariant). |
| `the verifier ended <state> with exit status <n>` | The verifier result is `failed` or `capped`, or the exit status is not 0. The engine does not read the verdict report. |
| `report missing: ...`, `report ... is invalid: <field>: ...` | The verdict report does not match the schema. The message names the field. A report that names another mode than the mode of the run (`full`, or `fix-up` in a fix-up round) is invalid. |
| `the verifier rejected the branch after 2 fix-up rounds: <findings>` | The verdict is REJECT after the last fix-up round. In `economy` mode, the text is `after 1 fix-up round`. |
| `the verifier rejected the chain after 1 fix-up round: <findings>` | `economy` mode. The findings of the last verdict name this ticket. See [the finding map](#the-finding-map). |
| `not verified: the chain ended when the verification of the stack '<name>' failed` | `economy` mode. The ticket is in a stack after a stack that failed. |
| `the fix-up specialist ended <state> with exit status <n>` | The adapter result of the fix-up specialist is `failed` or `capped`, or the exit status is not 0. |
| `the fix-up specialist reported status 'blocked': ...`, and the report errors above | The report of the fix-up specialist is missing, is invalid, or has a status other than `committed`. |
| `the fix-up of round <n> is refused: ...` | The branch does not hold the rejected commit, or holds no commit beyond it. The message holds the cause from the brief generator. |
| `run branch <name> cannot fast-forward to <branch>: ...` | The verdict is ACCEPT, but the run branch holds a commit that the ticket branch does not hold. |

## The adapter interface

An adapter is a Python object. It registers by name with `agent_definitions.adapters.register(name, adapter)`. The workflow field `adapter` names it. The built-in names `claude-code`, `opencode`, and `cursor` are valid workflow values, but a run with one of them exits 1 until an implementation registers under that name.

The adapter has the attribute `supports_resume`. It is true when the harness can resume a session. See [the continuation](#the-continuation).

The adapter has the attribute `tier_column`, which names the column of the tier table with its models. The engine resolves the model from that column. The specialist tier is `strong` in `assure` mode and `standard` in `economy` mode. The `specialist` entry of `tier-overrides` replaces it. The verifier tier is `verifier` in every mode. The `verifier` entry of `tier-overrides` replaces it, but it cannot name a weaker tier: the workflow is refused when it loads. See [the workflow reference](workflow.md#tier-overrides).

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
| `report-validation` | `ticket`, `valid`, `path`, `reason` (`null` when valid), `status` (the `status` of the report, `null` when not valid), `blocked_reason` (the `blocked_reason` of the report, or `null`) |
| `hotspot-finding` | `ticket`, `phase` (`build` or `fixup`), `round` (0 for the first build, else the fix-up round), `matches` (a list of objects, each with the changed `path` and the `pattern` it matches) |
| `gate-result` | `ticket`, `round` (0 for the first build, else the fix-up round), `phase` (`build`, `rebase`, or `fixup`), `command`, `exit_status`, `green`, `output_tail` (the last 4000 characters) |
| `continuation` | `ticket`, `count` (1 for the first continuation), `limit`, `trigger` (`capped`, `failed`, or `gates-red`), `mode` (`resume` or `brief`), `resume_session` (the session id that the request carries, or `null`), `commits` (the commits on the branch so far, each as `<sha> <subject>`), `reason` |
| `continuation-limit` | `ticket`, `count` (the continuations made), `limit`, `trigger` (the cause of the last stop) |
| `resume` | `run_id`, `built` (the tickets that are built), `failed`, `skipped` (the tickets that are complete in the journal), `open` (the ticket whose step was open, or `null`) |
| `lock-broken` | `run_id`, `pid` (the run id and the process id of the old holder, whose lock `--break-lock` removed) |
| `skip` | `ticket`, `blockers` (the ids of the blockers that are not built; empty for a ticket that a crash left unreached), `reason` |
| `rebase` | `ticket`, `result` (`rebased`, `conflict`, or `failed`), `onto_commit` (the run branch tip), `from_commit`, `to_commit` (`null` when the rebase stopped), `files` (the conflicting files) |
| `verify-start` | `ticket`, `round`, `agent`, `tier`, `model`, `commit` (the tip of the branch that the verifier sees), `copy` (the path of the temporary copy). In a chain verification also `stack` and `tickets`. |
| `verify-result` | `ticket`, `round`, `exit_status`, `end_state`, `session_id`, `event_stream` |
| `invariant-violation` | `ticket`, `round`, `worktree` (the real worktree of the ticket), `head_before`, `head_after` (its HEAD commit before and after the verifier run), `changes` (a list of strings, one for each path that differs) |
| `verify-report` | `ticket`, `round`, `valid`, `path`, `reason` (`null` when valid) |
| `verdict` | `ticket`, `round`, `mode`, `verdict`, `findings`, `unverified`, `report` (the path of the verdict report). In a chain verification also `stack`, `tickets`, `mapped` (a map from a ticket id to its findings), and `unmapped` (the findings that map to no ticket). |
| `fixup-start` | `ticket`, `round` (1 or 2; 1 in `economy` mode), `rejected_commit`, `findings_file`, `agent`, `tier`, `model` |
| `fixup-result` | `ticket`, `round`, `exit_status`, `end_state`, `session_id`, `event_stream` (the adapter result of the fix-up specialist) |
| `fixup-report` | `ticket`, `round`, `valid`, `path`, `reason` (`null` when valid), `status`, `blocked_reason` (as in `report-validation`) |
| `fixup-refused` | `ticket`, `round`, `reason` (the refusal of the brief generator) |
| `run-branch-advance` | `ticket`, `run_branch`, `from_commit`, `to_commit` |
| `step-end` | `ticket`, `state` (`built` or `failed`), `reason` (`null` when built). In `economy` mode a ticket can have a second `step-end` with the state `failed`, from the chain verification. The newest one wins. |
| `run-end` | `result` (`built` or `failed`), `built`, `failed`, `skipped` (lists of ticket ids) |

## Limits of this version

- In `assure` mode, every ticket branch starts from `base-branch`, not from the work of the tickets that block it. The rebase puts the accepted work under the branch before the gates run again and before the verifier starts. The specialist itself does not see the work of its blockers.
- The engine can resume a run only while the journal has no `run-end`. A run that a crash ended cannot be resumed: start a new run.
- If a process is killed between the move of the run branch and the write of `run-branch-advance`, the resume fails that step with the message `holds no commit beyond ... after the rebase`. The run branch holds the work of the ticket. Check it by hand.
- In `economy` mode, a defect shows only at the end of the chain. The stack of a rejected chain fails as a whole when no finding names a ticket.
- In `economy` mode, a fix-up commit goes on the chain tip. A stack that the engine verified before it does not see that commit. The verifier of the fix-up sees only the delta.
- A gate has no time limit.
