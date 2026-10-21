# ADR 0004: Gate commands are per repository

- Status: accepted
- Date: 2026-09-26, extended 2026-10-08, 2026-10-17 and 2026-10-21
- Applies to: the verifier guard, and the `gateCommands`, `getOnlyCommands` and `gateExtraFlags` keys of a declaration

## Context

The verifier half of a pair has a guarded shell. On Claude Code a `PreToolUse` hook permits a list of read commands and of writes into a temp directory. On OpenCode a `permission.bash` map does the same. Before this decision the list was global: each verifier got the same commands.

A verifier must run the gates of its repository. The gates are different in each repository. On 2026-09-26, four repositories reported that their verifiers could not run their gates:

- a repository with a documentation site, Go code, and Node code: `zensical build`, `go test`, `node --test`;
- a TypeScript monorepo: `npm` and `npx`. Its verifiers read the source in place of a test run, and the coordinator ran the tests;
- a .NET game: `dotnet test`;
- a Nix configuration on a Linux host: `nixos-rebuild build`.

A gate can also run in a wrapper that loads the environment of the repository: `nix develop -c` or `direnv exec <dir>`.

## Decision

On 2026-09-26: gate commands are per repository. The global list does not grow.

- A declaration names its gates in `gateCommands`. `bootstrap` takes them as `--gate-command`, one flag for each gate. The values come from the verification gates in the `docs/agents/delegation.md` of the repository.
- On Claude Code, the verifier guard permits each gate command as it permits a read command: alone or with arguments, in the verifier of that repository only. The specialist guard does not change.
- On OpenCode, each gate command becomes the rule `<command> *`: allow, in the verifier map.
- In each verifier, the guard removes a `direnv exec <dir>` prefix before the test. `direnv allow` stays a deny.
- The parser refuses an entry that holds shell syntax, a quote, or a glob character. It also refuses an entry that opens a write: a temp-write or temp-destination command, or a prefix of a known write (`git`, `git push`, `darwin-rebuild switch`).

On 2026-10-08 the decision was extended in two parts:

- The global list lost its build tools and its tracker CLI. It holds only the commands that every verifier needs. A consumer that needs a build tool or a tracker CLI names it in its declaration.
- A declaration can name `getOnlyCommands`: a command that reads only while it stays a GET, for example a tracker API client that writes with `-X POST`. The Claude Code guard permits it in that verifier only, and it denies a segment that holds ` -X`. On OpenCode the entry is not permitted, because a pattern cannot examine the arguments.

On 2026-10-17 the decision was extended for issue #75. The guard now denies a command that holds a substitution, because it cannot examine the command in the substitution. A gate such as `uvx "zensical==$(cat .zensical-version)" build --strict` holds one. `gateCommands` therefore also takes an exact gate: plain words, and one or more words in double quotes that hold one `$(...)` of plain words. The first word stays plain, and the command in the `$(...)` must not open a write. The guard permits an exact gate only as the full command, as a literal in single quotes. The OpenCode map does not hold an exact gate.

On 2026-10-21 the decision was extended for issue #163. A gate with any arguments can write: `npm run lint -- --fix` rewrites source files, `npm test -- -u` updates snapshots, `npx tsc --outDir <dir>` writes in that directory, and `./gradlew test spotlessApply` runs a task that formats the source. The Claude Code guard now permits after a plain gate only a path that exists in the git toplevel of the working directory or in a temp directory, the separator `--`, a redirection, and a flag of the new key `gateExtraFlags`. A declaration lists there the flags that cannot make its gates write. An entry `"<flag> <value>"` also permits the next word as the value of the flag, unless that word starts with `-`: the guard tests such a word as a word of its own, because a parser such as yargs (Jest) takes no such word as a value. Each other word denies. On OpenCode each plain gate becomes the rule `<command>` with no ` *`, because a pattern cannot tell a path from a word that writes.

On 2026-10-21 the decision was extended for issue #164. A `getOnlyCommands` entry was a prefix too, so with the entry `tea issues` the guard permitted `tea issues create`, which writes to the issue tracker. The Claude Code guard now permits after the words of an entry only options, the word after an option (its value), numbers, paths (a word with a `/`) and redirections. Each other word can be a subcommand, and it denies. An entry that ends with ` *`, for example `tea issues list *`, is a prefix on purpose: the guard permits each word after it, and the rendered verifier states that the entry is a prefix. OpenCode permits no `getOnlyCommands` entry, as before.

On 2026-10-21 the decision was extended for issue #169, before the release of #163. The check of #163 refused real gate commands, and a declaration had no way to permit them: a flake ref is not a path (`nix eval --raw .#x`), a task name is a bare word (`./gradlew test jacocoTestReport -x processTestAot`), and an issue number is not a path (`tea issues 226`). A gate that ends with ` *`, for example `./gradlew *`, now takes free arguments: the Claude Code guard permits each word after it, as it did for each gate before #163, and the segment rules for redirections, temp writes and git trees still apply. The guard does not check these words, so the rendered verifier names each such gate and states the rule of #124 and #163: do not put a word after such a gate that can make the gate write. A gate with no ` *` keeps the check of #163, and a number (all digits) is now permitted after it too. `gateExtraFlags` stays declaration-wide. On OpenCode a ` *` gate becomes the rule `<command> *`, and a gate with no ` *` stays exact.

## Rejected alternatives

- **A larger global list.** The decision of 2026-09-26 recorded no separate reason. The extension of 2026-10-08 stated the principle: the global list holds only the commands that every verifier needs.
- **The coordinator runs the gates.** The TypeScript monorepo did this as a fallback: its verifiers read the source, and the coordinator ran the tests. The decision recorded no reason against this option.
- **A GET-only command in `gateCommands`.** A read-only tracker API call without `-X` is a GET-only rule, not a plain gate, and the extension of 2026-10-08 kept that restriction. A gate command is permitted with any arguments, so it cannot carry the ` -X` rule. `getOnlyCommands` carries it.
- **A `gateExtraFlags` list for each gate (#169).** A list of flags for each gate does not permit a task name, a flake ref or a subcommand, because these words are not flags. The ` *` form permits them, and the verifier text carries the rule for them. So `gateExtraFlags` stays one list for the declaration.
- **Free shell text in an entry.** The decision makes the parser refuse an entry with shell syntax or a quote, and the skill states the rule: no quote, shell syntax, or glob character may reach the bash `case` pattern or the OpenCode glob. The decision recorded no further reason.

## Consequences

- Each verifier can run the gates of its own repository, and no other gates.
- A gate can write. For example, `npm test` can write `node_modules/` and a cache. The guard reads text and cannot stop that, as with `python3`. The verifier runs the gates in its temp copy of the worktree.
- On OpenCode a verifier cannot run a `getOnlyCommands` entry or a gate in `nix develop -c`. It reports such a command as a command that it cannot run.

## Evidence

- 2026-09-26: the four reports above.
- 2026-09-26: the change and a fix-up, each with a blind verifier ACCEPT. The fix-up added write probes: `nixos-rebuild switch`, `direnv allow`, `git add`, `git reset`, `git stash`, and `sudo`. The tests are in [`tests/test_gate_commands.py`](../../tests/test_gate_commands.py).
- 2026-10-08: the neutral global list and `getOnlyCommands`. Each verifier of the consumer that made the change permits the same set of commands as before. A test compares the sets with a literal of the old list: [`tests/test_neutral_guard.py`](../../tests/test_neutral_guard.py).
