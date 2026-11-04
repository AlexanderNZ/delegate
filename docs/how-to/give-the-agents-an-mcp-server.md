# How to give the agents an MCP server

Use this page when the agents of a [**run**](../glossary.md#run) must use an MCP server, for example a code index. The setup has five parts, and each part is in a different place: the server in `mcp-servers`, the tools of the [**verifier**](../glossary.md#verifier) in `verifier-tools`, the index in `prepare`, the allow rules in the settings file of the host, and the stop of the daemon in `env`. This page puts the five parts in one order. [The workflow reference](../reference/workflow.md#mcp-servers) gives each field, its type and its rules.

## Before you start

- Write the [**workflow**](../glossary.md#workflow) of the run. See [how to write a workflow for a run](write-a-workflow-for-a-run.md).
- Install the server on the host that runs the agents, and run it once by hand, to know its tools.

## The example

The example gives the agents of the [**stack**](../glossary.md#stack) `python` a code index server, `codegraph`. The version that the example shows is `codegraph` 1.6.2. `tools/list` of `codegraph serve --mcp` 1.6.2 holds one tool, `codegraph_explore`. Its `readOnlyHint` is true. A later version can add tools, so run `tools/list` again after each upgrade of the server, and update `verifier-tools` and the allow rules.

```toml title="workflow.toml"
base-branch = "main"
run-branch = "run/index"
mode = "assure"
adapter = "claude-code"

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = [
  { name = "unit", command = "uv run pytest tests/unit" },
]
prepare = ["uv sync --frozen", "codegraph init -y ."]
hotspots = ["workflow.toml", ".github/workflows/*", "uv.lock"]
gate-timeout = 20
agent-timeout = 90

[stacks.python.mcp-servers.codegraph]
command = ["codegraph", "serve", "--mcp"]
env = { CODEGRAPH_NO_DAEMON = "1" }
verifier-tools = ["codegraph_explore"]

[[tickets]]
id = "1"
text = "The export command reads the data set."
stack = "python"
blocked-by = []
```

## Steps

### 1. Choose which agents get the server

The table `[stacks.<name>.mcp-servers.<server>]` gives the server to the agents of that stack, and to no agent of another stack.

| Agent | Tools of the server that it can use |
|---|---|
| The [**specialist**](../glossary.md#specialist) | Every tool. A rendered specialist has no list of tools. |
| The verifier | Only the tools in `verifier-tools`. |

A server with no `verifier-tools` gives the verifier no tool, and the dry run prints a `warn` line for it. See [MCP servers](../reference/workflow.md#mcp-servers).

### 2. Choose `verifier-tools`

The verifier is read-only. Put in `verifier-tools` only the tools that read. A tool that changes the tree, a service or an external system does not go to a verifier.

Read `tools/list` of the server, and use the hints of each tool as a first filter. Then read the description of each tool, because a hint is a claim of the server and not a check. The example has one tool, `codegraph_explore`, and it only reads the index, so the example gives it to the verifier.

Give each tool by its name in the server, as one exact name. Do not write the prefix `mcp__<server>__`, and do not write `*`: the loader refuses both.

### 3. Build the index in `prepare`

A server can need an index or a cache in each tree. A tree that git makes holds no ignored file, so the [**worktree**](../glossary.md#worktree) of a [**ticket**](../glossary.md#ticket) has no index. Put the command that builds it in `prepare`. The example uses `codegraph init -y .`.

The [**engine**](../glossary.md#engine) runs `prepare` in the worktree of the ticket, and in the copy of each verifier, so both trees get an index. The worktree must be clean after `prepare`, so add the directory that the command writes to `.gitignore`. For the example, add `.codegraph/`. See [prepare](../reference/workflow.md#prepare).

### 4. Add the allow rules on a host in `dontAsk` mode

A [**headless**](../glossary.md#headless) [**session**](../glossary.md#session) cannot ask for a permission, and `dontAsk` [**permission mode**](../glossary.md#permission-mode) denies each tool that no rule allows. See [what the adapter does not set](../reference/claude-code-adapter.md#what-the-adapter-does-not-set). So a host that gives the agents a settings file with `DELEGATE_AGENT_SETTINGS` needs an allow rule for each MCP tool. See [the settings of a host](../reference/claude-code-adapter.md#the-settings-of-a-host).

Write one rule for each tool, in the form `mcp__<server>__<tool>`, and no wildcard. A wildcard would also allow a tool that a later version adds, before you read it.

```json title="settings.json"
{
  "permissions": {
    "defaultMode": "dontAsk",
    "allow": ["mcp__codegraph__codegraph_explore"]
  }
}
```

The rule is for the permission of the session. The seal of the verifier, which `verifier-tools` makes, decides which tools the verifier has at all. A tool needs both.

### 5. Stop the daemon of the server

Some servers start a daemon. `codegraph serve --mcp` starts a detached daemon, with the parent pid 1 and an idle timeout of 300 seconds. Measured on 2026-11-04 with `codegraph` 1.6.0 in a worktree. The daemon is outside the process group of the agent, so the timeout of the agent does not end it, and it stays after the clean removes the worktree.

Set the setting of the server that stops the daemon in `env`. For `codegraph`, it is `CODEGRAPH_NO_DAEMON = "1"`. Measured with 1.6.0: with this setting no process stays after the session, and the tools work.

Check this after a run. List the processes of the server, and make sure that none belongs to the run.

```bash
pgrep -fl "codegraph.*serve"
```

A process of another session on the same host also shows in the list. A process whose path is in a worktree of the run, or one that has the parent pid 1 after the run ended, is a daemon that stays. End it, and look for the setting that stops it.

### 6. Check the workflow before each launch

Run the dry run.

```bash
delegate run workflow.toml --dry-run
```

For the example, the quality check holds the line `ok verifier-tools: stack python: server codegraph: verifier-tools codegraph_explore`. A line `warn verifier-tools` names a server with no `verifier-tools`. See [the quality check](../reference/run.md#the-quality-check).
