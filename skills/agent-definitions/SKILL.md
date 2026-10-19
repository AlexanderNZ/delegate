---
name: agent-definitions
description: Render a specialist-verifier agent pair for Claude Code and OpenCode from one declaration, and validate rendered agent files against each harness's real schema. Use when adding or changing a global agent declaration, when the agent-delegation bootstrap emits a repo's project agents, or when an agent file needs checking.
---

# Agent definitions

One declaration, two harnesses, two files each. A pair is a specialist and a verifier that share a domain, skills, and references. The verifier is read-only and runs on the verifier tier. Neither half is hand-written.

The decisions that shaped the design are ADRs in [`docs/adr/`](../../docs/adr/). The harness facts come from the vendor documentation, read on 2026-09-19: the [Claude Code subagent docs](https://code.claude.com/docs/en/sub-agents), the [OpenCode agent docs](https://opencode.ai/docs/agents/), and the OpenCode config schema at `https://opencode.ai/config.json`. Codex is out of scope.

## What a declaration is

```toml
[agents.java-spring]
description = "Spring Boot backend work: controllers, services, JPA, transactions, RFC 7807 errors."
tier = "standard"          # strong | standard | cheap; the verifier half always uses the verifier tier
skills = ["clean-ddd-hexagonal", "api-design-principles", "tdd", "test-quality"]
maxTurns = 60              # optional
references = ["docs/architecture.md"]   # optional read-first paths, listed in the body
prompt = "Domain notes the agent needs."
# effort = "high"          # optional. Omit it. See "Effort" below.
# pair = false             # a single agent instead of a pair
# readOnly = true          # with pair = false: render the single agent as a verifier
# gateCommands = ["npm test", "npx vitest"]   # optional: the gates the verifier may run (see "Gate commands")
# getOnlyCommands = ["./scripts/api"]          # optional: commands the verifier may run only without a method, body or output flag (see "Gate commands")
# outputLanguage = "ste"   # optional: "none" (the default) or "ste" (see "Rendering rules")
# trackedFileBuild = true  # optional: the build reads tracked files only, as a flake does (default false)
```

The full example is `examples/java-spring.toml` in the repository.

## Commands

```bash
agent-definitions render examples/java-spring.toml -o /tmp/agents
agent-definitions validate /tmp/agents --skills-dir skills
```

`render` writes `<out>/claude-code/<name>.md` and `<out>/opencode/<name>.md`. `validate` exits 1 with one line per finding, or prints `ok`. Validate any directory of agent files, rendered or hand-written: `--claude-code DIR`, `--opencode DIR`.

`render` and `validate` take two more optional flags, both repeatable, that override the OpenCode column of the tier table ([ADR 0003](../../docs/adr/0003-opencode-model-override.md)).

```bash
agent-definitions render DECL -o /tmp/agents \
  --opencode-model strong=gateway/claude-opus-5 \
  --opencode-allow gateway/claude-opus-5
```

| Flag | Effect |
|---|---|
| `--opencode-model TIER=MODEL` | TIER uses MODEL in the OpenCode column. A tier left out keeps its `tiers.toml` value, and the Claude Code column never moves. An unknown tier name stops the command |
| `--opencode-allow MODEL` | adds MODEL to the OpenCode allowed set. It extends the set; the defaults stay allowed |

Give both, or the render carries a model the validator reports as `BAD_MODEL`. They are separate on purpose: a tier map that widened the allowed set by itself would accept whatever it named, so an override onto a model that no provider serves would stay green.

Use them where the provider is not the one `tiers.toml` names. OpenCode resolves a model through a provider, and a provider can belong to a job or a machine, while the tier table is global. The caller is therefore the configuration that sets up the provider, for example a work profile. When that configuration is off, the caller passes neither flag, and the defaults come back. `bootstrap` does not take these flags, because it writes a committed render that carries the defaults. Pass neither flag and every command behaves as it did before.

The same package ships a second command, `verifier-brief`. It prints a verifier's brief on stdout. A third subcommand, `bootstrap`, writes a whole repository's agent set; the next section documents it.

```bash
verifier-brief full  --repo PATH --branch NAME [--base main] --task 'TEXT or @FILE' [--stack HEADING ...]
verifier-brief fixup --repo PATH --branch NAME --rejected SHA --findings FILE [--authorised TEXT] [--stack HEADING ...]
```

| Mode | Sections it prints |
|---|---|
| `full` | `## Task`, `## Diff` (`git diff <base>...<branch>`, three-dot), `## Gates` |
| `fixup` | `## Findings under verification` (the file, verbatim), `## Coordinator-authorised additions` (only with `--authorised`), `## Delta` (`git diff <rejected>..<branch>`), `## Gates` |

The gates come from `docs/agents/delegation.md` in the repository's working tree. Both modes use the same selection. The selection exists because the first block is not always the correct block: in a monorepo, a brief for a docs-only change once carried the backend test suite.

- **One block for each stack.** A sub-heading under the "Verification gates" heading that names a path in backticks claims that path, for example `` ### Backend (`api/`) ``. The block of a sub-heading is the first fenced block before the next heading. A path claims the file of that name and each file below it as a directory: `api/` claims `api/Main.java`, not `apiary/x`.
- **The changed files decide.** The brief carries each block that claims a changed file, in the order of the doc, each under its own `###` heading. `full` reads the changed files from `git diff --name-only <base>...<branch>`, and `fixup` reads them from the delta, `git diff --name-only <rejected>..<branch>`.
- **`--stack HEADING` overrides the path match.** It selects the block under that sub-heading. The name is compared without backticks, so `--stack 'Backend (api/)'` selects `` Backend (`api/`) ``. A sub-heading that claims no path, such as "Contract drift", is selected only this way. Give the flag again for a second block. An unknown name exits 1 and names the known headings.
- **A change that no block claims** gets a placeholder line that tells the verifier to ask the coordinator for the gates. It never gets the first block, because the first block belongs to another stack.
- **A doc with no path claim** keeps the earlier behaviour, byte for byte: the first fenced block under the "Verification gates" heading. A repository without that document, that heading, or that block gets a placeholder line instead.

Exit conditions. `full` exits 1 when the three-dot diff is empty. `fixup` exits 1 when the rejected commit is not an ancestor of the branch tip, which means the specialist amended or rebased, and when the delta is empty, which means the tip is the rejected commit. Both modes exit 1 on a `--stack` name that no sub-heading has. A failed run prints the reason on stderr and nothing on stdout, so a coordinator cannot pass an empty brief to a verifier by mistake.

Why a command and not a prose instruction: the mode of the brief sets the mode of the verifier (below), and the diff form decides what the verifier reads. Both are easy to get wrong by hand and impossible to see afterwards.

The package lives in `src/delegate/`, and its tests in `tests/`. Its pytest suite runs in `nix flake check` as the `tests` check, which builds the package with `runTests = true`. The package itself runs no tests. The suite needs `git` and `pytest-xdist`, two `nativeCheckInputs` in `package.nix`. The guard tests run the rendered hooks with `python3` as the only tool on `PATH`, so the suite needs no `jq`.

## Bootstrap

One command writes a repository's context skill, its declaration, and its
rendered pair from arguments. It replaces the six manual steps in the
`agent-delegation` skill. The reason: context skills written by hand drift from
each other and from the worked example, and a later fix to their shape reaches
none of them.

```bash
agent-definitions bootstrap --repo PATH --name NAME --domain TEXT --tier TIER \
  --skill NAME [--skill NAME ...] --reference PATH [--reference PATH ...] \
  [--prompt-file FILE] [--max-turns N] [--gate-command CMD ...] [--get-only-command CMD ...] \
  [--output-language none|ste] [--tracked-file-build] [--skills-root DIR] [--dry-run]
```

It writes six files.

| Path | Content |
|---|---|
| `<skills-root>/<name>-context/SKILL.md` | one live-read section for each reference |
| `<skills-root>/<name>-context/agents.toml` | the `[agents.<name>]` declaration |
| `<repo>/.claude/agents/<name>-{specialist,verifier}.md` | the Claude Code pair |
| `<repo>/.opencode/agents/<name>-{specialist,verifier}.md` | the OpenCode pair |

- **`--skills-root` defaults to `.claude/skills`**, the only path Claude Code reads for a project skill. It is also the `--skills-dir` of the validation pass. A repository that keeps its skills in a different directory passes that directory and makes the `.claude/skills/` symlink by hand (the `agent-delegation` skill, § Then emit the repo's agents, in the same pass); the command makes no symlink.
- **A reference carries its note.** The value lands in the declaration as it is given, so `` --reference '`docs/operate.md` — the component map' `` is one argument. The path is the first backticked word. A value with no backtick is the path itself. The command exits 1 and names the path when the path is not in the repository.
- **The skills list opens with `<name>-context`**, and each `--skill` follows in the order given.
- **The command renders into a temporary directory and validates it**, never the harness directory. On a finding it prints each finding, exits 1, and writes no agent file. The skill and the declaration are on disk before that pass, because the validator reports `MISSING_SKILL` for a context skill it cannot find.
- **`--dry-run` prints every file with its content and writes nothing.** It does not validate: the context skill is not on disk, so the pass would report a skill that the real run creates.
- **`--output-language` and `--tracked-file-build` write the two neutral declaration options.** `--output-language ste` writes `outputLanguage = "ste"`, and the rendered pair then carries the ASD-STE100 rule. `--output-language none` writes `outputLanguage = "none"`. Any other value exits 1 and names the permitted values, `none` and `ste`. `--tracked-file-build` writes `trackedFileBuild = true`, and the rendered Claude Code specialist then carries the flake reason (see "Rendering rules"). Without a flag the declaration holds no such key, and the declaration, the context skill, and the rendered pair are the same bytes as before the flags existed.
- **`--get-only-command` writes `getOnlyCommands`**, as `--gate-command` writes `gateCommands`. Repeat it for each command. The entry rules are the rules of "Gate commands". Without the flag the declaration holds no `getOnlyCommands` key.
- **A second run with the same arguments writes the same bytes.** A run with different arguments replaces all six files. The command never changes `docs/agents/delegation.md`.

Each generated file opens with a comment that names the command to re-run, with `--repo .` in place of the path of one machine. The command names each option flag that the run used, so a re-run from the comment writes the same bytes. In the skill that comment is below the frontmatter, because Claude Code reads frontmatter from line 1 and from nowhere else.

A live-read block is one plain `git grep` command. Claude Code runs a load-time block only when it can analyse the command. The earlier compound block (`R=$(...); if ...; fi`) stopped a subagent from starting in the manual, acceptEdits and auto permission modes (measured on Claude Code 2.1.283, 2026-09-26). No Claude Code documentation states this rule: it is a measured behaviour. [ADR 0006](../../docs/adr/0006-load-time-blocks-are-one-plain-command.md) records the measurements and their source, the issue tracker of a consumer repository, issues 171, 145 and 196. The generated skill carries the same note. bootstrap counts the lines of each reference when it writes the skill: a document of at most 200 lines gets `git grep --untracked --no-exclude-standard -h -E -m 200 -e '^' -- :/<path>`, and a longer one gets its heading index, `git grep --untracked --no-exclude-standard -h -n -E -e '^#{1,6} ' -- :/<path>`, with a note that gives the `sed -n` command for one section. A reference must be a file; a directory exits 1.

The `:/` pathspec names the file from the top of the working tree, so a block reads the same file from the repository root and from each subdirectory. In a linked worktree it reads the files of that worktree, uncommitted changes included. An earlier form of the block used a path relative to the working directory, and a continuation spawned from `app/` stopped in about 250 ms. `--untracked` with `--no-exclude-standard` also reads a file that is not in the index and a file that `.gitignore` names, so a block reads any file on disk, as the `sed` block did. `-E` sets the pattern type, because `grep.patternType` in a git config changes the default. A block needs no shell syntax and no assignment prefix, so it stays one command that Claude Code can analyse. The context skill is for a git repository: outside one, each block returns an error. A block that exits with no output can also mean that its reference file is missing or renamed.

The two templates are `definitions/templates/context-skill.md` and `definitions/templates/context-skill-section.md`, data in the package like the agent templates. Both are `string.Template` files, so each dollar sign the generated text needs is written twice: `$$(...)` in `context-skill.md` is `$(...)` in the skill.

## Verifier modes

A rendered verifier reads its mode from the shape of its brief.

| The brief | Mode | The verifier |
|---|---|---|
| has a `## Findings under verification` section | fix-up | verifies each finding with evidence, confirms the delta touches only the findings and any `## Coordinator-authorised additions`, runs the gates and the first pass's automated checks, stops |
| has no findings section | full | uses the full method: reads the whole diff, checks every acceptance criterion |

In fix-up mode the verifier does not read the full diff again. It does not repeat the cases the first pass broke by hand. A delta that goes past the findings is a scope finding for the coordinator. The verifier reports it and stays in fix-up mode. The coordinator decides whether a full pass follows.

The first line of every verifier report names the mode.

A rendered specialist carries the matching rule: a fix-up after a rejection is a new commit on top of the rejected commit, never an amend. An amend destroys the delta, and `verifier-brief fixup` then refuses to build a brief.

The mode is in the template and in a generated brief because a rule in each brief did not stop fix-up verifiers from verifying the whole diff again. The coordinator's side of the protocol is in the `agent-delegation` skill, § Fix-up protocol.

## Rendering rules

| Rule | Claude Code | OpenCode |
|---|---|---|
| Model | omitted; the spawn call sets it | the tier's model from `tiers.toml`; OpenCode sets nothing at spawn |
| Effort | omitted unless declared; follows the model through `modelSettings.<model>.effortLevel` ([ADR 0001](../../docs/adr/0001-effort-follows-the-model.md)) | `variant`, only when the declared effort is an OpenCode variant (`high`, `max`) |
| Skills | `skills:` preloads them | cannot preload; the body tells the agent to load them with the skill tool |
| Verifier read-only | `tools: Read, Grep, Glob, Bash, Write`, plus a `PreToolUse` guard hook on Bash and one on Write | `permission` with `"*": deny` first, then the read tools, then a `bash` pattern map that opens with its own `"*": deny`, an `edit` map that allows only `../*`, and an `external_directory` map that allows only the temp prefixes |
| Specialist guardrails | `isolation: worktree`; a `PreToolUse` hook on Bash blocks `git push` (exit 2) | `permission.bash` with `"*": allow` first, then `git push*: deny` and `git -C * push*: deny`; the catch-all must come first or the denies are shadowed. Two denies, because an OpenCode pattern is an anchored glob and `git push*` does not cover `git -C <path> push` |
| Turn cap | `maxTurns` | `steps` |
| `outputLanguage` | `"ste"` opens the Report section of every agent file with "Write in ASD-STE100 Simplified Technical English."; `"none"` (the default) adds no rule | the same |
| `trackedFileBuild` | `true` adds "An unstaged file is invisible to a flake." after the specialist's staging rule; `false` (the default) leaves the rule alone | no change: the specialist states the staging rule without a reason |

Why the hook and not a denylist: a `disallowedTools: Bash(git push *)` entry removes Bash entirely. Why `tools` and not `permissionMode: plan`: permission mode is ignored when the parent session runs in auto mode, and a coordinator session can run in that mode. Why deny and never ask for the OpenCode verifier: `--auto` turns every `ask` into `allow`.

## The verifier's shell

Read-only means "no write outside a temp directory, and no write inside a git tree". It does not mean "no shell". A verifier that cannot run a command cannot run the gates, and the coordinator then does the verifier's work.

On Claude Code the verifier gets `tools: Read, Grep, Glob, Bash, Write`, a `PreToolUse` hook on `Bash`, and a `PreToolUse` hook on `Write` (see [The Write tool](#the-write-tool)). The hook is one bash line. It reads `.tool_input.command` with `python3`, turns each tab into a space, divides the command at `&&`, `||`, `;`, `|` and a newline outside quotes, and tests each segment. A segment that is not on the list exits 2, and the message names that segment. It fails closed: no `python3`, no command text, a quote that is not closed, or a shell without the bash string operators all end at exit 2. The hook carries the marker `# agent-definitions:verifier-guard`, which is what the validator looks for.

<!-- generated:begin verifier-guard-commands -->
<!-- Generated by `delegate docs`. Do not edit this section by hand. -->

| Group | Commands |
|---|---|
| git reads | `git diff`, `git log`, `git show`, `git status`, `git ls-files`, `git ls-tree`, `git rev-parse`, `git merge-base`, `git worktree list`, `git branch --show-current` |
| shell reads | `ls`, `cat`, `head`, `tail`, `sed -n`, `grep`, `rg`, `find`, `wc`, `diff`, `jq`, `shasum`, `stat`, `readlink`, `file`, `which`, `env`, `pwd`, `echo`, `printf`, `date`, `mktemp`, `python3`, `bash -c` |
| temp writes | `mkdir`, `mv`, `rm`, `tee`, `touch` — each one only when every path argument starts with `/tmp/`, `/private/tmp/`, `$TMPDIR`, or `/var/folders/`. A redirection target obeys the same rule, in every segment, but it can also be exactly `/dev/null`, which writes no file: `2>/dev/null` is permitted. |
| temp destination | `cp`, `cd` — each one only when the last path argument is a temp path. The earlier path arguments are sources, and a read of any path is a read. A `cd` takes one path argument, and it may also go to a directory in the git toplevel of the working directory, with no `..` component: `cd api && ./gradlew test`. |
| Write tool | `Write` — only when `file_path` is an absolute path that starts with `/tmp/`, `/private/tmp/`, or `/var/folders/`, or with the value of `$TMPDIR` in the environment of the hook, and has no `..` component. |
| git trees | No temp write, no `cp` destination, no redirection target, no output path and no `Write` may be inside a git tree, or hold one. A git tree is a directory that holds a `.git` entry: the git toplevel of the working directory of the hook, and each directory with a `.git` entry above a write target or in it. The working directory is the `cwd` of the hook input, or else the working directory of the process. The guards compare the paths after they remove a `/private` prefix on both sides, so a worktree in a temp directory stays read-only. A `cd` may go to the git toplevel of the working directory. |

<!-- generated:end verifier-guard-commands -->

The temp destination commands (`cp` and `cd`) follow more rules. This is what lets the verifier copy a worktree into a temp directory and go to the copy, which a red proof on a changed copy needs. A redirection does not end the scan. A path argument that comes after a redirection is a deny, in each order and in both the joined form (`>/tmp/log`) and the two-word form (`> /tmp/log`), because the shell removes the redirection and the path stays an argument of the command. A hyphen-led argument that holds `t` or `T`, and a long option that starts with `--t` or `--n`, are each a deny, because such a flag moves the destination into a word that the flag rule drops. Two literal prefixes are not enough: GNU short options cluster, so `cp -Rt /Users/y /tmp/a` still takes the next word as the destination, and a GNU long option accepts an unambiguous abbreviation such as `--targ`. `cp -R`, `cp -a`, `cp -p`, and `cp -Rp` stay permitted.

A `cd` writes nothing, so it may also go to a directory in the git toplevel of the working directory (#121). Many repositories give their gates in the form `cd <dir> && <gate>`, for example `cd api && ./gradlew test` or `cd app && npm test`, and the verifier of each stack must run its own gate. A `cd` takes one path argument: bash 3.2 goes to the first of two paths, and zsh reads two paths as a substitution in the working directory. The path must be a temp path or a path in that toplevel, which the next paragraph describes. A relative path with no `..` component stays below the working directory, so it is in the toplevel. An absolute path must be the toplevel or start with it and a `/`, after the guard removes a `/private` prefix and makes each `//` and `/./` one `/`. The guard denies a `..` component, a path that starts with `~` or `=`, and a path with a pattern or an expansion character (`*`, `?`, `[`, `{` or `$`), because each one can name a directory outside the tree: `cd ../outside`, `cd /etc`, `cd api/../..` and `cd ~` are each a deny. With no git toplevel above the working directory, only a temp path is permitted. Each later segment is tested on its own, and a relative write target is not a temp path, so `cd api && echo x > f` stays a deny.

The tree under review stays read-only also when it is in a temp directory (#106), and so does each other git tree (#125). On macOS, a worktree from `mktemp -d` is under `/var/folders`, which is a link to `/private/var/folders`. A git tree is a directory that holds a `.git` entry: a directory, or the pointer file of a worktree. Each guard finds the git toplevel of its working directory: the nearest directory, from the `cwd` of the hook input (or else the working directory of the process) up, that holds a `.git` entry. Each guard also looks for a `.git` entry from each write target up, and in the directory that the target names (#125). Before #125, a verifier that went to a copy with no `.git` entry, as the break-a-copy recipe does, could write into the original worktree, because Claude Code keeps the working directory from one Bash call to the next. The `python3` process that reads the hook input does each search, so the guard starts no git process. The Bash guard does not divide the command into commands in `python3`: it takes each word from a temp prefix to its end, also after a separator or a redirection sign in the word, as a path, and it gives the shell the git trees of each of these paths. An extra path is no risk, because each tree that it gives is a real git tree. The walk in a directory reads at most `TREE_SEARCH_LIMIT` (2000) directories, and it does not go into a directory that a pattern of the target cannot match. When the walk stops at that limit, a target that is the directory or holds it is a deny, because the guard cannot tell that the directory holds no git tree; a path in the directory stays permitted. The Bash guard denies a temp path as a write target when the path is inside a git tree or holds one: each temp-write command, a `cp` destination, a redirection target, and the output path of a GET-only command. It compares the paths after it removes a `/private` prefix on both sides. Before it compares, it reads the path as the shell will: it removes each quote and backslash, puts the value of `$TMPDIR` in place of a leading `$TMPDIR`, and makes each `//` and `/./` one `/`. A path with a pattern or an expansion character (`*`, `?`, `[`, `{` or `$`) counts only up to the first such character, so `rm -rf /tmp/*` is a deny when a git tree is in `/tmp`. A `cd` writes nothing, so it may go to the git toplevel of the working directory. A write to another temp path stays permitted: copy the tree into a temp directory, remove the `.git` entry of the copy in the same command, and change the copy. The working directory of a `delegate run` verifier is the temporary copy of the engine, so that verifier copies the copy again for a red proof. The source of the rule is `IN_TOPLEVEL_CHECK`, `TOPLEVEL_FINDER` and `TARGET_TREE_FINDER` in `src/delegate/definitions/render.py`.

Each entry matches the command alone or the command with arguments, and never a longer command name: `ls` does not match `lsof`. A segment that starts with `git -C <path>` loses that prefix before the test, so `git -C <path> diff` is the read `git diff`. This lets a verifier with no worktree of its own read a worktree by its path. The path is not examined, because a read of any path is a read. The subcommand after the path is examined, so `git -C <path> push` and `git -C <path> commit` stay denied. A segment that starts with `nix develop -c` or `nix develop <flake-ref> -c` (or `--command`) loses that prefix in the same way, so a gate that runs in a flake dev shell is tested as the gate command: `nix develop -c python3 test.py` is the read `python3 test.py`, and `nix develop -c git push` stays denied. Only a flake reference may come before `-c`. A flag there can write (`--profile` writes a symlink, `--build` runs build phases), so a hyphen-led word denies, and `nix develop` with no `-c` (an interactive shell) denies. A segment that starts with `direnv exec <dir>` loses that prefix too, so a gate that runs with the repository's `.envrc` is tested as the gate command. `direnv allow` writes direnv's allow list, and it denies. A segment that starts with `docker compose` loses each `-f <file>` and `--file <file>` that comes directly after it, as a segment that starts with `git` loses `-C <path>` (#121). A compose file is a read. So with the gate `docker compose config`, `docker compose -f compose.yml config` and `docker compose -f a.yml -f b.yml config` are the gate. The subcommand after the options is still tested, so `docker compose -f x.yml up` and `docker compose -f x.yml run app sh` stay denied, and `docker compose -f x.yml` with no subcommand denies. The guard removes no other option: `docker compose -p x -f y.yml config` and `docker compose --file=y.yml config` deny. The table is generated from `VERIFIER_READ_COMMANDS`, `VERIFIER_TEMP_WRITE_COMMANDS`, `VERIFIER_TEMP_DEST_COMMANDS`, and `VERIFIER_TEMP_PATH_PREFIXES` in `src/delegate/definitions/render.py`. Change the constants there and run `delegate docs`; a test fails when the table differs from the constants. The table holds no build tool and no tracker CLI. A verifier that must run one gets it from its declaration, with `gateCommands` or `getOnlyCommands` (below).

### Gate commands

The reasons are in [ADR 0004](../../docs/adr/0004-gate-commands-per-repository.md). A repository's verifier must run that repository's gates, and the gates differ per repository: `npm test` in one, `dotnet test` in another, `zensical build` in a third. The global list holds only what every verifier needs: no build tool and no tracker CLI is in it. A declaration adds its own gates with `gateCommands`, and `bootstrap` takes them as `--gate-command` (repeat it). Take them from the verification gates in the repository's `docs/agents/delegation.md`.

- Each gate command is permitted like a read command, alone or with arguments, in that repository's verifier only. `npm test` permits `npm test -- --run` and denies `npm install`. The specialist guard does not change.
- The `nix develop -c` and `direnv exec <dir>` prefixes apply, so `nix develop -c npm test` and `direnv exec . npm test` are the gate `npm test`. A session started from a shell that direnv loaded runs the bare command.
- A gate in a subdirectory runs as `cd <dir> && <gate>`: with the gate `./gradlew test`, `cd api && ./gradlew test` is permitted, because a `cd` may go to a directory in the git toplevel of the working directory. A `docker compose` gate matches with its compose files: with the gate `docker compose config`, `docker compose -f compose.yml config` is permitted (#121).
- An entry is plain words separated by single spaces: letters, digits, and `. _ / : = @ + -`. No quote, shell syntax, or glob character may reach the bash case pattern or the OpenCode glob.
- One exception is an exact gate: an entry of plain words in which one or more words are in double quotes and hold one `$(...)` of plain words, for example `uvx "zensical==$(cat .zensical-version)" build --strict`. The first word stays plain, and the command in the `$(...)` must not open a write. The guard permits an exact gate only as the full command, character for character, and with no arguments. `getOnlyCommands` takes no exact entry.
- An entry must not open a write: a temp-write or temp-destination command (`rm`, `cp`, and the others keep their path rules), or a prefix of a known write (`git`, `git push`, `git reset`). The declaration parser refuses both.
- On OpenCode each plain gate command becomes `<command> *`: allow, after the read commands. An exact gate is not in the OpenCode map.

Some commands read only while they stay a GET, for example a repository script `./scripts/api` that sends a GET unless a flag names another method or gives a body. A declaration names them with `getOnlyCommands`. The verifier guard permits each one alone or with arguments, in that verifier only, and it examines every argument. An entry obeys the same rules as a gate command: plain words, and no write. A plain gate command cannot carry the argument rule, so do not put such a command in `gateCommands`.

The guard denies a GET-only command when any argument is one of these flags:

| Kind | Denied spellings |
|---|---|
| Method | `-X <m>`, `-X<m>`, `--method <m>`, `--method=<m>`, `--request <m>`, `--request=<m>` |
| Body | `-d`, `--data`, `--data-*` (for example `--data-raw`), `--input`, `--input=<f>`, `-f`, `-F`, `--field`, `--field=<f>`, `--raw-field`, `--raw-field=<f>` |
| Output | `-o` and `--output` (also `-o<path>` and `--output=<path>`), unless the path is a temp path |

A single-hyphen word is a short flag or a cluster of short flags, so the guard denies it when it holds `X`, `d`, `f`, `F` or `o` (`-sX` and `-dBODY` deny). It removes quote and backslash characters from each word first, so `'--method' POST` denies. An output flag keeps the rule of the temp-write commands: `-o /tmp/out.json` and `--output=/tmp/out.json` pass, and `-o ./out.json`, `-o /tmp/../x`, and an `-o` with no path deny. The command with no such flag, and the command with read-only arguments (`--paginate`, `--jq .title`, `-H Accept:application/json`), stay permitted. The guard reads text. It does not see an argument that a shell expansion builds, so a GET-only command must not take its arguments from an expansion that an agent controls.

On OpenCode a `getOnlyCommands` entry is not permitted at all: a pattern cannot examine the arguments, so it cannot see a method flag, a body flag or an output flag. This is a limit of OpenCode, not a choice. The OpenCode verifier allows no GET-only command, whatever the flags.

A gate can write: `npm test` can write `node_modules/` and a cache. The guard reads text and cannot confine that, as for `python3`. The verifier method still applies: copy the worktree into a temp directory, and run the gates in the copy.

On OpenCode the verifier keeps `"*": deny` as its first rule and allows the read tools, and it now adds a `bash` pattern map whose own first key is `"*": deny`. Each read command becomes `<command> *`: OpenCode treats a trailing ` *` as optional, so one pattern covers the bare command and the command with arguments. The OpenCode map is smaller than the Claude Code list on purpose. An OpenCode pattern cannot examine a path argument, so the temp-write commands, the temp-destination commands, and each `getOnlyCommands` entry stay out. The map also leaves out `echo` and `printf`, and it ends with three deny rules, `*>*`, `*| tee *` and `*|tee *` (#123). OpenCode keeps the last rule that matches, so the denies win over the read allows: `cat a >> /abs/b` and `grep x f | tee /abs/y` are a deny, and `cat f` stays an allow. The `*>*` rule also denies `2>&1` and `2>/dev/null`. The limit of these rules is in Limits below.

The two guards are not equal, and the differences are in Limits below.

### The Write tool

The verifier writes its verdict with the Write tool. A Bash write of a verdict often fails: the Bash guard refuses a heredoc, and each `$(` and backtick of a command, also inside quotes, and a verdict names code.

- On Claude Code a `PreToolUse` hook on `Write` reads `tool_input.file_path` with `python3`. It permits the Write only when the path is absolute, starts with `/tmp/`, `/private/tmp/` or `/var/folders/`, or with the value of `$TMPDIR` in the environment of the hook, and has no `..` component. A `$TMPDIR` that is not absolute, or that is `/`, adds no prefix. It denies a path in the repository, a relative path, and a path with a `$`, because the Write tool does not expand a variable. Each deny exits 2, and the message names the path. With no `python3`, or with no file path in the input, the hook fails closed. It also denies a path inside a git tree, or a path that holds one, by the rule of the Bash guard: the git toplevel of its working directory, the git tree above the path, and a git tree in the path when the path is a directory (#125). The Write tool expands no pattern and no variable, so the guard compares the path as it is. The hook carries the marker `# agent-definitions:verifier-write-guard`. The prefixes come from `VERIFIER_TEMP_PATH_PREFIXES` without `$TMPDIR` (`VERIFIER_WRITE_PATH_PREFIXES`).
- On OpenCode the `edit` permission covers the write, edit and patch tools. OpenCode matches an `edit` rule against the path relative to the worktree, so a path in the project never starts with `../`. The verifier gets `edit: {"*": deny, "../*": allow}`. OpenCode asks `external_directory` for a path outside the project, with the absolute parent directory and `/*` as the target. The verifier gets an `external_directory` map that opens with `"*": deny` and allows only `/tmp/*`, `/private/tmp/*` and `/var/folders/*`. The map has no `$TMPDIR` prefix, so on a host whose `$TMPDIR` is not under one of these prefixes, Claude Code permits a write to `$TMPDIR` and OpenCode denies it. The project of OpenCode is the git toplevel of its working directory, and the `edit` map denies each path in it, so a worktree in a temp directory stays read-only on OpenCode too, when the path has the spelling of the real path. A path through a symbolic link is a limit (see Limits).
- The validator accepts `Write` on a Claude Code verifier only beside the Write hook with its marker. On OpenCode it accepts an `edit`, `write` or `patch` map only when the map opens with `"*": deny` and each allow starts with `../`, and then only beside an `external_directory` map that opens with `"*": deny` and allows only temp prefixes. Each other case is `VERIFIER_WRITE_TOOL`.

## Effort

Decision of 2026-09-19 ([ADR 0001](../../docs/adr/0001-effort-follows-the-model.md)): effort by model is the default. A subagent whose file sets no `effort` uses `modelSettings.<its own model>.effortLevel`, so choosing a model at spawn chooses the effort. The configuration that renders `settings.json` sets that key for every tier model. Declare `effort` only when the two halves of a pair need different effort on the same model. Never launch the coordinator session with `--effort` or `CLAUDE_CODE_EFFORT_LEVEL`; both defeat the per-model value.

## Validation findings

<!-- generated:begin finding-codes -->
<!-- Generated by `delegate docs`. Do not edit this section by hand. -->

| Code | Meaning |
|---|---|
| `UNKNOWN_KEY` | a frontmatter key the harness does not recognise; inert on Claude Code, swept into provider options on OpenCode |
| `BAD_MODEL` | model not in the tier table's allowed set for that harness |
| `BAD_EFFORT` | Claude Code `effort` outside the levels, or OpenCode `variant` outside the variants |
| `MISSING_TWIN` | a `-specialist` without its `-verifier`, or the reverse |
| `DUPLICATE_NAME` | two files declare one name; the harness picks one by filesystem read order |
| `VERIFIER_WRITE_TOOL` | a verifier lists Edit, MultiEdit, NotebookEdit, an unguarded Bash, or a Write with no `PreToolUse` hook on Write whose command carries `# agent-definitions:verifier-write-guard`; on OpenCode it permits a write tool with `allow` or `ask`, except an `edit` map that opens with `"*": deny` and allows only patterns that start with `../`, beside an `external_directory` map that opens with `"*": deny` and allows only temp prefixes; or its `bash` map does not open with `"*": deny`, or that map resolves a write command to allow |
| `VERIFIER_BASH_UNGUARDED` | a Claude Code verifier lists `Bash` with no `PreToolUse` hook on Bash whose command carries `# agent-definitions:verifier-guard` |
| `RULE_SHADOWED` | an OpenCode permission rule placed before `"*"` in the same map; the last matching rule wins, so it does nothing |
| `VERIFIER_NOT_READONLY` | a Claude Code verifier with no `tools` allowlist; an OpenCode verifier whose first rule is not `"*": deny` |
| `SPECIALIST_CAN_PUSH` | a `-specialist` with no push guard: on Claude Code no `PreToolUse` hook that selects Bash and names `push`; on OpenCode no `permission.bash` deny that covers `git push`. A deny that the catch-all shadows does not count |
| `BAD_FRONTMATTER` | frontmatter absent on line 1, unclosed, or not YAML |
| `NAME_INVALID` | a name that starts with a hyphen or holds a character outside letters, digits, hyphen and underscore, so the harness would skip the file |
| `MISSING_NAME` | a Claude Code file with no name; the harness treats it as documentation |
| `MISSING_DESCRIPTION` | a name with no description; the harness skips the file, and OpenCode needs a description for routing |
| `BAD_TYPE` | OpenCode `tools` as a string; that aborts config load for the whole session |
| `CROSS_HARNESS_MISMATCH` | an agent present for one harness and absent for the other |
| `MISSING_SKILL` | a preloaded skill absent from `--skills-dir` |
| `DESCRIPTION_BUDGET` | combined Claude Code descriptions exceed the budget in `tiers.toml` |
| `LOAD_BLOCK_NOT_PLAIN` | a load-time block (`` ```! ``) of a preloaded skill holds, outside quotes, `$(`, a backtick, `;`, `&&`, a double pipe, a pipe, `if` or a redirection; Claude Code cannot analyse the command, so a subagent that preloads the skill does not start (ADR 0006). The finding gives the file and the line |

<!-- generated:end finding-codes -->

## The tier table

`src/delegate/shared/tiers.toml` is the single source: tier to model per harness, allowed models per harness, effort levels, OpenCode variants, the description budget. A consumer configuration that renders agents, for example a Nix module, reads the same file, so the tiers have one source. The OpenCode identifiers follow the `provider/model-id` shape; confirm them with `opencode models` before the first OpenCode rollout.

The OpenCode column is the one part a caller may override, with the two flags above ([ADR 0003](../../docs/adr/0003-opencode-model-override.md)). The table stays the source of the defaults and of the whole Claude Code column. Do not edit the OpenCode column for one machine or one job: the table is global, and the override exists so the identifiers can live with whatever configures the provider.

## Neutrality check

The check scans four directories: `agent-delegation/` and `agent-definitions/`, the two skill directories in `skills/`, and `plugins/` and `.claude-plugin/`, the two plugin directories in the repository root. In this section, the kit is these four directories. The kit must hold no personal or company term. The test `tests/test_neutrality.py::test_the_kit_holds_no_denylisted_term_outside_the_allowlist` reads every file in the kit and fails on each line that holds a term from a denylist, unless the allowlist names that file and that term. The code is `src/delegate/docs/neutrality.py`.

- **`AGENT_DEFINITIONS_DENYLIST`** is the path of the denylist. The denylist is private, so it is never in the kit: the consumer repository keeps it and gives its path. Without the variable, the test skips with this notice: `neutrality check skipped: AGENT_DEFINITIONS_DENYLIST is not set`. A path with no file, or a denylist with no term, fails the test.
- **`AGENT_DEFINITIONS_KIT_ROOT`** is the directory that holds both skill directories. It is optional. Without it, the test uses the `skills/` directory of the repository, which is correct in a checkout. The check finds `plugins/` and `.claude-plugin/` in the parent of the kit root, which is the repository root in a checkout. A missing skill directory or plugin directory fails the test. A Nix build copies only the package, its tests and its example, so `package.nix` gives the kit as a second source, with the layout of the repository, when it gets a `neutralityDenylist` argument.
- **The denylist format.** One term on each line. A term has no space. A line that starts with `#` is a comment. The check matches each term case-insensitively, as a substring.
- **The allowlist format.** The file is `agent-definitions/neutrality-allowlist.txt`. One entry on each line: `<path> <term>`, with the path relative to the directory that holds both skill directories, for example `agent-delegation/SKILL.md sometoken`. The path of a plugin file is relative to the repository root, for example `plugins/claude-code/hooks/register.tsx sometoken` or `.claude-plugin/marketplace.json sometoken`. An entry permits every line of that file that holds that term. A line that starts with `#` is a comment. An entry is a permanent exception only, with a comment line directly above it that gives its reason. Today the kit has no permanent exception, so the allowlist holds no entry. The check does not scan the allowlist, because the allowlist names the terms. An entry names only a term that its file already holds, so the allowlist shows no term that the kit does not show.
- **`STALE_ALLOWLIST_ENTRY`.** The check reports an entry whose file does not hold its term, or that names a file that the check does not scan (a missing file, a generated file, or the allowlist). The finding gives the line of the entry, and the test fails. Remove the entry. Without this finding, a term that a change removes from its file stays visible in the allowlist.
- **Without Nix:** from the repository root, run `pip install .` and `pytest`, with the denylist path in `AGENT_DEFINITIONS_DENYLIST`.
- **The scan skips generated files:** the directories `__pycache__`, `.pytest_cache`, `build` and `*.egg-info`, and the file `.DS_Store`. A pip build or a test run writes them, and they copy kit files. The scan also skips `plugins/<name>/.claude-plugin/types/`, the type declarations that Claude Code writes when it loads a plugin from a folder.

## Limits

- No harness composes agent files. A project-tier agent is a complete file, never an extension of a global one. Claude Code and Codex replace on a name collision; OpenCode merges per key, which this renderer does not rely on.
- Docs cannot be attached by path. Put reference material in a skill and list it in `skills`; on OpenCode, name it in the prompt.
- Both Claude Code guards read the hook input with `python3`, which the kit already requires; they need no `jq`. Both fail closed: with no `python3` on the agent's PATH, or with input that holds no command text, each guard exits 2 and blocks the call ([ADR 0005](../../docs/adr/0005-guards-read-the-hook-input-with-python3.md)).
- The verifier guard reads the command as text. `python3`, `bash -c`, `env`, and `find` are on the allow list because the gates and the reviews need them, and each of them can write through the guard. The guard does not parse Python and it does not parse a nested shell. This is a contract for the verifier, not a sandbox.
- The verifier guard divides the command at `&&`, `||`, `;`, `|`, `&` and a newline, but not inside quotes. An `&` in a redirection (`2>&1`, `&>file`) does not divide. A redirection with `&` must go to a file descriptor: the guard permits `>&` and `n>&` only before a file descriptor number or `-`, and it denies each `>>&`, because the shell writes `ls >&out` and the zsh `ls >>&1` to a file. A quote right after the descriptor, as in `bash -c "ls 2>&1"`, is denied too, because a quote there can make the target a file name. Under bash and `/bin/sh`, write `bash -c "ls 2>&1 "` instead. A segment that ends at a redirection sign is denied, because in `ls >|cat` the shell writes the file `cat`. It reads single quotes, double quotes, a backslash and a comment as bash and zsh do. It does not parse a `$(` or backtick substitution, a parameter expansion with braces, an ANSI-C quote (`$'`) or a here-document, because each of these forms reads quotes in its own way. In a command that holds one of these forms, a separator inside a quoted argument divides the command, and the parts that are not commands are denied. A quote that is not closed denies the command. The guard fails closed in each case.
- The guard uses the bash string operators. Measured 2026-09-19: under `bash` and under macOS `/bin/sh` it behaves as the table says; under `dash` it exits 2 on a syntax error, which is a deny, so the guard stays fail-closed. zsh does not divide an unquoted expansion into words, so the guard first turns on sh emulation under `zsh`, and it then gives the verdicts of `bash` (#113). Before, it denied each temp write under `zsh`.
- An assignment prefix is not on the allow list. A command substitution or a process substitution runs a command that the guard cannot examine, so a command that holds `$(`, a backtick, `<(`, `>(` or the zsh `=(` is denied, also inside single quotes. The one exception is an exact gate of the declaration, given as the full command. `T=$(mktemp -d)` is denied. Use `mktemp -d` and then a literal path. To search for the text `$(`, write it as `[$](`.
- The destination-flag rule is wider than the flags it must stop, because the guard does not know which flags a command takes. In a temp-destination command it denies every hyphen-led word that holds `t` or `T`, so it also denies `cp --preserve=timestamps` and `cp --strip-trailing-slashes`, which move no destination. Each such deny is a lost convenience, not a lost capability: the copy runs without the flag.
- A temp path is a text prefix and a path with no `..` component. The guard rejects `..`, it does not resolve it: the guard reads the command as text, and a resolution needs the file system. `/tmp/../Users/x` is therefore a deny for each temp-write and temp-destination command, and for a redirection target. The rule reads a path component, so a name such as `/tmp/a..b` is permitted. A path that needs `..` must be written out in full.
- The copy allow reads the source and it does not examine it. A `cp` source can be any path the agent can read, secrets included, and the copy then sits in a temp directory. The temp directory is the boundary, not the source.
- The Write guard reads `file_path` as text, as the Bash guard reads a command. It does not resolve a symbolic link, so a link in a temp directory that points to a directory below the toplevel of a repository is a write into that repository. A link to the toplevel itself is a deny, because the search for a `.git` entry goes through the link. It takes `$TMPDIR` from its own environment, so a host that sets `$TMPDIR` to a directory in a repository opens that repository to the Write tool, as it does to a Bash write to `$TMPDIR`.
- The rule for the tree under review and for each other git tree looks for a `.git` entry along the path as the command gives it (#125). It does not resolve a symbolic link first, so a link in a temp directory that points to a directory below the toplevel of a git tree hides that tree, and a write through the link is a write into the tree. A link to the toplevel itself is a deny. The Bash guard finds the trees of the words of the command text, so a target that a shell expansion builds is not examined, as in the rest of the guard. The walk in a directory stops after 2000 directories; a target that is such a directory or holds it is a deny, a lost convenience and not a hole. The rule reads `.git` entries and not the git configuration, so `GIT_DIR` and `GIT_WORK_TREE` do not change a tree. A copy of a tree that keeps its `.git` entry is a git tree too, so remove the `.git` entry of the copy in the command that makes it.
- The `cd` rule reads the path as text (#121). It does not resolve a symbolic link, so a link in the tree that points out of the tree takes a `cd` out of the tree. It does not read `CDPATH`, so in a shell that has `CDPATH` set, a relative `cd` can go to a directory of `CDPATH`. A directory in the tree that holds its own `.git` entry, such as a submodule or a nested repository, is a new git toplevel after the `cd`, and a later `cd` may go only into that directory. In each case the guard still denies each write outside a temp directory, and each write inside a git tree.
- The OpenCode write rules have no `$TMPDIR` prefix, because an OpenCode pattern holds no environment variable. On a host whose temp directory is not under `/tmp/`, `/private/tmp/` or `/var/folders/`, OpenCode denies the write of the verdict, and the engine reads the verdict from the final text of the verifier. In a directory with no version control, OpenCode takes `/` as the worktree, so the relative path of a temp file does not start with `../`, and OpenCode denies the write too. Each of these is a deny, not a hole.
- The OpenCode maps cannot deny each write in the tree that the verifier reviews. OpenCode takes the real path of the project, with each symbolic link resolved, and it compares that path as text with the path that the agent gives. The `edit` map denies a path in the project in that real spelling. A path through a link to the project is outside the project as text, so its relative path starts with `../`, the `edit` map allows it, and the `external_directory` map allows its temp prefix. An OpenCode pattern holds no variable that names the project, and the path of the tree changes in each run, so no rule can deny that spelling. Measured on OpenCode 1.15.10 on 2026-10-19 on macOS: in a repository at `/var/folders/<x>/copy`, whose real path is `/private/var/folders/<x>/copy`, the write tool wrote `/var/folders/<x>/copy/src/w1.txt`, and it denied `/private/var/folders/<x>/copy/src/w2.txt` and `src/w3.txt`. In a repository whose real path is `/private/tmp/<y>/copy`, it wrote `/tmp/<y>/copy/src/b.txt`. On macOS the engine gives the verifier the path of its temporary copy under `/var/folders`, so the rendered OpenCode verifier states this limit. In a run, the tree is the temporary copy, and the worktree check of the engine still covers the real worktree.
- The OpenCode verifier map holds none of the rules that let a verifier copy a worktree or read it by path: no `cp` rule, no `cd` rule, and no `git -C <path>` rule. A `cp` rule and a `cd` rule must examine a path argument, which an OpenCode pattern cannot do. A `git -C * <read>` rule needs no path examination, but `git -C * diff*` compiles to the anchored `^git -C .* diff.*$`, and that pattern also matches `git -C /x push; git diff`. The OpenCode patterns stay narrow ([ADR 0002](../../docs/adr/0002-narrow-opencode-push-pattern.md)), and this widening is not permitted, so the OpenCode map stays as it was. An OpenCode verifier therefore cannot make the copy, and it reports the red proof as a command it cannot run.
- The OpenCode verifier allows no `getOnlyCommands` entry. A GET-only rule examines each argument for a method flag, a body flag and an output flag, and an OpenCode pattern cannot examine arguments. A GET-only command is therefore a deny on OpenCode, bare or with arguments, and a verifier there reports it as a command it cannot run. On Claude Code the guard also cannot see an argument that a shell expansion builds (the guard reads text).
- The OpenCode verifier map has no `nix develop` rule. A `nix develop * -c *` pattern cannot test the command after `-c`, so it would permit every command in a dev shell. An OpenCode verifier reports a dev-shell gate as a command it cannot run.
- The OpenCode verifier map does not divide a compound command either, for the same reason as the specialist map: OpenCode v1.18.31 hands the permission check the raw command. `git status && git commit -m x` is one deny on Claude Code and one allow on OpenCode.
- The OpenCode verifier map cannot deny each redirection to a file (#123). OpenCode keeps the last rule that matches: `evaluate` in `packages/core/src/permission.ts` of OpenCode at tag v1.15.10 is a `findLast` over the rules, read on 2026-10-20. So the deny rules `*>*`, `*| tee *` and `*|tee *` come after the read allows, and they win. OpenCode checks each simple command of a line on its own, with the text of a redirection that follows that command (`source` in `packages/opencode/src/tool/shell.ts` at the same tag). A redirection after a group (`{ cat f; } > x`), a subshell, a loop, or the last command of a pipeline (`cat f | grep x > y`) is not in that text, so no glob can deny it. Measured on OpenCode 1.15.10 on 2026-10-20 with `opencode debug agent <verifier> --tool bash`: the map denied `echo x > <file>`, `cat f >> <file>`, `ls &> <file>`, `grep x f | tee <file>` and `git status 2>/dev/null`, and it permitted and wrote `{ cat f; } > <file>` and `cat f | grep h > <file>`. Because of this limit, the OpenCode map has no `echo` rule and no `printf` rule: a verifier needs them only to write. The rendered OpenCode verifier states the limit. The model `opencode_bash_action` in `src/delegate/definitions/validate.py` matches the full command text, so it gives a deny for the group and the pipeline forms, and it does not show this limit.
- Frontmatter hooks in a project-scope agent run only after the workspace trust dialog is accepted for the folder the agent file came from, and a `-p` session does not count as accepting it. A project-tier verifier in a folder that is not trusted therefore has a Bash tool with no guard. The global tier is user-scope and it is not affected.
- The two push guards are not equal, and no pattern makes them equal. Measured 2026-09-19: the Claude Code hook applies an unanchored regular expression to the raw command, so it also stops `git add . && git push`, `git  push` with two spaces, and `GIT_DIR=/x git push`. An OpenCode pattern is an anchored glob, and OpenCode v1.18.31 hands the permission check the raw command with no split at `&&` (`packages/core/src/tool/bash.ts`, which carries a TODO for a tree-sitter parser), so OpenCode allows all three. Do not widen the OpenCode pattern to `*git push*` ([ADR 0002](../../docs/adr/0002-narrow-opencode-push-pattern.md)). The residual risk is an OpenCode specialist that pushes from inside a compound command.
- Two files with one name in one directory resolve by read order. The renderer refuses to produce that; the validator reports it in hand-written sets.
- A turn cap is a cost stop, not a lost deliverable. Claude Code 2.1.267, measured 2026-09-19: at `maxTurns` the Agent tool result gives only a harness NOTE and the agent's `agentId`. The agent's own text is absent. The status is still `completed`, and `SubagentStop` does not fire. A `SendMessage` to that `agentId` starts the same transcript again and keeps its context, and the cap applies again on each resume. OpenCode at `steps` makes the agent write a summary with the tasks that remain, and it documents no resume. Set a cap to bound cost, and resume the agent when it hits the cap.
