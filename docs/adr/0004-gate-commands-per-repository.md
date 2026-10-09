# ADR 0004: Gate commands are per repository

- Status: accepted
- Date: 2026-09-26, extended 2026-10-08
- Applies to: the verifier guard, and the `gateCommands` and `getOnlyCommands` keys of a declaration

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

## Rejected alternatives

- **A larger global list.** The decision of 2026-09-26 recorded no separate reason. The extension of 2026-10-08 stated the principle: the global list holds only the commands that every verifier needs.
- **The coordinator runs the gates.** The TypeScript monorepo did this as a fallback: its verifiers read the source, and the coordinator ran the tests. The decision recorded no reason against this option.
- **A GET-only command in `gateCommands`.** A read-only tracker API call without `-X` is a GET-only rule, not a plain gate, and the extension of 2026-10-08 kept that restriction. A gate command is permitted with any arguments, so it cannot carry the ` -X` rule. `getOnlyCommands` carries it.
- **Free shell text in an entry.** The decision makes the parser refuse an entry with shell syntax or a quote, and the skill states the rule: no quote, shell syntax, or glob character may reach the bash `case` pattern or the OpenCode glob. The decision recorded no further reason.

## Consequences

- Each verifier can run the gates of its own repository, and no other gates.
- A gate can write. For example, `npm test` can write `node_modules/` and a cache. The guard reads text and cannot stop that, as with `python3`. The verifier runs the gates in its temp copy of the worktree.
- On OpenCode a verifier cannot run a `getOnlyCommands` entry or a gate in `nix develop -c`. It reports such a command as a command that it cannot run.

## Evidence

- 2026-09-26: the four reports above.
- 2026-09-26: the change and a fix-up, each with a blind verifier ACCEPT. The fix-up added write probes: `nixos-rebuild switch`, `direnv allow`, `git add`, `git reset`, `git stash`, and `sudo`. The tests are in [`tests/test_gate_commands.py`](../../tests/test_gate_commands.py).
- 2026-10-08: the neutral global list and `getOnlyCommands`. Each verifier of the consumer that made the change permits the same set of commands as before. A test compares the sets with a literal of the old list: [`tests/test_neutral_guard.py`](../../tests/test_neutral_guard.py).
