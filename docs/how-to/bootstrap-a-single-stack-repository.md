# How to bootstrap a single-stack repository

Use this page when your repository has one stack, for example one Python package. You get one agent pair: a specialist and its verifier twin. The pair is for Claude Code and for OpenCode.

For a repository with more than one stack, see [how to bootstrap a monorepo](bootstrap-a-monorepo.md).

## Before you start

- Install `delegate`. See [step 1 of the tutorial](../tutorial.md#1-install-the-kit).
- Work in a git repository. The examples below use a Python package.

## Steps

### 1. Write the delegation document

The document holds the gate commands and the hotspots of the repository. The verifier brief takes the gates from the first block under the heading `Verification gates`.

````markdown docs/agents/delegation.md
# Delegation: the rules of this repository

## Verification gates

```bash
python3 -m pytest -rs
```

## Hotspots

Only the coordinator changes these paths:

- `docs/agents/delegation.md`
- `workflow.toml`
````

### 2. Write the skill with your style rules

Each agent preloads the skills that you name. A skill is a Markdown file in `.claude/skills/<name>/SKILL.md`. The bootstrap command refuses a skill that does not exist.

```markdown .claude/skills/house-style/SKILL.md
---
name: house-style
description: The style rules of this repository. Use when you write or change code here.
---

# House style

- A test calls the public function and checks the result.
- A function has type hints.
```

### 3. Bootstrap the pair

```bash
delegate bootstrap --repo . --name python \
  --domain "A Python package with unit tests." \
  --tier standard \
  --skill house-style \
  --reference docs/agents/delegation.md \
  --gate-command "python3 -m pytest"
```

The option `--gate-command` is important. The verifier may run your gate command, and it may run no other build command. Give one `--gate-command` for each gate that the verifier must run.

The command writes these files:

- The context skill and the declaration, in `.claude/skills/python-context/`.
- The agents `python-specialist` and `python-verifier`, in `.claude/agents/` and in `.opencode/agents/`.

Run the command a second time with the same arguments. It changes no byte. See [the bootstrap reference](../reference/commands.md#delegate-bootstrap) for every option, and for the output language and the tracked-file build.

### 4. Check the pair

```bash
delegate validate --claude-code .claude/agents --opencode .opencode/agents --skills-dir .claude/skills
```

The command prints `ok` and exits 0 when the files are valid. See [the finding codes](../reference/commands.md#the-finding-codes) for the cases where it does not.

### 5. Commit the files

The engine builds each ticket in a git worktree that starts from your base branch. The worktree holds only what you committed.

```bash
git add -A
git commit -m "Add the agent pair"
```

### 6. Name the pair in a workflow

The workflow file names the agents that `--name python` made. The names are `python-specialist` and `python-verifier`.

```toml workflow.toml
base-branch = "main"
run-branch = "run/demo"
mode = "assure"
adapter = "claude-code"

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = ["python3 -m pytest"]
hotspots = ["docs/agents/delegation.md", "workflow.toml"]

[[tickets]]
id = "1"
text = "The export command writes a CSV file."
stack = "python"
blocked-by = []
```

Check the file. The command builds nothing.

```bash
delegate run workflow.toml --dry-run
```

The command prints the plan and exits 0. See [the workflow reference](../reference/workflow.md) for every field, and [the tutorial](../tutorial.md) to build the ticket.
