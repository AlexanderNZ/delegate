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

Some sections of the reference pages are generated from the code. Do not edit the text between the `generated:begin` and `generated:end` markers. After you change a flag, an exit code, a guard command or a finding code, run this command from the root of the repository, and commit the pages that it changed:

```bash
delegate docs
```

A test fails when a committed section differs from the code. See [the reference for `delegate docs`](docs/reference/docs.md).
