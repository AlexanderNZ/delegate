# How to watch and resume a run

Use this page to follow a [**run**](../glossary.md#run) while it works, and to go on with a run that stopped. The state of a run is in its [**journal**](../glossary.md#journal). A stop loses no finished work.

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

The command prints `run <run id>` and `journal <path>`. Keep the run id.

### 2. See the state

In a second terminal, in the same repository, read the state of the newest run.

```bash
delegate status
delegate watch --stall-minutes 30 --max-minutes 50
```

`status` prints the state of the run once, and exits. It prints the progress of the run: the percentage, the ETA, and the tokens as two counts, the new tokens and the cache reads. Then it prints one line for each ticket, with the state, the [**verdict**](../glossary.md#verdict), and the branch. Under the line of a ticket with a [**step**](../glossary.md#step), it prints the time used and the tokens. While an agent runs, it also prints the time since its last activity. See [the status reference](../reference/status-and-watch.md#delegate-status) for the parts of a line. A program can read the same snapshot with `delegate status --json`. See [the snapshot](../reference/status-and-watch.md#the-snapshot).

`watch` follows the run until it ends. In a terminal, it shows the live view. At the top, the live block shows the same progress as `status`. The run has one line, with the percentage, the time used, the ETA, and the tokens. Each ticket has one line, with the state, the phase, the tokens, and the time since the last activity of its agent. Below the block, the feed shows one short line for each event that matters: the local time, the ticket, and what happened. For example, `10:51  #64 specialist finished · 36m · tokens 302k new, 9.7M cache · report ok`. A problem and a stall are lines that start with `✗`. `watch` draws the frame again at each poll. See [the live view](../reference/status-and-watch.md#the-live-view).

The option `--stall-minutes` makes `watch` exit when nothing changes for 30 minutes. The option `--max-minutes` makes it exit before a [**harness**](../glossary.md#harness) time limit. The journal names the [**event stream**](../glossary.md#event-stream) of each agent when the agent starts, so an agent that writes its stream is not a stall. The journal also names the log file of each [**gate**](../glossary.md#gate) when the gate starts, so a long gate that writes output is not a stall.

When standard output is not a terminal, `watch` prints one raw line for each event, for a script or a harness. To see the raw lines in a terminal, add `--raw`:

```bash
delegate watch --raw
```

See [the raw lines](../reference/status-and-watch.md#the-raw-lines).

When the newest run has ended, `watch` with no run id shows none of its events. It prints one line with the result of that run, and exits. To see the events of a run that ended, give its run id:

```bash
delegate watch <run-id> --raw
```

See [the newest run that ended](../reference/status-and-watch.md#the-newest-run-that-ended).

### 3. Act on the exit code

The exit code of `watch` tells you why it stopped: the run succeeded, the run failed, a [**step**](../glossary.md#step) has a problem, or a limit is reached. See [the exit codes of `watch`](../reference/status-and-watch.md#exit-codes) for each code.

You can run `watch` as a background task in your harness. The harness then alerts you when the command exits.

### 4. Go on after a limit

Each `watch` prints a line `position <seq>` before it exits. To go on from that point, give the position. No event is printed twice.

```bash
delegate watch --from <position> --stall-minutes 30
```

## Resume a run

A run stops when you press Ctrl-C, or when its process is killed. Such a run has no `run-end` in its journal, and `delegate status` shows the result `running`.

### 1. Resume after Ctrl-C

Give the run id. The workflow file is the one that the run started from. The [**engine**](../glossary.md#engine) rebuilds the state from the journal and starts at the first step that is not complete. It never builds a finished step again.

```bash
delegate run --resume <run-id>
```

A ticket with a recorded commit is not built again. A step that was open continues in its own [**worktree**](../glossary.md#worktree). See [the resume reference](../reference/run.md#the-resume) for each case.

### 2. Resume after a kill

A process that is killed cannot release the lock of its [**run branch**](../glossary.md#run-branch). Resume the run with the same command as after Ctrl-C. The lock names the same run id and a process that no longer exists, so the [**resume**](../glossary.md#resume) takes it over and needs no flag. When the run ends, the command prints a note on stderr that names the old process.

A lock whose process still exists stays. A lock of another run id, whose process no longer exists, needs `--break-lock`. First check that no other run uses the run branch. See [the run lock](../reference/run.md#the-run-lock).

A run that the [**invariant**](../glossary.md#invariant) check halted, or that has a `run-end`, cannot be resumed.

## Report a run as a coordinator agent

A [**coordinator**](../glossary.md#coordinator) agent can start a run for a person. The person cannot see the agents, so the coordinator reports the run. This example starts the run detached from the tool shell.

```sh
delegate run --detach workflow.toml
```

The command prints `run <run id>` and `log <path>`, and exits at once. The run goes on in its own process, and writes its output to the log file of the run. See [the detached run](../reference/run.md#the-detached-run). `delegate status` and `delegate watch` with no run id read the newest run. The coordinator obeys these rules from the start of the run to its end.

1. **Start the run detached.** Start the run with `delegate run --detach`, as in the example. The command starts the run process with the `setsid` system call, so the run is outside the process group of the tool shell. A harness can stop a background task at a time limit, and it can stop the processes of its tool shell. A run that only has `nohup` and `&` can stay in the process group of the tool shell.
2. **Follow the run with `delegate watch`.** Start `delegate watch` as a background task, as in [step 2](#2-see-the-state). The harness then tells the coordinator when it exits. A background task has no terminal, so `watch` prints the raw lines, as with `--raw`. When it exits at a limit, start it again with `--from <position>`, as in [step 4](#4-go-on-after-a-limit).
3. **Show `delegate status` as it is.** Each time the coordinator tells the person the state, it shows the output of `delegate status`, as it is. The coordinator does not write its own script to read the journal or an event stream.
4. **Report a stall at once.** A stalled agent is an agent with no activity for the stall limit, the time that `--stall-minutes` sets. `watch` then exits with the reason `stall`, as [the stall](../reference/status-and-watch.md#the-stall) tells. The coordinator tells the person at once, and gives the output of `delegate status`.
5. **Resume a stopped run.** When the run process stops before the run ends, the coordinator goes on with `delegate run --detach --resume <run-id>`. The resume takes over the lock of the same run when its process no longer exists, as in [the resume after a kill](#2-resume-after-a-kill). If the lock belongs to another run and to a process that no longer exists, the coordinator adds `--break-lock`.
