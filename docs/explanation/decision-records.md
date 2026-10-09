# The decision records

Each design decision of this kit is an architecture decision record, an ADR. An ADR is a short file that gives the context of a decision, the decision, and its consequences. I keep them in the repository so that a contributor can read the reason for a rule without access to a private tracker. A code comment gives its reason inline, or it cites an ADR.

The ADRs are in `docs/adr/`. They record decisions about the whole kit, so they sit in the documentation and not inside one skill. The site does not build them as pages. This page is the index, and each entry links to the file on GitHub. A test fails when an ADR exists that this page does not list, so the index cannot fall behind.

## The index

- [ADR 0001: Effort follows the model](https://github.com/AlexanderNZ/delegate/blob/main/docs/adr/0001-effort-follows-the-model.md): a rendered Claude Code agent has no `effort` key unless the declaration sets one, because the effort comes with the model.
- [ADR 0002: The OpenCode push guard stays narrow](https://github.com/AlexanderNZ/delegate/blob/main/docs/adr/0002-narrow-opencode-push-pattern.md): the OpenCode specialist keeps two push denies, and the renderer does not widen them to a pattern that would also match other commands.
- [ADR 0003: The caller can override the OpenCode model column](https://github.com/AlexanderNZ/delegate/blob/main/docs/adr/0003-opencode-model-override.md): `render` and `validate` take flags that change the OpenCode column of the tier table, and `bootstrap` takes none.
- [ADR 0004: Gate commands are per repository](https://github.com/AlexanderNZ/delegate/blob/main/docs/adr/0004-gate-commands-per-repository.md): the global verifier guard list does not grow. A repository names its own gates, and they are the only extra commands its verifier may run.
- [ADR 0005: The Claude Code guards read the hook input with python3](https://github.com/AlexanderNZ/delegate/blob/main/docs/adr/0005-guards-read-the-hook-input-with-python3.md): both guards read the command with `python3`, so they need no `jq` and they fail closed.

## How to read an ADR

Each ADR opens with a status, a date and the part of the kit that it applies to. The date matters. A harness fact in an ADR is true on the day I measured it, and a new version of the harness can change it. When a decision depends on such a fact, the ADR names the version and the date.

Some ADRs set limits that you meet again in [the enforcement model and its limits](the-enforcement-model-and-its-limits.md). ADR 0002 and ADR 0005 do.

## How to add an ADR

Write the next number, a short name, and the sections Context, Decision and Consequences. Then add the ADR to the index above. The test names the ADR that is missing from the index.
