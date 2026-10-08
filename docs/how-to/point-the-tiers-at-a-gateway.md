# How to point the tiers at a gateway with your own tier file

Use this page when your company gateway serves models under its own names. A workflow names strengths: `strong`, `standard`, `cheap`, and `verifier`. A tier table maps each strength to one model for each harness. You write your own tier file, and the engine uses the models in it. You change no workflow.

## Before you start

- Know the model name that each harness sends to your gateway. Test it with the harness before you write the file.
- Have a workflow. See [how to bootstrap a single-stack repository](bootstrap-a-single-stack-repository.md).

## Steps

### 1. Write the tier file

Copy `tiers.toml` from the package of the kit. Change the model of each tier in the column of each harness that you use. Keep the other tables.

```toml my-tiers.toml
[effort]
levels = ["low", "medium", "high", "xhigh", "max"]

[effort.opencode]
variants = ["high", "max"]

[tiers.strong]
claude-code = "gateway/large-model"
opencode = "gateway/large-model"

[tiers.standard]
claude-code = "gateway/medium-model"
opencode = "gateway/medium-model"

[tiers.cheap]
claude-code = "gateway/small-model"
opencode = "gateway/small-model"

[tiers.verifier]
claude-code = "gateway/verifier-model"
opencode = "gateway/verifier-model"

[allowed-models]
claude-code = [
  "gateway/large-model", "gateway/medium-model", "gateway/small-model", "gateway/verifier-model", "inherit",
]
opencode = [
  "gateway/large-model", "gateway/medium-model", "gateway/small-model", "gateway/verifier-model",
]

[budget]
claude-code-description-chars = 48000
```

The table has one column for each harness, named by the harness. Each adapter reads its own column, and never chooses a model. The list `allowed-models` holds the models that the validator accepts for rendered agent files. Put each model of your gateway in it.

### 2. Name the workflow

The workflow names tiers only. A model name in a workflow is an error.

```toml workflow.toml
base-branch = "main"
run-branch = "run/demo"
mode = "assure"
adapter = "claude-code"

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = ["python3 -m unittest"]
hotspots = ["workflow.toml", "my-tiers.toml"]

[[tickets]]
id = "1"
text = "The export command writes a CSV file."
stack = "python"
blocked-by = []
```

### 3. Check the file and run

Give the file with `--tiers`, after `run`. The check reports a tier file that is not valid, and it builds nothing.

```bash
delegate run workflow.toml --tiers my-tiers.toml --dry-run
delegate run workflow.toml --tiers my-tiers.toml
```

In `assure` mode the specialist uses the tier `strong`, and the verifier uses the tier `verifier`. The engine asks the harness for `gateway/large-model` for the specialist, and for `gateway/verifier-model` for the verifier. In `economy` mode the specialist uses the tier `standard`. See [the modes](../reference/workflow.md#modes).

## Other commands

`--tiers` also changes the other commands that read the tier table. For `render`, `validate` and `bootstrap`, put it before the subcommand, as in `delegate --tiers my-tiers.toml validate`. The commands `brief`, `status`, `watch` and `docs` refuse it. See [the options before the subcommand](../reference/commands.md#options-before-the-subcommand).

To change only the OpenCode column, use `--opencode-model` and `--opencode-allow` and no file. See [the model of the OpenCode adapter](../reference/opencode-adapter.md#the-model).
