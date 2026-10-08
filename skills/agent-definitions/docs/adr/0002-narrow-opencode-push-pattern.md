# ADR 0002: The OpenCode push guard stays narrow

- Status: accepted
- Date: 2026-09-19
- Applies to: the `permission.bash` map of a rendered OpenCode specialist, and the `git -C` rules of the OpenCode verifier map

## Context

A specialist must not push. Each harness has its own guard.

- **Claude Code.** A `PreToolUse` hook on Bash applies an unanchored regular expression to the raw command text. It stops `git push`, `git -C <path> push`, a push in a compound command (`git add . && git push`), a push with two spaces (`git  push`), and a push after an environment assignment (`GIT_DIR=/x git push`).
- **OpenCode.** The guard is a `permission.bash` map of glob patterns. A pattern is anchored at the two ends: `git push*` compiles to `^git push.*$`. OpenCode v1.18.31 gives the permission check the raw command. It does not divide the command at `&&` (`packages/core/src/tool/bash.ts`, which holds a TODO for a tree-sitter parser).

The first OpenCode map had one deny, `git push*`. That deny did not stop `git -C <path> push`. A second deny, `git -C * push*`, closed that gap. Three forms still pass on OpenCode: the compound command, the two spaces, and the environment assignment. A pattern with a wildcard at the start, `*git push*`, stops two of the three.

## Decision

- The OpenCode specialist map keeps two denies after the catch-all allow: `git push*` and `git -C * push*`.
- The renderer does not add `*git push*`.
- The validator finding `SPECIALIST_CAN_PUSH` requires a deny for `git push`, `git push origin main`, and `git -C /tmp/x push`.
- The skill records the forms that pass on OpenCode, under Limits.
- The same rule applies to the OpenCode verifier map. It gets no `git -C * <read>` rule, because `git -C * diff*` compiles to `^git -C .* diff.*$`, and that pattern also matches `git -C /x push; git diff`.

The push-pattern decision was recorded with no separate reason. The table under Evidence shows what the wider pattern changes.

## Rejected alternatives

- **Add `*git push*` to the two denies.** It stops the compound command and the environment assignment. It still allows `git  push` with two spaces, and `git -c <key>=<value> push`, so the two guards stay unequal. It also denies each command whose text holds the words `git push` at any position, for example a `grep` for the words or a `git log --grep`. The Claude Code hook denies these reads too, because it also reads the raw text.
- **Replace the two denies with `*git push*`.** The wide pattern alone does not stop `git -C <path> push`, because the words `git` and `push` are not adjacent in that command.

## Consequences

- An OpenCode specialist can push from inside a compound command, with two spaces, or after an environment assignment. This risk is on OpenCode only. The skill names it under Limits.
- Review this decision if a later OpenCode version divides a compound command before the permission check. The narrow pattern then applies to each segment of the command.

## Evidence

- 2026-09-19: a review of the first OpenCode specialist map found the anchored compile of `git push*` and the missing `git -C` case. The second deny and the finding `SPECIALIST_CAN_PUSH` came in on the same day, with a test for each harness.
- 2026-09-19: the source of OpenCode v1.18.31 was read: `packages/core/src/tool/bash.ts` passes the raw command to the permission check.
- 2026-09-19: the test `test_the_two_harnesses_are_not_equal_on_compound_commands` in [`validator/tests/test_render.py`](../../validator/tests/test_render.py) runs the Claude Code hook and the OpenCode map on the three forms. The hook stops each one. The map allows each one.
- 2026-09-19: the Claude Code verifier got a `git -C <path> <read>` rule. The OpenCode verifier map stayed unchanged, because `git -C * diff*` also matches `git -C /x push; git diff`, and this decision forbids that widening.
- 2026-10-08: the Claude Code specialist hook denied a `grep` command whose pattern held the words `git push`. No push was in the command.
- 2026-10-08: the table below comes from `opencode_bash_action` in [`validator/agent_definitions/validate.py`](../../validator/agent_definitions/validate.py). That function models the rule order and the glob of OpenCode. It is a model, not a run of OpenCode.

| Command | Current map | Current map plus `*git push*` | `*git push*` only |
|---|---|---|---|
| `git push origin main` | deny | deny | deny |
| `git -C /repo push` | deny | deny | allow |
| `git add . && git push` | allow | deny | deny |
| `GIT_DIR=/x git push` | allow | deny | deny |
| `git  push` (two spaces) | allow | allow | allow |
| `git -c a=b push` | allow | allow | allow |
| `grep -rn 'git push' docs/` | allow | deny | deny |
| `git log --grep='git push'` | allow | deny | deny |
| `echo do not git push` | allow | deny | deny |
