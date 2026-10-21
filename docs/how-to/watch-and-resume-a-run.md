# How to watch and resume a run

Use this page to follow a [**run**](../glossary.md#run), to [**pause**](../glossary.md#pause) it, to change its plan, to go on with a run that stopped, and to clean up after a run. The state of a run is in its [**journal**](../glossary.md#journal). A stop loses no finished work.

## Before you start

You have a [**workflow**](../glossary.md#workflow) file and the [**agent pair**](../glossary.md#agent-pair) for each [**stack**](../glossary.md#stack). See [how to bootstrap a single-stack repository](bootstrap-a-single-stack-repository.md). This page uses this workflow with two [**tickets**](../glossary.md#ticket).

```toml title="workflow.toml"
base-branch = "main"
run-branch = "run/demo"
mode = "assure"
adapter = "claude-code"

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = ["python3 -m unittest"]
hotspots = ["workflow.toml"]

[[tickets]]
id = "1"
text = "The export command reads the data set."
stack = "python"
blocked-by = []

[[tickets]]
id = "2"
text = "The export command writes a header row."
stack = "python"
blocked-by = ["1"]
```

## Watch a run

### 1. Start the run

Start the run in one terminal.

```bash
delegate run workflow.toml
```

When the run starts, the command prints three lines: `run <run id>`, `journal <path>`, and `follow it with delegate watch · pause it with delegate pause <short id>`. Keep the run id. The short id is the part of the run id after the last `-`. `status`, `watch`, `pause` and `--resume` take the run id or the short id. When the run ends, the command prints a summary line, for example `run built · 5s · 2 built · 0 failed · 0 skipped · 0 new · 0 cache`.

### 2. See the state

In a second terminal, in the same repository, read the state of the run. With no run id, `status` and `watch` read the one [**active run**](../glossary.md#active-run).

```bash
delegate status
delegate watch --stall-minutes 30 --max-minutes 50
```

`status` prints the state of the run once, and exits. It prints the progress of the run: the percentage, the tickets that ended, the time used, the ETA, and the tokens as two counts. The two counts are the new tokens and the cache reads. Then it prints the ticket table, with one flat row for each ticket in plan order. A rail `│` joins a row to the row above when that row is its [**blocker**](../glossary.md#blocker). The `after` column names a blocker that is not the row above. A row also gives the state, the phase, the [**verdict**](../glossary.md#verdict), the branch, the time used and the tokens. Then it gives the time of the last event, and the reason of a ticket that failed or was skipped. Each further phase of a running ticket is on a sub-row. `status` prints one snapshot, so it gives no time since the last activity of an agent: `delegate status --json` gives it. See [the status reference](../reference/status-and-watch.md#delegate-status) for the columns. A program can read the same snapshot with `delegate status --json`. See [the snapshot](../reference/status-and-watch.md#the-snapshot).

`watch` follows the run until it ends. In a terminal, it shows the live view. At the top, the run line gives the same progress as `status`: the percentage, the time used, the ETA, and the tokens. Under it, the ticket table has one flat row for each ticket in plan order, with the rail and the `after` column of `status`. A long chain of blockers does not make the table wider. A row gives the state, the first phase, the time used, the tokens, and the [liveness](../reference/status-and-watch.md#the-liveness-of-an-agent) of its agent: a spinner while the agent is active, `quiet <time>` after 30 seconds with no activity, and `⚠ idle <time>` after the stall time. Each further phase of the running ticket is a sub-row under it. Below a divider, the feed shows one short line for each event of the ticket that runs now: the local time, the ticket, and what happened. For example, `10:51  #64  specialist finished · 36m · tokens 302k new, 9.7M cache · report ok`. A problem and a stall are lines that start with `✗`. `watch` draws the frame again at each poll. See [the live view](../reference/status-and-watch.md#the-live-view).

The option `--stall-minutes` makes `watch` exit when nothing changes for 30 minutes. The option `--max-minutes` makes it exit before a [**harness**](../glossary.md#harness) time limit. The journal names the [**event stream**](../glossary.md#event-stream) of each agent when the agent starts, so an agent that writes its stream is not a stall. The journal also names the log file of each [**gate**](../glossary.md#gate) when the gate starts, so a long gate that writes output is not a stall.

When standard output is not a terminal, `watch` prints one raw line for each event, for a script or a harness. To see the raw lines in a terminal, add `--raw`:

```bash
delegate watch --raw
```

See [the raw lines](../reference/status-and-watch.md#the-raw-lines).

With no run id, `watch` follows the one active run, also when a newer run ended. A run is active when its journal has no `run-end` and a live process holds the lock of its [**run branch**](../glossary.md#run-branch). When more than one run is active, `watch` on a terminal shows a table of the active runs, the newest run first. Then it asks which run to follow: type its number, or push Enter for the newest run. When standard input or standard output is not a terminal, `watch` follows none: it prints one line on stderr that names each active run, and exits 2. See [the run with no run id](../reference/status-and-watch.md#the-run-with-no-run-id) and [the run picker](../reference/status-and-watch.md#the-run-picker).

When no run is active and the newest run has ended, `watch` with no run id shows none of its events. It prints one line with the result of that run, and exits. When the newest run stopped with no `run-end`, the line tells how to resume it. To see the events of a run that ended, give its run id:

```bash
delegate watch <run-id> --raw
```

See [the newest run that ended](../reference/status-and-watch.md#the-newest-run-that-ended).

### 3. Act on the exit code

The exit code of `watch` tells you why it stopped. For example, the run succeeded, failed or paused, a [**step**](../glossary.md#step) has a problem, or a limit is reached. See [the exit codes of `watch`](../reference/status-and-watch.md#exit-codes) for each code.

You can run `watch` as a background task in your harness. The harness then alerts you when the command exits.

### 4. Go on after a limit

A `watch` that follows a journal prints a line `position <seq>` before it exits. After Ctrl-C, it prints the position in a line on stderr, and exits 130. To go on from that point, give the position. No event is printed twice.

```bash
delegate watch --from <position> --stall-minutes 30
```

With no run id, `watch --from` chooses the run as `watch` with no run id does: the one active run, also when a newer run ended, or the run that you choose when more than one run is active. When no run is active, it goes on in the newest run. To go on in a specific run, give its run id before `--from`, as the line of Ctrl-C does. See [the run with no run id](../reference/status-and-watch.md#the-run-with-no-run-id).

## Pause a run

A pause stops a run at its next [**step**](../glossary.md#step) boundary. It cuts off no agent, so no work in progress is lost.

### 1. Ask for the pause

In a second terminal, in the same repository, give the run id or the short id.

```bash
delegate pause <run-id>
```

The command prints `run <run id>: pause requested; the run stops at its next step boundary` and the path of the request. The run stops before the next step, gate or [**verifier**](../glossary.md#verifier) starts. `delegate status` shows `pausing` until the run stops, and then `paused`. `delegate run` then prints the summary and the line below, and exits 3. `watch` exits 7.

```text
run paused · 1s · 0 built · 0 failed · 0 skipped · 0 new · 0 cache
paused before the gate `python3 -m unittest` of ticket 1; go on with delegate run --resume <run id>
```

A run that stops before it reads the request does not show `pausing`. For example, Ctrl-C stops it. `delegate status` then shows a note that a pause request waits. A run with no live process cannot be paused: `delegate pause` exits 1. See [the pause](../reference/run.md#the-pause).

### 2. Go on after the pause

Resume the paused run with the same command as after Ctrl-C. See [resume after Ctrl-C](#1-resume-after-ctrl-c). Before you resume, you can add a ticket, or change a ticket that has not started. See [change the plan of a run](#change-the-plan-of-a-run).

## Change the plan of a run

A [**resume**](../glossary.md#resume) can add a ticket to a run. It can also change or remove a ticket that has not started. The built part of the run stays as it is. See [the plan change](../reference/run.md#the-plan-change) for each rule.

### 1. Stop the run

Pause the run, as in [pause a run](#pause-a-run). You can also change a run that stopped, or a run that ended.

### 2. Edit the workflow

Edit the workflow file that the run started from. In this example, ticket `1` started before the pause. The edit adds ticket `3`, and ticket `2` now waits for it.

```toml
[[tickets]]
id = "2"
text = "The export command writes a header row."
stack = "python"
blocked-by = ["1", "3"]

[[tickets]]
id = "3"
text = "The export command reads the options file."
stack = "python"
blocked-by = ["1"]
```

A new ticket goes at the end, or before a ticket that has not started. The `blocked-by` lists set the new order. Do not change or remove a ticket that has started. The resume refuses such a change, and names the ticket and the field.

### 3. Check the change

```sh
delegate run --resume <run-id> --dry-run
```

The dry run changes nothing. It prints the old order, the new order, the verdict of each rule, and the text of each added or changed ticket.

```text
run <run id>: plan change
old order: 1, 2
new order: 1, 3, 2
rule 1: ok: the built part is fixed: the built part is 1
rule 2: ok: a new ticket goes after the built part: added: 3
rule 3: ok: a ticket that has not started can change: changed: 2 (blocked-by)
rule 4: ok: no built work is changed: no new ticket comes before a ticket that has started
rule 5: ok: a new ticket gets its own gates and verifier: each new ticket gets its own gates and verifier
rule 6: ok: the journal records the change: the resume writes plan-change
rule 7: ok: a ticket that has started cannot be removed: removed: none
rule 8: refused: the coordinator reviews the later tickets: needs --reviewed for 2
rule 9: ok: the brief of each later ticket gets the section Plan change: the briefs of 2 get the section
later tickets to review, in plan order: 2
ticket 3 (added):
  The export command reads the options file.
ticket 2 (changed):
  The export command writes a header row.
```

A later ticket has not started, and comes after an added or changed ticket. Here ticket `2` is a later ticket. Without `--reviewed`, the dry run and the resume exit 1, and change nothing. The message on stderr names the tickets to review:

```text
delegate run: the plan change needs a review of the later tickets '2': read each one against the added and changed tickets, change its text or its blocked-by where a precondition changed, then give --reviewed 2 or --reviewed all
```

### 4. Review the later tickets

Read each later ticket against the added and changed tickets. Change its text or its `blocked-by` when a precondition changed. Then run the dry run again with `--reviewed 2`. It exits 0 when each rule accepts the change.

### 5. Resume the run

```sh
delegate run --resume <run-id> --reviewed 2
```

`--reviewed` takes the ids of the later tickets, separated by commas, or `all`. The journal records `plan-change`. The [**brief**](../glossary.md#brief) of each later ticket gets the section [**`Plan change`**](../glossary.md#plan-change). Its [**specialist**](../glossary.md#specialist) reports `blocked` when the ticket no longer fits the new plan.

## Resume a run

A run stops when you press Ctrl-C, or when its process is killed. Such a run has no `run-end` in its journal, and `delegate status` shows the result `running`. After Ctrl-C, `delegate run` writes `interrupted` in the journal, prints the command that goes on with the run, and exits 130. See [Ctrl-C](../reference/run.md#ctrl-c). A paused run has no `run-end` too, and `delegate status` shows `paused`.

### 1. Resume after Ctrl-C

Give the run id. The workflow file is the one that the run started from. The [**engine**](../glossary.md#engine) rebuilds the state from the journal and starts at the first step that is not complete. It never builds a finished step again.

```bash
delegate run --resume <run-id>
```

A ticket with a recorded commit is not built again. A step that was open continues in its own [**worktree**](../glossary.md#worktree). See [the resume reference](../reference/run.md#the-resume) for each case.

### 2. Resume after a kill

A process that is killed cannot release the lock of its run branch. Resume the run with the same command as after Ctrl-C. The lock names the same run id and a process that no longer exists, so the resume takes it over and needs no flag. When the run ends, the command prints a note on stderr that names the old process.

A lock whose process still exists stays. A lock of another run id, whose process no longer exists, needs `--break-lock`. First check that no other run uses the run branch. See [the run lock](../reference/run.md#the-run-lock).

A run that the [**invariant**](../glossary.md#invariant) check halted cannot be resumed, also with `--retry`: start a new run. A run that has a `run-end` can be resumed only with `--retry`, or with a change of its tickets. See [the plan change](../reference/run.md#the-plan-change).

## Clean up after a run

A run that ends `built` removes the [**worktree**](../glossary.md#worktree) of each ticket, and deletes each ticket branch that the run branch holds. The run branch stays, because you merge it. A run that ended before that clean-up existed, or with `keep-worktrees = true`, keeps its worktrees and its ticket branches. `delegate clean` removes them. See [`delegate clean`](../reference/commands.md#delegate-clean).

### 1. Merge the run branch

Merge the run branch into the [**base branch**](../glossary.md#base-branch), as the merge steps of the `agent-delegation` [**skill**](../glossary.md#skill) tell.

### 2. See what the command removes

```bash
delegate clean --ended --merged --dry-run
```

The command changes nothing. It prints one line for each run: the worktrees and the branches that it would remove, each item that it would keep with the reason, and the space that it would free. `--ended` selects each run that ended `built`. `--merged` adds the run branch of such a run when the base branch holds it. A run that is active, failed, paused or stopped keeps everything, and its line gives the reason. To clean one run, give its run id in place of `--ended`.

### 3. Remove it

```bash
delegate clean --ended --merged
```

The command removes what the dry run named, and records a `cleanup` event in the [**journal**](../glossary.md#journal) of each run where something went. A worktree with a change that is not committed stays, and so does its branch. A file that a gate makes, for example in `__pycache__`, is such a change when `.gitignore` does not name it. The command exits 0 when it removed or kept each item by these rules, and 1 on an error.

## Report a run as a coordinator agent

A [**coordinator**](../glossary.md#coordinator) agent can start a run for a person. The person cannot see the agents, so the coordinator reports the run. This example starts the run detached from the tool shell.

```sh
delegate run --detach workflow.toml
```

The command first checks the workflow, the [**tier file**](../glossary.md#tier-file), the lock and the branches. A check that fails exits 1 and starts no process. Then the command prints `run <run id>` and `log <path>`, and exits at once. The run goes on in its own process, and writes its output to the log file of the run. See [the detached run](../reference/run.md#the-detached-run). `delegate status` and `delegate watch` with no run id read the one active run, or the newest run when no run is active. With more than one active run, `status` prints a table of the active runs. With no terminal, `watch` prints one line that names each active run, and exits 2: give the run id. The coordinator obeys these rules from the start of the run to its end.

1. **Start the run detached.** Start the run with `delegate run --detach`, as in the example. The command starts the run process with the `setsid` system call, so the run is outside the process group of the tool shell. A harness can stop a background task at a time limit, and it can stop the processes of its tool shell. A run that only has `nohup` and `&` can stay in the process group of the tool shell.
2. **Follow the run with `delegate watch`.** Start `delegate watch` as a background task, as in [step 2](#2-see-the-state). The harness then tells the coordinator when it exits. A background task has no terminal, so `watch` prints the raw lines, as with `--raw`. When it exits at a limit, start it again with `--from <position>`, as in [step 4](#4-go-on-after-a-limit).
3. **Show `delegate status` as it is.** Each time the coordinator tells the person the state, it shows the output of `delegate status`, as it is. The coordinator does not write its own script to read the journal or an event stream.
4. **Report a stall at once.** A stalled agent is an agent with no activity for the stall limit, the time that `--stall-minutes` sets. `watch` then exits with the reason `stall`, as [the stall](../reference/status-and-watch.md#the-stall) tells. The coordinator tells the person at once, and gives the output of `delegate status`.
5. **Resume a stopped run.** When the run process stops before the run ends, the coordinator goes on with `delegate run --detach --resume <run-id>`. The resume takes over the lock of the same run when its process no longer exists, as in [the resume after a kill](#2-resume-after-a-kill). If the lock belongs to another run and to a process that no longer exists, the coordinator adds `--break-lock`. A paused run goes on with the same command. After a change of the workflow, the coordinator first runs the dry run of the resume, and adds `--reviewed`. With `--detach`, a refused plan change prints its error on stderr, exits 1, and starts no process.
