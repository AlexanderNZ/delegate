# How to bootstrap a monorepo

Use this page when one repository holds more than one stack, for example a Python backend and a web front end. You make one agent pair for each stack. Each specialist then carries the skills of its own stack, and each verifier may run only the gate of its own stack.

For a repository with one stack, see [how to bootstrap a single-stack repository](bootstrap-a-single-stack-repository.md). This page uses the same steps. It differs in the delegation document, and in the number of `bootstrap` runs.

## Before you start

- Install `delegate`. See [step 1 of the tutorial](../tutorial.md#1-install-the-kit).
- Work in a git repository. The examples use a backend in `backend/` and a web front end in `web/`.

## Steps

### 1. Write one gate block for each stack

The delegation document has one sub-heading for each stack under `Verification gates`. A sub-heading that names a path in backticks claims that path. The brief generator then gives a verifier the block of each stack that the change touches, and no other block.

````markdown docs/agents/delegation.md
# Delegation: the rules of this repository

## Verification gates

### Backend (`backend/`)

```bash
cd backend && python3 -m pytest
```

### Web (`web/`)

```bash
cd web && npm test
```

## Hotspots

Only the coordinator changes these paths:

- `docs/agents/delegation.md`
- `workflow.toml`
````

The [agent-definitions skill](../../skills/agent-definitions/SKILL.md) holds the rules of the gate selection.

### 2. Write one skill for each stack

Each stack has the skills that its specialist needs. Here each stack has one skill, and a real repository can have many.

```markdown .claude/skills/backend-style/SKILL.md
---
name: backend-style
description: The style rules of the backend. Use when you change code in backend/.
---

# Backend style

- A function has type hints.
- A test calls the public function.
```

```markdown .claude/skills/web-style/SKILL.md
---
name: web-style
description: The style rules of the web front end. Use when you change code in web/.
---

# Web style

- A component has one job.
- A test renders the component and checks the result.
```

### 3. Bootstrap one pair for each stack

Run `bootstrap` once for each stack. The option `--name` gives the stack its own agents and its own context skill, so the runs do not overwrite each other.

```bash
delegate bootstrap --repo . --name backend \
  --domain "The Python backend, with a pytest suite." \
  --tier standard \
  --skill backend-style \
  --reference docs/agents/delegation.md \
  --gate-command "python3 -m pytest"
delegate bootstrap --repo . --name web \
  --domain "The web front end, with an npm test suite." \
  --tier standard \
  --skill web-style \
  --reference docs/agents/delegation.md \
  --gate-command "npm test"
```

Each verifier may run the gate command that you gave to its own run, and no other build command. The backend verifier cannot run `npm test`. See [the bootstrap reference](../reference/commands.md#delegate-bootstrap) for every option.

The runs write these agents: `backend-specialist`, `backend-verifier`, `web-specialist`, and `web-verifier`.

### 4. Check the pairs and commit them

```bash
delegate validate --claude-code .claude/agents --opencode .opencode/agents --skills-dir .claude/skills
git add -A
git commit -m "Add the agent pairs"
```

### 5. Name each pair in a workflow

A workflow has one `[stacks.<name>]` table for each stack. A ticket names the stack that builds it. The engine spawns the specialist and the verifier of that stack.

```toml workflow.toml
base-branch = "main"
run-branch = "run/demo"
mode = "assure"
adapter = "claude-code"

[stacks.backend]
specialist = "backend-specialist"
verifier = "backend-verifier"
gates = ["cd backend && python3 -m pytest"]
hotspots = ["docs/agents/delegation.md", "workflow.toml"]

[stacks.web]
specialist = "web-specialist"
verifier = "web-verifier"
gates = ["cd web && npm test"]
hotspots = ["docs/agents/delegation.md", "workflow.toml"]

[[tickets]]
id = "1"
text = "The API returns the list of items."
stack = "backend"
blocked-by = []

[[tickets]]
id = "2"
text = "The page shows the list of items."
stack = "web"
blocked-by = ["1"]
```

Check the file. The command builds nothing.

```bash
delegate run workflow.toml --dry-run
```

The plan puts ticket `1` before ticket `2`. See [the workflow reference](../reference/workflow.md) for every field.
