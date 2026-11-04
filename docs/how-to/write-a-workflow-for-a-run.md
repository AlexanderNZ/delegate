# How to write a workflow for a run

Use this page when you write a [**workflow**](../glossary.md#workflow) file for a [**run**](../glossary.md#run), or when you copy one from an older run. The page gives the choices that change the quality and the time of a run. [The workflow reference](../reference/workflow.md) gives each field, its type and its default.

A copied workflow does not have the fields that a later version added. The dry run tells you which settings are missing. So run it before each launch, and read its quality check.

## Before you start

- Make the [**agent pair**](../glossary.md#agent-pair) of each [**stack**](../glossary.md#stack), and commit it. See [how to bootstrap a single-stack repository](bootstrap-a-single-stack-repository.md).
- Write each [**ticket**](../glossary.md#ticket) as behaviour, with no file path. The [**engine**](../glossary.md#engine) adds the [**file boundary**](../glossary.md#file-boundary) to the [**brief**](../glossary.md#brief).

## The example

This workflow has one stack and three tickets. Each section below explains one part of it.

```toml title="workflow.toml"
base-branch = "main"
run-branch = "run/export"
mode = "assure"
adapter = "claude-code"

[tier-overrides]
specialist = "standard"

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = [
  { name = "unit", command = "uv run pytest tests/unit" },
  { name = "e2e", command = "uv run pytest tests/e2e" },
]
prepare = ["uv sync --frozen"]
# The engine also reserves .claude/agents/ and .opencode/agents/ in every stack.
hotspots = ["workflow.toml", ".github/workflows/*", "uv.lock"]
gate-timeout = 20
agent-timeout = 90

[[tickets]]
id = "1"
text = "The export command reads the data set."
stack = "python"
blocked-by = []
tier = "strong"

[[tickets]]
id = "2"
text = "The export command writes a header row."
stack = "python"
blocked-by = ["1"]

[[tickets]]
id = "3"
text = "The import command reports an empty file."
stack = "python"
blocked-by = []
```

## Steps

### 1. Choose the mode

Set [**`mode`**](../glossary.md#mode) to [**`assure`**](../glossary.md#assure) or [**`economy`**](../glossary.md#economy).

- `assure` verifies each ticket at once, before the next ticket builds on it. Use it when a defect in one ticket spoils the tickets after it.
- `economy` builds a [**chain**](../glossary.md#chain), and verifies once for each stack at the end. Use it for a long chain of small tickets, when the fewest tokens matter most.

See [the mode trade-off](../explanation/the-mode-trade-off.md) for the reasons, and [how to run an economy chain](run-an-economy-chain.md) for an `economy` run.

### 2. Name the agents of each stack

Each `[stacks.<name>]` table names the [**specialist**](../glossary.md#specialist) and the [**verifier**](../glossary.md#verifier) of the stack. Use the names that [**`bootstrap`**](../glossary.md#bootstrap) made with `--name`. A ticket names its stack in `stack`.

### 3. Write the gates, and give them names

A [**gate**](../glossary.md#gate) is a command that the engine runs. The engine does not trust the [**report**](../glossary.md#report) of the specialist for it. Put the commands of the gates of the repository in `gates`.

- Use the same commands as the `--gate-command` options of `bootstrap`. The [**verifier guard**](../glossary.md#verifier-guard) permits only these commands. The setup check of the dry run refuses a gate that the verifier does not permit.
- Each view of a run shows a gate by its short name. Both gates of the example have the short name `pytest`. A table with `name` and `command` gives each gate its own name. See [gate names](../reference/workflow.md#gate-names).

### 4. Put the installs in `prepare`

A tree that git makes holds no ignored file, so it has no installed dependencies. Put the install commands in `prepare`, not in `gates`. The engine runs them in the [**worktree**](../glossary.md#worktree) before the specialist, and in the copy of each verifier.

The worktree must be clean after `prepare`. Add each directory that it writes to `.gitignore`, as `.venv/` for the example. See [prepare](../reference/workflow.md#prepare).

### 5. Set the time limits

Without a limit, a gate or an agent that hangs stops the run for ever. Set both limits for each stack, in minutes.

- `gate-timeout` is the limit of one gate run. Give it more than your slowest full gate run. A gate past the limit is a red gate.
- `agent-timeout` is the limit of one [**session**](../glossary.md#session) of an agent. Give it more than your longest specialist session. An agent past the limit has no valid report.

See [time limits](../reference/workflow.md#time-limits) for what the engine does at each limit.

### 6. Name the hotspots

A [**hotspot**](../glossary.md#hotspot) is a path that only the [**coordinator**](../glossary.md#coordinator) changes. The engine stops a [**step**](../glossary.md#step) whose diff changes one. Name the workflow file, the CI configuration and each file that sets the rules of the agents. With `hotspots = []`, a specialist can change the CI. See [the hotspot guard](../reference/run.md#the-hotspot-guard).

### 7. Keep the default `ticket-start`

In `assure` mode, the example gives no `ticket-start`. Each ticket branch then starts from the tip of the [**run branch**](../glossary.md#run-branch). The rebase keeps the commit, so each ticket runs its gates once.

Set `ticket-start = "base-branch"` only when each specialist must start from the base of the run. A ticket with no `blocked-by` after an accepted ticket then runs its gates two times. See [ticket start](../reference/workflow.md#ticket-start).

### 8. Put the order in `blocked-by`

A ticket that needs the work of another ticket names it in `blocked-by`. Ticket `2` of the example waits for ticket `1`. Ticket `3` needs no other ticket, so its list is empty.

A ticket whose [**blocker**](../glossary.md#blocker) failed is skipped, and the independent tickets still run. A retry that builds the blocker makes the ticket ready again at the next resume. See [the skip rule](../reference/run.md#the-skip-rule).

### 9. Give a ticket a stronger tier

The mode gives the [**tier**](../glossary.md#tier) of each specialist: `strong` in `assure` mode, and `standard` in `economy` mode. `tier-overrides` changes the tier of a role. The example runs its specialists on `standard`.

A ticket can set its own specialist tier with `tier`. Ticket `1` of the example lays the base of the feature, so it gets `strong`.

The verifier tier never goes down. See [the tier of a ticket](../reference/workflow.md#the-tier-of-a-ticket) and [tier overrides](../reference/workflow.md#tier-overrides).

### 10. Check the workflow before each launch

Commit the workflow file. Then run the dry run.

```bash
delegate run workflow.toml --dry-run
```

The command prints the plan, then the section `quality check`, then the result of the setup check. It builds nothing. For the example, each line of the quality check is `ok`.

```text
quality check
ok gate-timeout: stack python: gate-timeout = 20 minutes
ok agent-timeout: stack python: agent-timeout = 90 minutes
ok ticket-start: ticket-start = "run-branch": each ticket starts from the run branch
ok hotspots: stack python: hotspots workflow.toml, .github/workflows/*, uv.lock
ok verifier-tier: the verifier runs on the tier verifier
ok gate-names: stack python: each gate has its own short name: unit, e2e
```

A `warn` line names a setting to add or change, and its effect on the run. A `warn` does not change the exit code, so read each line. Set the setting, or tell the owner of the run why the run goes on without it. See [the quality check](../reference/run.md#the-quality-check) for each check.

Run the same command before each [**resume**](../glossary.md#resume) too. `--resume <run-id> --dry-run` shows the [**plan change**](../glossary.md#plan-change), and no quality check. A run without `--dry-run` prints each `warn` line on stderr at its start.

Then start the run. See [how to watch and resume a run](watch-and-resume-a-run.md).

```bash
delegate run workflow.toml
```
