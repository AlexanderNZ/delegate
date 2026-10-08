# How to run an economy chain

Use this page when you have a long chain of small tickets and you want the fewest tokens. In `economy` mode, the engine builds the tickets one after the other. Each ticket builds on the one before it. The engine verifies once for each stack, at the end of the chain.

If you need to find a defect before the next ticket builds on it, use `assure` mode. See [the workflow reference](../reference/workflow.md#modes) for the two modes.

## Before you start

- Make the agent pair for your stack. See [how to bootstrap a single-stack repository](bootstrap-a-single-stack-repository.md).
- Commit the agent files. The engine builds in git worktrees, and a worktree holds only what you committed.
- Write each ticket as behaviour. A ticket holds no file path. The engine adds the file boundary to the brief.

## Steps

### 1. Write the workflow

Set `mode = "economy"`. Put the order of the chain in `blocked-by`. Here ticket `3` waits for ticket `2`, and ticket `2` waits for ticket `1`.

```toml workflow.toml
base-branch = "main"
run-branch = "run/chain"
mode = "economy"
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

[[tickets]]
id = "3"
text = "The export command quotes a comma."
stack = "python"
blocked-by = ["2"]
```

The specialists run on the tier `standard`. To use a cheaper tier, add `[tier-overrides]` with `specialist = "cheap"`. The verifier tier never goes down, in any mode.

### 2. Check the plan

```bash
delegate run workflow.toml --dry-run
```

The command prints the tickets in the order of the chain. It builds nothing.

### 3. Run the chain

```bash
delegate run workflow.toml
```

The engine does these things:

1. It makes the run branch `run/chain`.
2. It builds ticket `1` in a worktree. It runs the gates itself.
3. It builds ticket `2` on the branch of ticket `1`, and ticket `3` on the branch of ticket `2`.
4. It starts one blind verifier for the stack. The verifier gets the text of the three tickets and the diff of each ticket.
5. On ACCEPT, it moves the run branch to the end of the chain.

The command prints `run <run id>` and `journal <path>`. It exits 0 when every ticket is built and accepted.

### 4. Read the result

```bash
delegate status
```

Each ticket has one line. The state `built` and the verdict `ACCEPT` mean that the ticket is done and checked. The verdict of the stack covers each ticket in the chain.

### 5. Take the work

The engine never merges to your base branch. The run branch holds only accepted work, so a fast-forward is enough.

```bash
git merge --ff-only run/chain
```

## When the verifier rejects the chain

A REJECT starts one fix-up round. The fix-up is a new commit on the end of the chain. A fresh verifier then checks the new commit and the findings. A second REJECT fails the stack, and the run branch does not move.

The verifier starts each finding with the id of its ticket in square brackets, for example `[2] The header row is missing.` The engine uses the label to tell you which ticket a finding is about. See [the chain verification](../reference/run.md#the-chain-verification) for the rules of the finding map.
