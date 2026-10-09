# How to use the kit after `to-spec` and `to-tickets`

Use this page when you plan work with the [**`to-spec`**](../glossary.md#to-spec) and [**`to-tickets`**](../glossary.md#to-tickets) skills of [mattpocock/skills](https://github.com/mattpocock/skills), and you want the kit to build the [**tickets**](../glossary.md#ticket). Those skills write a [**spec**](../glossary.md#spec) and tickets with blocking edges. The kit takes them as its input, and replaces only the build step.

The [**engine**](../glossary.md#engine) never contacts an issue tracker. The agent that invokes the engine saves the tickets to files first. Then you write a [**workflow**](../glossary.md#workflow) that names the files.

## Before you start

- Make the [**agent pair**](../glossary.md#agent-pair) for each [**stack**](../glossary.md#stack). See [how to bootstrap a single-stack repository](bootstrap-a-single-stack-repository.md) or [how to bootstrap a monorepo](bootstrap-a-monorepo.md).
- Plan the work with `to-spec` and `to-tickets`. Each ticket must say what to build as behaviour, and which tickets block it.

## Steps

### 1. Save each ticket to a file

If your tickets are in a tracker, save the body of each ticket to a file with the tool of your tracker. Any agent can do this. Name the file by the ticket, for example `tickets/1.md`. The kit does not care which tool you use.

A ticket file from `to-tickets` has the sections below. The section `Blocked by` lists the tickets that must be done first.

```markdown title="tickets/1.md"
# Read the data set

## What to build

The export command reads the data set. A data set with three rows gives three records.

## Acceptance criteria

- [ ] The command reads a data set from a file.
- [ ] The command reports a file that does not exist.

## Blocked by

None.
```

```markdown title="tickets/2.md"
# Write a header row

## What to build

The export command writes a header row. The header row names each column of the data set.

## Acceptance criteria

- [ ] The first line of the output is the header row.

## Blocked by

- #1
```

Ticket text is about behaviour and holds no file path. The engine adds the [**file boundary**](../glossary.md#file-boundary) to the [**brief**](../glossary.md#brief) of the [**specialist**](../glossary.md#specialist).

### 2. Write the workflow

Each `[[tickets]]` table names the file in `text-file`. The path is relative to the directory of the workflow file. Copy the `Blocked by` section of each ticket into `blocked-by`, as ticket ids.

```toml title="workflow.toml"
base-branch = "main"
run-branch = "run/plan"
mode = "assure"
adapter = "claude-code"

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = ["python3 -m unittest"]
hotspots = ["workflow.toml", "tickets/"]

[[tickets]]
id = "1"
text-file = "tickets/1.md"
stack = "python"
blocked-by = []

[[tickets]]
id = "2"
text-file = "tickets/2.md"
stack = "python"
blocked-by = ["1"]
```

The ticket files are [**hotspots**](../glossary.md#hotspot) in this example, so a specialist cannot change them. See [the workflow reference](../reference/workflow.md#tickets) for the fields of a ticket.

### 3. Choose a mode

Use [**`assure`**](../glossary.md#assure) to verify each ticket at once. Use [**`economy`**](../glossary.md#economy) for a long chain of small tickets. Set [**`mode`**](../glossary.md#mode) in the workflow. See [how to run an economy chain](run-an-economy-chain.md).

### 4. Commit, check, and run

The engine builds in [**worktrees**](../glossary.md#worktree) that start from your [**base branch**](../glossary.md#base-branch), so commit the files first. Then check the plan, and run.

```bash
git add -A
git commit -m "Add the plan"
delegate run workflow.toml --dry-run
delegate run workflow.toml
```

The dry run reports a ticket file that does not exist, a [**blocker**](../glossary.md#blocker) that no ticket has, and a cycle. The plan puts ticket `1` before ticket `2`.

### 5. Read the result and merge

Follow the [**run**](../glossary.md#run) with `delegate status` and `delegate watch`. See [how to watch and resume a run](watch-and-resume-a-run.md). When the run ends, merge the [**run branch**](../glossary.md#run-branch) yourself.

```bash
git merge --ff-only run/plan
```

The engine never merges to your base branch, never pushes, and never closes a ticket. Close the tickets in your tracker yourself.
