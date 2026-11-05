# How to give the agents a command-line tool

Use this page when the agents of a [**run**](../glossary.md#run) must use a tool, for example a code index, and the tool has a command-line interface. A command-line tool costs no context until an agent runs it. The instructions and the tool definitions of an [MCP server](give-the-agents-an-mcp-server.md) load into each [**session**](../glossary.md#session). So when a tool has both, the command-line interface is often the better choice. The setup has four parts: the [**specialist**](../glossary.md#specialist), the [**verifier**](../glossary.md#verifier), the data of the tool, and the git hooks of the host.

## Before you start

- Write the [**workflow**](../glossary.md#workflow) of the run. See [how to write a workflow for a run](write-a-workflow-for-a-run.md).
- Install the tool on the host that runs the agents, and run it once by hand, to know which subcommands read and which subcommands write.
- Write the [**declaration**](../glossary.md#declaration) of the agents, or find the one that made the agent files of the repository.

## The example

The example gives the agents of the [**stack**](../glossary.md#stack) `python` a code index, `codegraph`. The version that the example shows is `codegraph` 1.6.2. Measured with this version:

- `codegraph explore` writes nothing in `.codegraph/`. It does not update the index after a change of a file.
- `codegraph sync` after a change of one file took about 1 second.
- A second `codegraph sync` that starts while one runs in the same tree fails at once with exit 1. It does not wait for the lock.
- `.codegraph/.gitignore` holds `*` and `!.gitignore`, so git does not ignore that file. In a repository whose `.gitignore` does not name `.codegraph/`, `git status` shows `?? .codegraph/` in the [**worktree**](../glossary.md#worktree), and the clean check of `prepare` fails it.
- A copied `codegraph.lock` that names a live process makes `codegraph sync` fail with exit 1 ("locked by another process"). `codegraph sync` after a copy of only `codegraph.db` exits 0 and writes `.codegraph/.gitignore` again.

The declaration gives the verifier the subcommand that reads. The workflow builds the index.

```toml title="agents.toml"
[agents.python]
description = "A Python application."
gateCommands = ["uv run pytest tests/unit", "codegraph explore *"]
```

```text title=".gitignore"
.codegraph/
```

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
prepare = [
  "uv sync --frozen",
  "if [ -f \"$DELEGATE_REPO/.codegraph/codegraph.db\" ]; then rm -rf .codegraph && mkdir .codegraph && cp \"$DELEGATE_REPO/.codegraph/codegraph.db\" .codegraph/ && codegraph sync; else codegraph init -y .; fi",
]
hotspots = ["workflow.toml", ".github/workflows/*", "uv.lock"]
gate-timeout = 20
agent-timeout = 90

[[tickets]]
id = "1"
text = "The export command reads the data set."
stack = "python"
blocked-by = []
```

## Steps

### 1. Let the specialist run the tool

The specialist can run each shell command, so the tool needs no entry in the declaration for it. The specialist can also run `codegraph sync` after it changes a file.

A host in `dontAsk` [**permission mode**](../glossary.md#permission-mode) denies each command that no rule allows, because a [**headless**](../glossary.md#headless) session cannot ask. So such a host needs one allow rule for the command. See [the settings of a host](../reference/claude-code-adapter.md#the-settings-of-a-host).

```json title="settings.json"
{
  "permissions": {
    "defaultMode": "dontAsk",
    "allow": ["Bash(codegraph *)"]
  }
}
```

### 2. Give the verifier the subcommand that reads

The [**verifier guard**](../glossary.md#verifier-guard) permits only the read commands and the entries of `gateCommands` in the declaration. So the verifier cannot run the tool until the declaration names it.

Add the subcommand that reads to `gateCommands`, with a trailing `*`. For the example, the entry is `codegraph explore *`. The guard then permits each word after the entry, except a word that the shell can expand. Without the `*`, the guard permits the subcommand alone, and the verifier cannot give it a question.

Name only the subcommand that reads, and not the tool. Other subcommands can write: `codegraph sync` and `codegraph index` change the index, and `codegraph init -y` makes it. The guard denies each of them, because the entry does not name them. See [the verifier guard](../glossary.md#verifier-guard) and [the guard sets as JSON](../reference/commands.md#the-guard-sets-as-json).

Render the pair again, and copy the agent files to the project directory of the [**adapter**](../glossary.md#adapter).

```bash
delegate render agents.toml -o out
```

### 3. Make the data of the tool in `prepare`

A tree that git makes holds no ignored file. So an index or a cache of the tool is not in the worktree of a [**ticket**](../glossary.md#ticket). It is also not in the [**temporary copy**](../glossary.md#temporary-copy) of a verifier.

Make the data in `prepare`. The [**engine**](../glossary.md#engine) runs `prepare` in both trees, and gives each command the environment entry `DELEGATE_REPO`, the path of the main checkout. Copy the data from the main checkout, then update it. A copy and an update are faster than a new build. The example removes `.codegraph/`, copies only `codegraph.db` from the main checkout, and runs `codegraph sync`. When the main checkout has no `codegraph.db`, it runs `codegraph init -y .`. The recipe copies only the database because the lock file and the pid files of the main checkout name a live process, and a copy of them makes `codegraph sync` fail. The recipe removes `.codegraph/` first, so it is safe when `prepare` runs again in a reused worktree: `cp -R` would copy into `.codegraph/.codegraph`. See [prepare](../reference/workflow.md#prepare).

The worktree must be clean after `prepare`. The `.gitignore` of the repository must name `.codegraph/`, as in the worked example. The file `.codegraph/.gitignore` does not ignore itself, so without the line the check fails. The same holds for the data of any tool.

### 4. Keep the git hooks of the host safe

A host can have git hooks that update the data of the tool. The engine runs `git worktree add` and `git checkout`, so these hooks run in a run.

- A `post-checkout` hook that exits with a status that is not 0 makes `git worktree add` and `git checkout` fail. So such a hook must always exit 0.
- A hook must not start the update in the background. Two updates in one tree can collide, and the second one fails at once.

### 5. Check the workflow before each launch

Run the dry run.

```bash
delegate run workflow.toml --dry-run
```

For the example, the output has the line `stack python prepare: uv sync --frozen`, and one such line for the second command. A line that starts with `warn` names a problem to fix. See [the quality check](../reference/run.md#the-quality-check).
