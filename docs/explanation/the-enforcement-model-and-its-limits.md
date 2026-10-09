# The enforcement model and its limits

The kit says that a [**specialist**](../glossary.md#specialist) never pushes, that a [**verifier**](../glossary.md#verifier) never writes to the [**worktree**](../glossary.md#worktree) of the [**ticket**](../glossary.md#ticket), and that only the [**coordinator**](../glossary.md#coordinator) edits a [**hotspot**](../glossary.md#hotspot). This page says how the kit makes those rules hold, and where it stops. A guard deserves trust exactly as far as it goes, and not further.

## What the engine enforces

The [**engine**](../glossary.md#engine) uses tools that every [**harness**](../glossary.md#harness) has: git, a diff and a snapshot. It does not rely on the hooks of a harness.

- **A pre-push hook in each worktree.** The engine makes each worktree itself, and it sets a `pre-push` hook for that worktree only. The hook refuses every push. The main checkout and the other worktrees keep their own hooks.
- **A diff check against the hotspot paths.** After a specialist reports, and before the [**gates**](../glossary.md#gate) run, the engine compares the changed paths with the hotspot patterns of the [**stack**](../glossary.md#stack). A match stops the [**step**](../glossary.md#step). No gate runs and no verifier starts.
- **An unchanged-worktree check around the verifier.** The engine records the HEAD and the status of the real worktree before the verifier runs, and again after. A difference is an [**invariant**](../glossary.md#invariant) violation, and the [**run**](../glossary.md#run) halts, whatever [**verdict**](../glossary.md#verdict) the verifier wrote.
- **Gates outside the specialist.** The engine runs the gates itself. The verifier works in a [**temporary copy**](../glossary.md#temporary-copy) that the engine prepares, so the verifier may break the copy and not the branch.

The reference describes each check in detail. See [the guards in the `delegate run` reference](../reference/run.md#the-guards).

## Why git, and not the harness

Each harness guards in its own way, and some harnesses do not guard at all in a [**headless**](../glossary.md#headless) run. A rule that depends on a [**harness hook**](../glossary.md#harness-hook) is as strong as that hook. In a headless [**session**](../glossary.md#session) the hook may not run.

So the first guard is the same in every harness. A pre-push hook, a diff and a snapshot work with every harness that the engine drives, because they are git and the file system.

The harness hooks in the agent files stay. A harness that supports hooks gets a second guard from them: a [**push guard**](../glossary.md#push-guard) for the specialist, and a command guard for the verifier. I count them as a second line, not as the first.

## What the engine does not stop

- The push hook does not stop `git push --no-verify`, because git skips every `pre-push` hook then. It does not stop a push from a clone, or from another directory. It holds against the plain push command of a specialist in its worktree.
- The unchanged-worktree check covers the worktree of the ticket. It does not cover the main checkout, which a person can change at any time, and it does not cover a path outside the repository.
- The snapshot ignores a file that git ignores. A verifier that writes only to an ignored file in the worktree does not show in the snapshot.
- None of this is a container. A specialist runs as the person who started it, with their permissions, in a worktree. If a policy needs stronger isolation, the kit is not the right tool by itself.

## The limits of the guards

The command guards of the harnesses have limits of their own. The [**skill**](../glossary.md#skill) that renders the agents lists them, and I list every one here, in the order of the skill, with the reason I accept it. The full text is in [the Limits section of the agent-definitions skill](https://github.com/AlexanderNZ/delegate/blob/main/skills/agent-definitions/SKILL.md#limits).

1. No harness composes agent files. A project agent is a complete file, never an extension of a global one, so a project agent must carry everything it needs. Claude Code and Codex replace on a name collision, and OpenCode merges per key, which the [**renderer**](../glossary.md#renderer) does not rely on.
2. Docs cannot be attached by path. I put reference material in a skill and list the skill. On OpenCode, the prompt names it.
3. Both Claude Code guards read the hook input with `python3`, which the kit requires already, so they need no `jq`. They [**fail closed**](../glossary.md#fail-closed): with no `python3`, or with input that holds no command text, the guard blocks the call. See [ADR 0005](https://github.com/AlexanderNZ/delegate/blob/main/docs/adr/0005-guards-read-the-hook-input-with-python3.md).
4. The [**verifier guard**](../glossary.md#verifier-guard) reads the command as text. `python3`, `bash -c`, `env` and `find` are on the allow list because the gates need them, and each of them can write through the guard. It does not parse Python and it does not parse a nested shell. This is a contract for the verifier, not a sandbox. It is why I also check the worktree after the verifier.
5. The verifier guard divides a command at `&&`, `||`, `;` and `|` without respect for quoting. A separator inside a quoted argument divides the command, and the parts that are not commands are denied. The guard fails closed here, so the cost is a lost convenience.
6. The guard uses the bash string operators. Under `bash` and macOS `/bin/sh` it behaves as the table says. Under `zsh` it also denies a temp write, and under `dash` it exits 2 on a syntax error. Each difference is a deny, so the guard stays closed whichever shell runs it.
7. An assignment prefix and a command substitution are not on the allow list. `T=$(mktemp -d)` is denied. The workaround is to run `mktemp -d` and then use a literal path.
8. The destination-flag rule is wider than the flags it must stop, because the guard does not know which flags a command takes. It also denies `cp --preserve=timestamps`, which moves no destination. The copy runs without the flag, so no capability is lost.
9. A temp path is a text prefix and a path with no `..` component. The guard rejects `..`, and does not resolve it, because a resolution needs the file system. A path that needs `..` must be written out in full.
10. The copy allow reads the source and does not examine it. A `cp` source can be any path the agent can read, secrets included, and the copy then sits in a temp directory. The temp directory is the boundary, not the source.
11. The OpenCode verifier map has no `cp` rule, no `cd` rule and no `git -C <path>` rule, because an OpenCode pattern cannot examine a path argument. A wider `git -C` pattern would also match a push in a compound command. See [ADR 0002](https://github.com/AlexanderNZ/delegate/blob/main/docs/adr/0002-narrow-opencode-push-pattern.md). An OpenCode verifier cannot make the copy, and it reports the [**red proof**](../glossary.md#red-proof) as a command it cannot run.
12. The OpenCode verifier allows no `getOnlyCommands` entry, because an OpenCode pattern cannot examine the arguments of a GET-only rule. A [**GET-only command**](../glossary.md#get-only-command) is a deny there. On Claude Code the guard also cannot see an argument that a shell expansion builds.
13. The OpenCode verifier map has no `nix develop` rule, because a pattern cannot test the command after `-c`, and a loose pattern would permit every command in a dev shell. An OpenCode verifier reports a dev-shell gate as a command it cannot run.
14. The OpenCode verifier map does not divide a compound command, for the same reason as the specialist map: OpenCode hands the permission check the raw command. `git status && git commit -m x` is one deny on Claude Code and one allow on OpenCode.
15. Frontmatter hooks in a project-scope agent run only after the workspace trust dialog is accepted for the folder, and a `-p` session does not count as accepting it. A project verifier in a folder that is not trusted has a Bash tool with no guard. The global [**tier**](../glossary.md#tier) is not affected.
16. The two push guards are not equal, and no pattern makes them equal. The Claude Code hook applies an unanchored expression to the raw command. An OpenCode pattern is an anchored glob, so OpenCode allows `git add . && git push`, a push with two spaces, and a push after an environment assignment. I do not widen the OpenCode pattern to `*git push*`. The residual risk is an OpenCode specialist that pushes from inside a compound command. The pre-push hook of the engine closes that gap in a run.
17. Two files with one name in one directory resolve by read order. The renderer refuses to produce that, and the [**validator**](../glossary.md#validator) reports it in hand-written sets.
18. A [**turn cap**](../glossary.md#turn-cap) is a cost stop, not a lost deliverable. At the cap, Claude Code gives only a harness note and the agent id, and the agent's own text is absent. Resuming the agent keeps its context, and the cap applies again on each [**resume**](../glossary.md#resume). OpenCode writes a summary and documents no resume. I set a cap to bound cost, and I resume the agent when it hits the cap.

## How I read the limits

Most of these limits fail closed. The cost is a command that a guard denies and that has to run another way. A few fail open: the OpenCode push patterns, the missing guard in an untrusted folder, and the read of a source path. For those, the guards of the engine are the answer. A pre-push hook, a hotspot diff and an unchanged-worktree check do not depend on the harness, so they cover the places where a harness guard is weak.

I do not claim that the two layers together are a sandbox. They are a posture with evidence: a rule is checked by something other than the agent that must follow it. For the reasons the verifier is blind and the gates run outside the specialist, see [why the verifier is blind](why-the-verifier-is-blind.md).
