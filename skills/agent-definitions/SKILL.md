---
name: agent-definitions
description: Render a specialist-verifier agent pair for Claude Code and OpenCode from one declaration, and validate rendered agent files against each harness's real schema. Use when adding or changing a global agent declaration, when the agent-delegation bootstrap emits a repo's project agents, or when an agent file needs checking.
---

# Agent definitions

One declaration, two harnesses, two files each. A pair is a specialist and a verifier that share a domain, skills, and references. The verifier is read-only and runs on the verifier tier. Neither half is hand-written.

The decisions that shaped the design are ADRs in [`docs/adr/`](docs/adr/). The harness facts come from the vendor documentation, read on 2026-09-19: the [Claude Code subagent docs](https://code.claude.com/docs/en/sub-agents), the [OpenCode agent docs](https://opencode.ai/docs/agents/), and the OpenCode config schema at `https://opencode.ai/config.json`. Codex is out of scope.

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

`render` and `validate` take two more optional flags, both repeatable, that override the OpenCode column of the tier table ([ADR 0003](docs/adr/0003-opencode-model-override.md)).

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

The package lives in `src/agent_definitions/`, and its tests in `tests/`. Its pytest suite runs in `nix flake check` as the `agent-definitions` check. The suite needs `git`, a `nativeCheckInput` in `package.nix`. The guard tests run the rendered hooks with `python3` as the only tool on `PATH`, so the suite needs no `jq`.

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

A live-read block is one plain `git grep` command. Claude Code runs a load-time block only when it can analyse the command. The earlier compound block (`R=$(...); if ...; fi`) stopped a subagent from starting in the manual, acceptEdits and auto permission modes (measured on Claude Code 2.1.283, 2026-09-26). bootstrap counts the lines of each reference when it writes the skill: a document of at most 200 lines gets `git grep --untracked --no-exclude-standard -h -E -m 200 -e '^' -- :/<path>`, and a longer one gets its heading index, `git grep --untracked --no-exclude-standard -h -n -E -e '^#{1,6} ' -- :/<path>`, with a note that gives the `sed -n` command for one section. A reference must be a file; a directory exits 1.

The `:/` pathspec names the file from the top of the working tree, so a block reads the same file from the repository root and from each subdirectory. In a linked worktree it reads the files of that worktree, uncommitted changes included. An earlier form of the block used a path relative to the working directory, and a continuation spawned from `app/` stopped in about 250 ms. `--untracked` with `--no-exclude-standard` also reads a file that is not in the index and a file that `.gitignore` names, so a block reads any file on disk, as the `sed` block did. `-E` sets the pattern type, because `grep.patternType` in a git config changes the default. A block needs no shell syntax and no assignment prefix, so it stays one command that Claude Code can analyse. The context skill is for a git repository: outside one, each block returns an error. A block that exits with no output can also mean that its reference file is missing or renamed.

The two templates are `templates/context-skill.md` and `templates/context-skill-section.md`, data in the package like the agent templates. Both are `string.Template` files, so each dollar sign the generated text needs is written twice: `$$(...)` in `context-skill.md` is `$(...)` in the skill.

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
| Effort | omitted unless declared; follows the model through `modelSettings.<model>.effortLevel` ([ADR 0001](docs/adr/0001-effort-follows-the-model.md)) | `variant`, only when the declared effort is an OpenCode variant (`high`, `max`) |
| Skills | `skills:` preloads them | cannot preload; the body tells the agent to load them with the skill tool |
| Verifier read-only | `tools: Read, Grep, Glob, Bash`, plus a `PreToolUse` guard hook on Bash | `permission` with `"*": deny` first, then the read tools, then a `bash` pattern map that opens with its own `"*": deny` |
| Specialist guardrails | `isolation: worktree`; a `PreToolUse` hook on Bash blocks `git push` (exit 2) | `permission.bash` with `"*": allow` first, then `git push*: deny` and `git -C * push*: deny`; the catch-all must come first or the denies are shadowed. Two denies, because an OpenCode pattern is an anchored glob and `git push*` does not cover `git -C <path> push` |
| Turn cap | `maxTurns` | `steps` |
| `outputLanguage` | `"ste"` opens the Report section of every agent file with "Write in ASD-STE100 Simplified Technical English."; `"none"` (the default) adds no rule | the same |
| `trackedFileBuild` | `true` adds "An unstaged file is invisible to a flake." after the specialist's staging rule; `false` (the default) leaves the rule alone | no change: the specialist states the staging rule without a reason |

Why the hook and not a denylist: a `disallowedTools: Bash(git push *)` entry removes Bash entirely. Why `tools` and not `permissionMode: plan`: permission mode is ignored when the parent session runs in auto mode, and a coordinator session can run in that mode. Why deny and never ask for the OpenCode verifier: `--auto` turns every `ask` into `allow`.

## The verifier's shell

Read-only means "no write outside a temp directory". It does not mean "no shell". A verifier that cannot run a command cannot run the gates, and the coordinator then does the verifier's work.

On Claude Code the verifier gets `tools: Read, Grep, Glob, Bash` and a `PreToolUse` hook on `Bash`. The hook is one bash line. It reads `.tool_input.command` with `python3`, turns each tab into a space, divides the command at `&&`, `||`, `;` and `|`, and tests each segment. A segment that is not on the list exits 2, and the message names that segment. It fails closed: no `python3`, no command text, or a shell without the bash string operators all end at exit 2. The hook carries the marker `# agent-definitions:verifier-guard`, which is what the validator looks for.

<!-- generated:begin verifier-guard-commands -->
<!-- Generated by `delegate docs`. Do not edit this section by hand. -->

| Group | Commands |
|---|---|
| git reads | `git diff`, `git log`, `git show`, `git status`, `git ls-files`, `git ls-tree`, `git rev-parse`, `git merge-base`, `git worktree list`, `git branch --show-current` |
| shell reads | `ls`, `cat`, `head`, `tail`, `sed -n`, `grep`, `rg`, `find`, `wc`, `diff`, `jq`, `shasum`, `stat`, `readlink`, `file`, `which`, `env`, `pwd`, `echo`, `printf`, `date`, `mktemp`, `python3`, `bash -c` |
| temp writes | `mkdir`, `mv`, `rm`, `tee`, `touch` — each one only when every path argument starts with `/tmp/`, `/private/tmp/`, `$TMPDIR`, or `/var/folders/`. A redirection target obeys the same rule, in every segment. |
| temp destination | `cp`, `cd` — each one only when the last path argument is a temp path. The earlier path arguments are sources, and a read of any path is a read. |

<!-- generated:end verifier-guard-commands -->

The temp destination commands (`cp` and `cd`) follow more rules. This is what lets the verifier copy a worktree into a temp directory and go to the copy, which a red proof on a changed copy needs. A redirection does not end the scan. A path argument that comes after a redirection is a deny, in each order and in both the joined form (`>/tmp/log`) and the two-word form (`> /tmp/log`), because the shell removes the redirection and the path stays an argument of the command. A hyphen-led argument that holds `t` or `T`, and a long option that starts with `--t` or `--n`, are each a deny, because such a flag moves the destination into a word that the flag rule drops. Two literal prefixes are not enough: GNU short options cluster, so `cp -Rt /Users/y /tmp/a` still takes the next word as the destination, and a GNU long option accepts an unambiguous abbreviation such as `--targ`. `cp -R`, `cp -a`, `cp -p`, and `cp -Rp` stay permitted.

Each entry matches the command alone or the command with arguments, and never a longer command name: `ls` does not match `lsof`. A segment that starts with `git -C <path>` loses that prefix before the test, so `git -C <path> diff` is the read `git diff`. This lets a verifier with no worktree of its own read a worktree by its path. The path is not examined, because a read of any path is a read. The subcommand after the path is examined, so `git -C <path> push` and `git -C <path> commit` stay denied. A segment that starts with `nix develop -c` or `nix develop <flake-ref> -c` (or `--command`) loses that prefix in the same way, so a gate that runs in a flake dev shell is tested as the gate command: `nix develop -c python3 test.py` is the read `python3 test.py`, and `nix develop -c git push` stays denied. Only a flake reference may come before `-c`. A flag there can write (`--profile` writes a symlink, `--build` runs build phases), so a hyphen-led word denies, and `nix develop` with no `-c` (an interactive shell) denies. A segment that starts with `direnv exec <dir>` loses that prefix too, so a gate that runs with the repository's `.envrc` is tested as the gate command. `direnv allow` writes direnv's allow list, and it denies. The table is generated from `VERIFIER_READ_COMMANDS`, `VERIFIER_TEMP_WRITE_COMMANDS`, `VERIFIER_TEMP_DEST_COMMANDS`, and `VERIFIER_TEMP_PATH_PREFIXES` in `src/agent_definitions/render.py`. Change the constants there and run `delegate docs`; a test fails when the table differs from the constants. The table holds no build tool and no tracker CLI. A verifier that must run one gets it from its declaration, with `gateCommands` or `getOnlyCommands` (below).

### Gate commands

The reasons are in [ADR 0004](docs/adr/0004-gate-commands-per-repository.md). A repository's verifier must run that repository's gates, and the gates differ per repository: `npm test` in one, `dotnet test` in another, `zensical build` in a third. The global list holds only what every verifier needs: no build tool and no tracker CLI is in it. A declaration adds its own gates with `gateCommands`, and `bootstrap` takes them as `--gate-command` (repeat it). Take them from the verification gates in the repository's `docs/agents/delegation.md`.

- Each gate command is permitted like a read command, alone or with arguments, in that repository's verifier only. `npm test` permits `npm test -- --run` and denies `npm install`. The specialist guard does not change.
- The `nix develop -c` and `direnv exec <dir>` prefixes apply, so `nix develop -c npm test` and `direnv exec . npm test` are the gate `npm test`. A session started from a shell that direnv loaded runs the bare command.
- An entry is plain words separated by single spaces: letters, digits, and `. _ / : = @ + -`. No quote, shell syntax, or glob character may reach the bash case pattern or the OpenCode glob.
- An entry must not open a write: a temp-write or temp-destination command (`rm`, `cp`, and the others keep their path rules), or a prefix of a known write (`git`, `git push`, `git reset`). The declaration parser refuses both.
- On OpenCode each gate command becomes `<command> *`: allow, after the read commands.

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

On OpenCode the verifier keeps `"*": deny` as its first rule and allows the read tools, and it now adds a `bash` pattern map whose own first key is `"*": deny`. Each read command becomes `<command> *`: OpenCode treats a trailing ` *` as optional, so one pattern covers the bare command and the command with arguments. The OpenCode map is smaller than the Claude Code list on purpose. An OpenCode pattern cannot examine a path argument, so the temp-write commands, the temp-destination commands, and each `getOnlyCommands` entry stay out.

The two guards are not equal, and the differences are in Limits below.

## Effort

Decision of 2026-09-19 ([ADR 0001](docs/adr/0001-effort-follows-the-model.md)): effort by model is the default. A subagent whose file sets no `effort` uses `modelSettings.<its own model>.effortLevel`, so choosing a model at spawn chooses the effort. The configuration that renders `settings.json` sets that key for every tier model. Declare `effort` only when the two halves of a pair need different effort on the same model. Never launch the coordinator session with `--effort` or `CLAUDE_CODE_EFFORT_LEVEL`; both defeat the per-model value.

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
| `VERIFIER_WRITE_TOOL` | a verifier lists Edit, Write, MultiEdit, NotebookEdit, or an unguarded Bash; on OpenCode it permits a write tool with `allow` or `ask`, or its `bash` map does not open with `"*": deny`, or that map resolves a write command to allow |
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

<!-- generated:end finding-codes -->

## The tier table

`src/agent_definitions/tiers.toml` is the single source: tier to model per harness, allowed models per harness, effort levels, OpenCode variants, the description budget. A consumer configuration that renders agents, for example a Nix module, reads the same file, so the tiers have one source. The OpenCode identifiers follow the `provider/model-id` shape; confirm them with `opencode models` before the first OpenCode rollout.

The OpenCode column is the one part a caller may override, with the two flags above ([ADR 0003](docs/adr/0003-opencode-model-override.md)). The table stays the source of the defaults and of the whole Claude Code column. Do not edit the OpenCode column for one machine or one job: the table is global, and the override exists so the identifiers can live with whatever configures the provider.

## Neutrality check

The kit is `agent-delegation/` and `agent-definitions/`, the two skill directories in `skills/`. The kit must hold no personal or company term. The test `tests/test_neutrality.py::test_the_kit_holds_no_denylisted_term_outside_the_allowlist` reads every file in the kit and fails on each line that holds a term from a denylist, unless the allowlist names that file and that term. The code is `src/agent_definitions/neutrality.py`.

- **`AGENT_DEFINITIONS_DENYLIST`** is the path of the denylist. The denylist is private, so it is never in the kit: the consumer repository keeps it and gives its path. Without the variable, the test skips with this notice: `neutrality check skipped: AGENT_DEFINITIONS_DENYLIST is not set`. A path with no file, or a denylist with no term, fails the test.
- **`AGENT_DEFINITIONS_KIT_ROOT`** is the directory that holds both kit directories. It is optional. Without it, the test uses the `skills/` directory of the repository, which is correct in a checkout. A Nix build copies only the package, its tests and its example, so `package.nix` gives the kit as a second source when it gets a `neutralityDenylist` argument.
- **The denylist format.** One term on each line. A term has no space. A line that starts with `#` is a comment. The check matches each term case-insensitively, as a substring.
- **The allowlist format.** The file is `agent-definitions/neutrality-allowlist.txt`. One entry on each line: `<path> <term>`, with the path relative to the directory that holds both kit directories, for example `agent-delegation/SKILL.md sometoken`. An entry permits every line of that file that holds that term. A line that starts with `#` is a comment. An entry is a permanent exception only, with a comment line directly above it that gives its reason. Today the kit has no permanent exception, so the allowlist holds no entry. The check does not scan the allowlist, because the allowlist names the terms. An entry names only a term that its file already holds, so the allowlist shows no term that the kit does not show.
- **`STALE_ALLOWLIST_ENTRY`.** The check reports an entry whose file does not hold its term, or that names a file that the check does not scan (a missing file, a generated file, or the allowlist). The finding gives the line of the entry, and the test fails. Remove the entry. Without this finding, a term that a change removes from its file stays visible in the allowlist.
- **Without Nix:** from the repository root, run `pip install .` and `pytest`, with the denylist path in `AGENT_DEFINITIONS_DENYLIST`.
- **The scan skips generated files:** the directories `__pycache__`, `.pytest_cache`, `build` and `*.egg-info`, and the file `.DS_Store`. A pip build or a test run writes them, and they copy kit files.

## Limits

- No harness composes agent files. A project-tier agent is a complete file, never an extension of a global one. Claude Code and Codex replace on a name collision; OpenCode merges per key, which this renderer does not rely on.
- Docs cannot be attached by path. Put reference material in a skill and list it in `skills`; on OpenCode, name it in the prompt.
- Both Claude Code guards read the hook input with `python3`, which the kit already requires; they need no `jq`. Both fail closed: with no `python3` on the agent's PATH, or with input that holds no command text, each guard exits 2 and blocks the call ([ADR 0005](docs/adr/0005-guards-read-the-hook-input-with-python3.md)).
- The verifier guard reads the command as text. `python3`, `bash -c`, `env`, and `find` are on the allow list because the gates and the reviews need them, and each of them can write through the guard. The guard does not parse Python and it does not parse a nested shell. This is a contract for the verifier, not a sandbox.
- The verifier guard divides the command at `&&`, `||`, `;` and `|` without respect for quoting. A separator inside a quoted argument divides the command, and the parts that are not commands are denied. The guard fails closed here.
- The guard uses the bash string operators. Measured 2026-09-19: under `bash` and under macOS `/bin/sh` it behaves as the table says; under `zsh` it also denies a temp write, because zsh does not divide the segment into words; under `dash` it exits 2 on a syntax error. Each difference is a deny, so the guard stays fail-closed whichever shell runs it.
- An assignment prefix and a command substitution are not on the allow list. `T=$(mktemp -d)` is denied. Use `mktemp -d` and then a literal path.
- The destination-flag rule is wider than the flags it must stop, because the guard does not know which flags a command takes. In a temp-destination command it denies every hyphen-led word that holds `t` or `T`, so it also denies `cp --preserve=timestamps` and `cp --strip-trailing-slashes`, which move no destination. Each such deny is a lost convenience, not a lost capability: the copy runs without the flag.
- A temp path is a text prefix and a path with no `..` component. The guard rejects `..`, it does not resolve it: the guard reads the command as text, and a resolution needs the file system. `/tmp/../Users/x` is therefore a deny for each temp-write and temp-destination command, and for a redirection target. The rule reads a path component, so a name such as `/tmp/a..b` is permitted. A path that needs `..` must be written out in full.
- The copy allow reads the source and it does not examine it. A `cp` source can be any path the agent can read, secrets included, and the copy then sits in a temp directory. The temp directory is the boundary, not the source.
- The OpenCode verifier map holds none of the rules that let a verifier copy a worktree or read it by path: no `cp` rule, no `cd` rule, and no `git -C <path>` rule. A `cp` rule and a `cd` rule must examine a path argument, which an OpenCode pattern cannot do. A `git -C * <read>` rule needs no path examination, but `git -C * diff*` compiles to the anchored `^git -C .* diff.*$`, and that pattern also matches `git -C /x push; git diff`. The OpenCode patterns stay narrow ([ADR 0002](docs/adr/0002-narrow-opencode-push-pattern.md)), and this widening is not permitted, so the OpenCode map stays as it was. An OpenCode verifier therefore cannot make the copy, and it reports the red proof as a command it cannot run.
- The OpenCode verifier allows no `getOnlyCommands` entry. A GET-only rule examines each argument for a method flag, a body flag and an output flag, and an OpenCode pattern cannot examine arguments. A GET-only command is therefore a deny on OpenCode, bare or with arguments, and a verifier there reports it as a command it cannot run. On Claude Code the guard also cannot see an argument that a shell expansion builds (the guard reads text).
- The OpenCode verifier map has no `nix develop` rule. A `nix develop * -c *` pattern cannot test the command after `-c`, so it would permit every command in a dev shell. An OpenCode verifier reports a dev-shell gate as a command it cannot run.
- The OpenCode verifier map does not divide a compound command either, for the same reason as the specialist map: OpenCode v1.18.31 hands the permission check the raw command. `git status && git commit -m x` is one deny on Claude Code and one allow on OpenCode.
- Frontmatter hooks in a project-scope agent run only after the workspace trust dialog is accepted for the folder the agent file came from, and a `-p` session does not count as accepting it. A project-tier verifier in a folder that is not trusted therefore has a Bash tool with no guard. The global tier is user-scope and it is not affected.
- The two push guards are not equal, and no pattern makes them equal. Measured 2026-09-19: the Claude Code hook applies an unanchored regular expression to the raw command, so it also stops `git add . && git push`, `git  push` with two spaces, and `GIT_DIR=/x git push`. An OpenCode pattern is an anchored glob, and OpenCode v1.18.31 hands the permission check the raw command with no split at `&&` (`packages/core/src/tool/bash.ts`, which carries a TODO for a tree-sitter parser), so OpenCode allows all three. Do not widen the OpenCode pattern to `*git push*` ([ADR 0002](docs/adr/0002-narrow-opencode-push-pattern.md)). The residual risk is an OpenCode specialist that pushes from inside a compound command.
- Two files with one name in one directory resolve by read order. The renderer refuses to produce that; the validator reports it in hand-written sets.
- A turn cap is a cost stop, not a lost deliverable. Claude Code 2.1.267, measured 2026-09-19: at `maxTurns` the Agent tool result gives only a harness NOTE and the agent's `agentId`. The agent's own text is absent. The status is still `completed`, and `SubagentStop` does not fire. A `SendMessage` to that `agentId` starts the same transcript again and keeps its context, and the cap applies again on each resume. OpenCode at `steps` makes the agent write a summary with the tasks that remain, and it documents no resume. Set a cap to bound cost, and resume the agent when it hits the cap.
