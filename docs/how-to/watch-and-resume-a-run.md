# How to watch and resume a run

Use this page to follow a run while it works, and to go on with a run that stopped. The state of a run is in its journal. A stop loses no finished work.

## Before you start

You have a workflow file and the agent pair for each stack. See [how to bootstrap a single-stack repository](bootstrap-a-single-stack-repository.md). This page uses this workflow with two tickets.

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

`status` prints one line for each ticket. It shows the state, the verdict, and the branch. See [the status reference](../reference/status-and-watch.md#delegate-status) for the parts of a line.

`watch` prints each event as it happens and exits when the run ends. The option `--stall-minutes` makes it exit when nothing changes for 30 minutes. The option `--max-minutes` makes it exit before a harness time limit. Set the stall time to more than the longest time of one agent run.

### 3. Act on the exit code

The exit code of `watch` tells you why it stopped: the run succeeded, the run failed, a step has a problem, or a limit is reached. See [the exit codes of `watch`](../reference/status-and-watch.md#exit-codes) for each code.

You can run `watch` as a background task in your harness. The harness then alerts you when the command exits.

### 4. Go on after a limit

Each `watch` prints a line `position <seq>` before it exits. To go on from that point, give the position. No event is printed twice.

```bash
delegate watch --from <position> --stall-minutes 30
```

## Resume a run

A run stops when you press Ctrl-C, or when its process is killed. Such a run has no `run-end` in its journal, and `delegate status` shows the result `running`.

### 1. Resume after Ctrl-C

Give the run id. The workflow file is the one that the run started from. The engine rebuilds the state from the journal and starts at the first step that is not complete. It never builds a finished step again.

```bash
delegate run --resume <run-id>
```

A ticket with a recorded commit is not built again. A step that was open continues in its own worktree. See [the resume reference](../reference/run.md#the-resume) for each case.

### 2. Resume after a kill

A process that is killed cannot release the lock of its run branch. The resume then refuses, and the message tells you to pass `--break-lock`. First check that no other run uses the run branch. Then remove the lock and resume.

```bash
delegate run --resume <run-id> --break-lock
```

The option does not remove the lock of a process that still exists. See [the run lock](../reference/run.md#the-run-lock).

A run that the invariant check halted, or that has a `run-end`, cannot be resumed.
