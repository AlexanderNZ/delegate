# Contributing to delegate

This page tells you how to set up the repository, how to write a change, and how to send it. You need no Nix and no help from the maintainer.

## Set up

You need Python 3.11 or later, git, and `jq`. The tests of the guards run the rendered hooks, and the hooks call `jq`. Nix is optional. The repository has a flake for people who use Nix, and you can ignore it.

Run these commands from the root of the repository, in a clean Python environment:

```bash
pip install ./skills/agent-definitions/validator pytest
cd skills/agent-definitions/validator
python -m pytest -rs
```

These are the commands that CI runs, so a green run on your machine is a green run in CI. A test checks that this page and the CI file agree.

One test checks the tree against a private list of terms. The list is not in the repository. Without it, that test skips with a notice, and the rest of the suite runs. You do not need the list.

## Write the test first

Write the failing test before the code that makes it pass. Run the test, and read the failure. The test must fail for the reason that you want to fix. Then write the least code that makes it pass.

A bug fix starts the same way. Write a test that shows the bug, watch it fail, and then fix the bug.

A test drives a public entry point, such as `main(argv)` or the adapter interface. It checks what a user can see: an exit code, a standard output, a file, or the state of git. A test never imports a private function and never checks a log line. A test that needs git uses a real temporary git repository. A mock stands only at a system boundary, such as the harness process.

## Regenerate the docs

Some sections of the reference pages are generated from the code. Do not edit the text between the `generated:begin` and `generated:end` markers. After you change a flag, an exit code, a guard command or a finding code, run this command from the root of the repository. Then commit the pages that it changed:

```bash
delegate docs
```

A test fails when a committed section differs from the code. See [the reference for `delegate docs`](docs/reference/docs.md).

## Date every harness fact

A harness changes from one release to the next. So each fact about a harness carries the version of the harness and the date that you measured it. A fact is a command, an option, an event of a stream, or an end state.

- A reference page states the version and the date next to the facts. See [the `claude-code` adapter reference](docs/reference/claude-code-adapter.md) for an example.
- Each set of recorded streams has a `manifest.json` in `skills/agent-definitions/validator/tests/fixtures/`. The manifest holds the `harness_version` and the `recorded` date of the streams. A test fails when a manifest has no version or no date.
- Write the date as `YYYY-MM-DD`. Do not write a fact that you did not measure. When you cannot measure it, say so on the page.

## Add a harness adapter

Read [how to add a harness adapter](docs/how-to/add-a-harness-adapter.md) first. It shows the interface and an example. Then tick each box before you send the adapter:

- [ ] Measure the headless command of the harness on a real machine. Record one stream for each end state, and a `manifest.json` with the version of the harness and the date.
- [ ] Write the adapter module in `skills/agent-definitions/validator/agent_definitions/`, beside `claude_code.py` and `opencode.py`.
- [ ] Write the contract test in `skills/agent-definitions/validator/tests/`. It replays each recorded stream through a stand-in command. It checks the fields of the result, the model and the agent on the command, and the error for a harness that writes no event.
- [ ] Connect the adapter. Add a branch for its name in `get` in `skills/agent-definitions/validator/agent_definitions/adapters.py`. Add its name to `ADAPTERS` in `skills/agent-definitions/validator/agent_definitions/workflow.py`. Add a column to each tier in `skills/agent-definitions/validator/agent_definitions/tiers.toml`.
- [ ] Write a test that runs `delegate run` with the stand-in. It must show that the model on the command is the model of the tier column of the adapter.
- [ ] Do the live smoke run: run the adapter once against the real harness on a real machine. Write the version of the harness and the date of the run in the reference page.
- [ ] Write the reference page of the adapter in `docs/reference/`. Link it from `README.md`. Run `delegate docs`.
