# Delegation: this repository's gates and rules

This document holds the values that the `agent-delegation` skill asks each repository for. The skill is in `skills/agent-delegation/SKILL.md`. These rules bind every agent that works in this repository.

## Domain and stacks

The repository holds a harness-neutral delegation kit. It has one stack: a Python 3.11+ package (`skills/agent-definitions/validator/`) with a pytest suite, two skills as plain Markdown directories, and a Nix flake that is optional.

## Verification gates

```bash
cd skills/agent-definitions/validator && nix develop -c python3 -m pytest -rs
nix flake check
```

Without Nix, the same suite runs with `pip` and `pytest`: in `skills/agent-definitions/validator`, run `pip install . pytest` into a Python 3.11+ environment, then `python -m pytest -rs`.

The neutrality check needs `AGENT_DEFINITIONS_DENYLIST`, the path of the private denylist file. Without that variable, the neutrality test skips with a notice, and the rest of the suite runs. CI gives the variable from a secret.

## Test quality bar

- A test drives `main(argv)` or the adapter interface, and asserts an observable outcome: the exit code, the stdout, the files written, or the git state.
- A test that needs git uses a real temporary git repository.
- A test never imports a private function and never asserts on a log line. A mock exists only at a system boundary.
- A bug fix starts with a test that fails for the reported reason.

## Breakage

Red CI on `main` halts delegation. The coordinator makes `main` green before a new task starts.

## Hotspots (coordinator only)

Only the coordinator changes these paths:

- `LICENSE`
- `.github/workflows/`
- `flake.lock`
- `docs/agents/delegation.md`
- `AGENTS.md`
- `CLAUDE.md`
- `skills/agent-definitions/neutrality-allowlist.txt`

## Read first

1. `AGENTS.md`
2. This document
3. The spec, issue #1
4. The ticket in the brief

## Tracker

The tracker is GitHub issues on this repository. This is the process of this project only. The kit itself needs no tracker.

## Output language

Agent-written text (reports, commit messages, comments, docs that agents write) is in ASD-STE100 Simplified Technical English.
