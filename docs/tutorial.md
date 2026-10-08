# Tutorial: from install to one verified ticket

In this tutorial you run `delegate` once, from start to end. You make a small sample repository. You bootstrap an agent pair for it. You build one ticket in `assure` mode on Claude Code. You read the verdict and the journal.

The longest part is the wait for the agents.

## Before you start

You need these things:

- Python 3.11 or later.
- `git`.
- `uv`, to install the kit.
- Claude Code, installed and logged in. The command `claude` must work in your terminal.

The tutorial uses real agents, so the run uses tokens of your Claude Code account.

## 1. Install the kit

```bash
uv tool install "git+https://github.com/AlexanderNZ/delegate#subdirectory=skills/agent-definitions/validator"
```

This puts the commands `delegate`, `agent-definitions` and `verifier-brief` on your PATH. This tutorial uses only `delegate`.

Check that the install worked:

```bash
delegate --help
```

The output lists the subcommands `render`, `validate`, `bootstrap`, `brief`, `run`, `status`, `watch` and `docs`.

## 2. Get the sample files

The sample files are in the repository of the kit. Clone it.

```bash
git clone https://github.com/AlexanderNZ/delegate delegate-kit
```

The sample files are in `delegate-kit/examples/tutorial`. There are six files:

| File | What it is |
|---|---|
| `words.py` and `test_words.py` | A small Python function and its test. |
| `docs/agents/delegation.md` | The gate command and the hotspots of the project. |
| `.claude/skills/sample-style/SKILL.md` | A skill with the style rules of the project. |
| `ticket.md` | The ticket that you will build. |
| `workflow.toml` | The workflow file. It names the ticket, the mode, and the gate. |

## 3. Make the sample repository

Make a new directory, start a git repository in it, and copy the sample files in.

```bash
mkdir sample
cd sample
git init -b main
git config user.name "Tutorial User"
git config user.email "tutorial@example.com"
cp -R ../delegate-kit/examples/tutorial/. .
```

The `git config` lines set a name for the commits. Use your own name if you prefer.

## 4. Bootstrap the agent pair

The kit builds each ticket with a specialist agent and a verifier agent. The verifier is blind: it gets the ticket and the diff, and never the report of the specialist. The command `bootstrap` writes both agents for your repository.

```bash
delegate bootstrap --repo . --name python \
  --domain "A small Python library with unit tests." \
  --tier standard \
  --skill sample-style \
  --reference docs/agents/delegation.md \
  --gate-command "python3 -m unittest"
```

The option `--gate-command` is important. The verifier may run your gate command, and it may run no other build command.

The command prints the files that it wrote:

- `.claude/skills/python-context/SKILL.md` and `.claude/skills/python-context/agents.toml`. These are the context skill and the declaration.
- `.claude/agents/python-specialist.md` and `.claude/agents/python-verifier.md`. These are the agent pair for Claude Code.
- The same pair for OpenCode, in `.opencode/agents/`.

The workflow file names the agents `python-specialist` and `python-verifier`. These are the names that `--name python` made.

## 5. Commit the sample

The engine builds each ticket in a new git worktree that starts from `main`. The worktree holds only what is committed. Commit the sample files and the agent files now.

```bash
git add -A
git commit -m "Add the sample project and its agents"
```

## 6. Check the plan

Check the workflow file. This command creates no branch, no worktree and no journal.

```bash
delegate run workflow.toml --dry-run
```

The command prints the plan. It has one ticket, and the ticket is ready at once.

## 7. Run the ticket

```bash
delegate run workflow.toml
```

The engine now works on its own. It does these things in order:

1. It makes the run branch `run/sample` and a worktree for ticket 1.
2. It starts the specialist agent in the worktree. The specialist writes the code and the test, and commits them.
3. It runs the gate `python3 -m unittest` itself. The engine does not trust the report of the specialist for this.
4. It starts the verifier agent in a temporary copy of the branch. The verifier gets the ticket text and the diff. It runs the gate again and writes its verdict.
5. If the verdict is ACCEPT, it moves the run branch to the commit that the verifier saw.

The command prints a line `run <run id>` and a line `journal <path>`. It exits 0 when the ticket is accepted.

If the verdict is REJECT, the engine starts a fix-up round. The specialist adds a new commit, and a new verifier checks it. The limit is two rounds. The reference page for `delegate run` has the details.

## 8. Read the result

Show the state of the run:

```bash
delegate status
```

The output looks like this. Your run id and your times are different.

```text
run 20261009T031500Z-a1b2: built (mode assure, adapter claude-code)
journal /path/to/sample/.git/delegate/runs/20261009T031500Z-a1b2/journal.jsonl
ticket 1: state built, verdict ACCEPT, branch run/sample-1, last event 2026-10-09T03:19:02.512384+00:00
```

The state `built` and the verdict `ACCEPT` mean that the ticket is done and checked.

## 9. Read the verdict and the journal

The verifier writes its verdict to a file. Read it:

```bash
cat .git/delegate/runs/*/verifier/1/verdict.json
```

The file holds these things:

- The verdict.
- The evidence for each acceptance criterion.
- The output of the gate.
- The findings.
- The `unverified` list. The verifier lists there what it could not check. Read this list with care.

The journal records each event of the run, in order. Print it:

```bash
delegate watch
```

The command prints one line for each event. The line names the event, for example `step-start`, `gate-result`, `verdict`, `run-branch-advance` and `run-end`. The command ends when it reaches the end of the run. It exits 0 because the run succeeded.

## 10. Take the work

The engine never merges to your base branch. The merge is your action. The run branch holds only accepted work, so a fast-forward is enough.

```bash
git merge --ff-only run/sample
git log --oneline
```

The log now shows the commits of the specialist on `main`.

## What you did

You ran the whole loop once:

- `bootstrap` wrote an agent pair from one interview.
- `run` built the ticket in a worktree, ran the gate outside the specialist, and verified the result with a blind verifier.
- `status` and `watch` showed the state and the journal.
- You merged the branch yourself.

## Next steps

- [The workflow file](reference/workflow.md) shows how to write a workflow with many tickets.
- [The `delegate run` reference](reference/run.md) explains the fix-up round, the resume of a stopped run, and the guards.
- [The reference for `delegate status` and `delegate watch`](reference/status-and-watch.md) lists the exit codes that a coordinator can act on.
