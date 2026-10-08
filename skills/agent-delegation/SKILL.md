---
name: agent-delegation
description: Protocol for delegating implementation work to subagents — the
  coordinator/specialist/verifier role split, the delegation brief template,
  blind adversarial verification, and sequential merge. Use when spawning
  agents to implement work, planning multi-agent work, writing a delegation
  brief, or bootstrapping a repo's gates and its specialist-verifier pair.
  Repos may add a docs/agents/delegation.md with repo-specific gates.
---

# Agent delegation protocol

Portable protocol for handing implementation work to subagents. Repo-specific
gates (verification commands, hotspot files, read-first docs) live in that
repo's `docs/agents/delegation.md`, referenced from its CLAUDE.md; this skill
is the part that never changes.

## Before you delegate: is the repo set up?
If the repo has no `docs/agents/delegation.md`, do NOT guess the gates or skip
them — bootstrap it first (below), then delegate. Delegating without the repo's
gates is how an agent runs the wrong test and reports "done".

## Bootstrapping a repo's gates (interview the owner)
When there's no `docs/agents/delegation.md`, gather the slots the brief template
can't fill generically. Infer sensible defaults from the repo first (build tool,
test runner, CI config, deploy wiring) and ask the owner to confirm or correct —
a compact confirm-my-guesses prompt beats a blank interrogation. Elicit:
- **Verification gates** — the exact command(s) that prove a change is good
  here (typecheck / test / lint / build) and how to confirm at runtime (drive
  the flow, hit the endpoint). "What do you run before you'd trust a change?"
- **Test quality bar** — the coverage metric, if the repo has one (which
  assembly/scope, what threshold, how it's measured and where it's enforced),
  and what a *useful* test looks like here: what layer tests drive (public
  API vs internals), the regression-test rule for bug fixes (failing test
  first; mutation-verify backfilled tests by reverting the fix and watching
  them fail), and any surfaces that are exempt (e.g. presentation code).
  A coverage number without the useful-test definition invites
  implementation-mirroring tests that inflate the metric.
- **Breakage / stop rule** — what "red main" means here: does a push deploy? is
  there CI? what state halts further delegation until fixed?
- **Hotspots (single-writer)** — files or resources only the coordinator may
  touch: secrets, config, deploy scripts, schema/migrations, CI, shared live
  targets.
- **Read-first docs** — the governing docs an agent must read before touching
  this repo.
- **Issue tracker** — where tasks/specs live (Gitea `tea`? GitHub `gh`? none —
  solo repo?) and how to read/close them.
- **Model routing** — any local gateway or alias wrinkles.
- **Domain and stacks** — the repo's domain in one sentence, each technology
  stack it contains (e.g. Spring Boot backend, React frontend, Astro sales
  site), and the full set of skills per stack. A project agent must carry both
  the domain context and every stack skill its specialist needs. Agent
  definitions do not compose: a single-stack repo gets one pair with every
  skill; a monorepo gets one pair per stack, each self-sufficient. The domain
  rules a specialist must hold go into the prompt.
Write the answers verbatim into `docs/agents/delegation.md` (this skill's shape,
the repo's values), reference it from CLAUDE.md § Delegation, and if the repo is
a docs site, add it to the nav.

### Then emit the repo's agents, in the same pass
The delegation doc and the repo's agents are one artifact set. Emit both from
the same interview answers. The two cannot then drift apart.

The `agent-definitions` skill owns the declaration schema, the per-harness
templates, the tier table, and the finding codes. Read it before you run the
command below. Its targets are Claude Code and OpenCode. Codex is out of scope
and receives MCP configuration only.

One command writes the repo-context skill, the declaration beside it, and the
rendered pair. It validates the pair before it copies an agent file.

```bash
agent-definitions bootstrap --repo . --name <repo> --tier strong \
  --domain '<the domain, in one sentence>' \
  --skill <library-skill> [--skill <library-skill> ...] \
  --reference '`docs/agents/delegation.md` — the gates and the hotspots' \
  [--reference ... ] \
  --gate-command '<a gate command>' [--gate-command ...] \
  --prompt-file <the file with the repo domain rules> --max-turns 80
```

The interview answers are the arguments. Each `--skill` is a library skill; the
command puts `<repo>-context` first by itself. Each `--reference` is a
read-first document with a note, and the path is the first backticked word.
`--prompt-file` holds the repo's domain rules: the gates, the hotspots, the
stop rule, the tracker, and the output language — everything no harness
enforces. Run the command with `--dry-run` first, and read every file before it
lands.

Four rules the command applies for you:

1. **The context skill goes to `.claude/skills/<repo>-context/`.** Claude Code
   reads a project skill from that path and from nowhere else. The body of the
   skill reads the repo's reference documents at load time, so no copy of a
   document goes stale. Each read is one plain `git grep` command with a `:/`
   path, so it works from the repo root and from each subdirectory. OpenCode preloads no skill at all, so the OpenCode half
   of the pair tells the agent to load it.
2. **The declaration stays beside that skill**, at
   `.claude/skills/<repo>-context/agents.toml`. A later change to the repo's
   docs re-renders the pair from one file.
3. **The pair is validated in a temporary directory**, never in the harness
   directory. A project agents directory can hold hand-written files that fail
   the schema for reasons outside this bootstrap. A finding is a stop: the
   command prints it, exits 1, and writes no agent file. Correct the arguments
   and run the command again.
4. **Only `ok` copies.** Claude Code files go to `.claude/agents/`, OpenCode
   files to `.opencode/agents/`.

Commit the agent files, the context skill, and the declaration with the
delegation doc. A fresh clone then holds them, and a reviewer sees them. The
command does not touch `docs/agents/delegation.md`.

A repo that keeps its skills in a different directory is the exception. An
example is a configuration repo whose deploy step copies a `skills/` directory
into the home directories. No harness reads that directory as a project skill
path. In such a repo, pass `--skills-root <dir>` and commit a symlink beside
its neighbours: `.claude/skills/<repo>-context -> ../../<dir>/<repo>-context`.
`git add` the symlink; git stores it with mode 120000. Without that symlink the
harness never finds the skill, and the preload drops it in silence. The command
makes no symlink. Such a repo records the exception in its
`docs/agents/delegation.md`.

Names carry the repo prefix: the renderer writes `<repo>-specialist` and
`<repo>-verifier`. Claude Code replaces a global agent of the same name
entirely, so the prefix prevents that collision.

### Every project agent must be self-sufficient

Agent definitions do not compose. A harness loads one agent file per task. It
cannot combine a project agent with a global agent, merge two skill lists, or
layer a domain context onto a stack context at runtime. A project agent that
omits a stack skill forces the coordinator to choose: use the project agent and
lose the stack discipline, or use the global agent and lose the domain context.
Both choices produce worse work.

The rule: a project agent carries every skill the specialist needs for any task
it will receive. That means both the project's domain context and the
technology skills for its stack. Read the delegation doc's skill routing table
and the repo's stack. Every skill that appears for that stack goes into the
declaration. The result is one agent that knows the domain and the stack. The
global agents (`java-spring-specialist`, `react-specialist`) stay as fallbacks
for repos that have no project agent, or for generic cross-repo work.

### Single-stack repos

A repo with one stack keeps one pair. Its declaration lists the context skill
and every stack skill. No structural change is needed — just ensure the skills
list is complete.

### Per-stack agents for monorepos

A monorepo holds more than one technology stack. One agent cannot carry every
skill for every stack without bloating the context.

The pattern: one project agent per stack. Every agent in the repo shares the
same context skill, the same references, and the same tier. The agents differ
in their description, their skills list, and their prompt.

The suffix names the stack, not the project:

| Suffix | Stack |
|--------|-------|
| `-api` | a backend, for example Spring Boot |
| `-app` | a frontend, for example React |
| `-sales` | a sales or marketing site, for example Astro |

Each agent prepends `<repo>-context` to its skills list. The stack skills are
the same across every repo that uses that stack. Two repos `shop` and `blog`
with a Spring Boot backend get a `shop-api` and a `blog-api` with the same
stack skills; the only difference is the context skill and the prompt.

Keep one shared skill set for each suffix, and write it down once, where the
owner of the skill library keeps the delegation values. Each set holds the
design and test skills for that stack, the skill for the repo's issue tracker,
and the skills that the repo vendors for that stack.

A backend in a different framework (e.g. FastAPI) uses the `-api` suffix too.
It drops the framework-specific skills. The domain-design skills stay.

The `bootstrap` command writes one pair per run. For a monorepo, write the
declaration by hand as a single TOML file with multiple `[agents.<name>]`
tables, then render and validate:

```bash
agent-definitions render .claude/skills/<repo>-context/agents.toml -o /tmp/agents
agent-definitions validate /tmp/agents \
  --skills-dir .claude/skills --user-skills-dir ~/.claude/skills
```

Pass `--user-skills-dir` to find skills that are installed in the user
directory. A skill found in either the project directory or the user directory
passes. A skill absent from both is `MISSING_SKILL`.

The brief names the per-stack agent, not the global fallback: "Agent:
`<repo>-api-specialist` / `<repo>-api-verifier`" for a backend ticket. The
global agents stay as fallbacks for repos that have no project agent, or for
generic cross-repo work.

A single-stack repo keeps one pair. Add the stack skills to its existing
declaration. The per-stack split adds value only when the repo holds more than
one stack.

## Roles
- **Coordinator** (interactive session): decomposes work, writes briefs,
  runs the gates itself, merges sequentially. Never implements in parallel
  with a specialist it supervises. Owns any irreversible or shared-resource
  action (deploys, prod writes). Before it presents a task as user-only,
  the coordinator verifies that it lacks the CLI/API access to do it. If
  the tool is available (fly CLI, REST API, MCP server), the coordinator
  does the work or delegates it to a specialist.
- **Specialist** (spawned agent): one task, isolated git worktree, declared
  file boundary. Does not push. Single responsibility — expanding into adjacent
  work is a defect, not initiative. If a tool, file, or source named in the
  brief is unavailable, this is a stop-and-report condition. Do not substitute
  generated content for canonical sources. Report the blocker with what you
  need and wait for the coordinator to provide it. This is not a judgment call.
- **Verifier** (separate agent or /code-review): every specialist branch gets
  a verifier — no merge without an independent verifier ACCEPT. The coordinator
  MUST NOT review specialist diffs itself; the coordinator reads the verifier's
  report. Coordinator-authored review is a protocol violation. Spawn the
  verifier immediately when a specialist reports — do not batch verifier spawns
  or wait for other specialists to finish. The coordinator rebases the
  specialist's branch onto current main BEFORE it spawns the verifier. A
  verifier that reviews a pre-rebase diff sees unrelated changes as removals,
  which produces false positives. For tasks with visual specifications, the
  verifier compares rendered output against the source files on disk, not
  against the brief's prose description. Verify BLIND: give the verifier the
  task + diff, never the specialist's report; anchoring on the implementer's
  framing kills adversarial value.
  For domain work the verifier is the specialist's pair twin —
  `<domain>-verifier` against `<domain>-specialist` — never a generic agent.
  The twin holds the same skills, the same domain references, and a read-only
  tool set. A verifier without the specialist's skills cannot judge the diff.
  It reads the code and misses the discipline. A generic verifier is correct
  only for docs and configuration work, where no domain pair applies.

### With agent teams on, spawn pair agents as subagents
When agent teams are on (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`), an Agent
call with a `name` starts a teammate, not a subagent. A teammate ignores the
agent file's `isolation` and `skills`. It runs in the main checkout, without
its worktree and without its preloaded skills.

- **Specialist.** Pass `isolation: "worktree"` on the Agent call itself. The
  frontmatter value is not sufficient. With `isolation` on the call, the agent
  starts as a subagent, and its `name` stays usable for SendMessage.
- **Verifier.** Do not pass a `name`. A verifier needs no worktree, and an
  unnamed call always starts a subagent. Resume it by its agentId.
- **Recognise a teammate.** The spawn result says "Spawned successfully … will
  receive instructions via mailbox", not "Async agent launched". The agent has
  the TaskCreate and TaskList tools. Stop it and spawn it again correctly.

A subagent returns its final text in its completion notification, so a brief
does not need a "report via SendMessage" instruction. Only a brief for a
deliberate teammate spawn needs that instruction. If an agent goes idle with no
report, send it a message that asks for the report. Do not spawn it again.

Evidence: code.claude.com/docs/en/sub-agents § Subagent names, read
2026-09-24. Measured the same day with agent teams on: a named project
specialist with `isolation` on the call ran in its own worktree, with its
context skill preloaded, the push hook active, and no Task tools. An unnamed
project verifier also had its skill preloaded, its guard active, and no Task
tools. An earlier specialist, spawned with a name and no call `isolation`, ran
in the primary checkout on `main` (measured 2026-09-19).

### Confirm the permission mode before you spawn a pair agent
A pair agent preloads its context skill, and a context skill can run shell
blocks at load time. A subagent cannot ask for permission. In a permission
mode that does not allow those blocks, the agent fails at once with "Shell
command permission check failed".

- Before the first pair spawn, the coordinator confirms the session's
  permission mode.
- If the mode does not allow the load-time blocks, the coordinator tells the
  user and asks for a mode change.
- Do not spawn the agent again in a loop. Each new spawn in the same mode fails
  the same way.

### A capped specialist resumes — it never re-spawns
A specialist that reaches its turn cap stops mid-task. The harness reports the
run as a success, so the cap is easy to miss.

- **Recognise it.** The Agent tool result holds only a harness NOTE ("stopped
  at its N-turn limit … Send the agent a message (SendMessage) to let it
  continue") plus the agentId. The agent's own text is absent, `status` is
  `completed`, and no `SubagentStop` fires for that agent_id. Those two signals
  together mean a cap, not an error.
- **Resume it.** Send a message to that agentId. The same transcript, context,
  and worktree state continue, and the cap applies again to the resumed run.
  Repeat until real report text returns.
- **Never re-spawn.** A fresh Agent call for the same task starts the work over
  in a new context. It abandons the worktree the first run built.
- **In print mode, read every result event.** A background resume emits a
  second one. A parser that stops at the first result loses the report.
- **OpenCode differs.** At `steps` it forces the agent to write a summary with
  its remaining tasks, and it documents no resume. Treat that summary as a
  hand-off. Brief the next agent from it.

Evidence: a turn-cap experiment on Claude Code 2.1.267, 2026-09-19, with
the observations above.

## Output language: ASD-STE100 Simplified Technical English

This section applies only when a repo selects ASD-STE100 for agent output. A
repo selects it in its `docs/agents/delegation.md`, or with
`outputLanguage = "ste"` in its agent declarations (the `agent-definitions`
skill). The declaration default is `"none"`. A repo that selects no output
language skips this section.

In a repo that selects it, agent-written text follows a binding subset of
ASD-STE100 Simplified Technical English. The full standard defines ~900
approved words with one meaning each; this skill does not require the approved
dictionary. The rules below are the
binding subset.

Ambiguity in one agent's output causes a misparse in the next agent's input.
These rules prevent that failure.

### Scope

This section governs all text an agent produces during delegated work:
reports, commit messages, code comments, doc updates, and briefs.

Two exemptions apply:
- **Verbatim material.** Quoted gate output, error messages, tool output, and
  cited text are exempt. Do not rewrite verbatim material into STE.
- **Prose in a personal voice.** A repo's `docs/agents/delegation.md` can
  name a voice skill for prose that a person signs (blog posts, decision docs,
  review comments). Load that skill before you draft such a deliverable. When
  the voice skill governs the deliverable, STE does not apply. The two can
  share ground (no elegant variation, directness), but a personal voice can
  use rhythm variation, figurative language, and register blending that STE
  forbids. Do not blend them.

### Rules that bind

1. **One word, one meaning.** Use the same word for the same thing every time.
   Do not use synonyms for variety. ("Worktree" stays "worktree" — never
   "working copy", "checkout", or "clone".)
2. **Active voice, simple tenses.** Use present, past, or future only. Do not
   use progressive ("is running"), perfect ("has completed"), or subjunctive
   ("were it to fail"). Permitted modals: "can", "must", and "will" (simple
   future) only — not "may", "might", "could", "should", or "would". Use
   passive voice only when the actor is unknown or irrelevant.
3. **Short sentences.** Procedures: one instruction per sentence, 20 words
   maximum. Descriptions: 25 words maximum. A sentence that needs a semicolon
   is two sentences. Do not join two instructions with "and" or "then".
4. **No figurative language.** No metaphors, no idioms, no humor, no slang, no
   rhetorical questions in technical output. "The build failed" is literal.
   "The build is on fire" is not.
5. **No -ing verb forms.** Use the infinitive, imperative, or simple tense
   instead. The -ing form is permitted only as part of a technical name (e.g.
   "logging", "binding").
6. **Approved vocabulary + technical names.** Use common, unambiguous words.
   Domain-specific technical names (e.g. "worktree", "rebase", "aggregate",
   "flake") are always permitted. Prefer short common words over formal
   alternatives. Use verbs, not nominalisations ("verify", not "perform
   verification"). Do not use phrasal verbs ("start", not "spin up").
7. **Noun clusters: three words maximum.** "Git worktree path" is acceptable.
   "Isolated feature branch worktree merge protocol" is not. Restructure long
   clusters with prepositions.
8. **Paragraphs: six sentences maximum, one topic each.** Use numbered lists
   for sequential steps. Use bulleted lists for parallel items.
9. **Articles always.** Write "the file", not "file". Write "a branch", not
   "branch". Do not omit sentence parts to shorten a sentence.
10. **Explicit structure.** State the subject and main verb before any
    qualifier. Keep all sentence components explicit. Omitted words create
    ambiguity.

## Material handoff — coordinator prepares inputs before delegation

Worktree agents have a reduced tool set. MCP tools, DesignSync, and any
coordinator-scoped data source are unavailable to specialists. The
coordinator must verify tool availability before it references any tool in
a brief.

If a brief requires data from an external source (design project, API,
MCP server, external file), the coordinator materializes that data to
disk before it spawns the specialist. The brief references the on-disk
path, not the external tool.

The rule: **never name an external tool in a brief as a specialist
instruction.** Name the file path the coordinator wrote the data to.

Anti-pattern: "Use DesignSync to read the design tokens."
Correct: "Read the canonical design tokens from `.claude/handoff/tokens.css`."

## Brief template
Every section, every time — omitting one is how scope creep and invented values
happen.
1. **Task** — one unit; its issue (with comments) is the spec.
2. **Read first** — CLAUDE.md (name it even though it auto-loads), governing
   docs, the issue, the specs/comps the values derive from.
3. **File boundary** — paths the agent may touch; everything else read-only.
   Concurrent specialists need pairwise-disjoint boundaries. Hotspot files are
   single-writer (coordinator only).
4. **Constraints** — repo rules binding this task + task-specific ones.
4b. **Agent** — name the specialist that runs the task and the verifier twin
   that checks it. In a monorepo, name the per-stack agent that matches the
   task's stack: `<repo>-api-specialist` for a backend ticket,
   `<repo>-app-specialist` for a frontend ticket, `<repo>-sales-specialist`
   for a sales site ticket. In a single-stack repo, name the project pair:
   `<repo>-specialist` and `<repo>-verifier`. A global stack pair
   (`java-spring-specialist`, `react-specialist`) is the fallback for repos
   with no project agent, or for generic cross-repo work. The agent file
   carries the skills, the tool rules, and the turn cap, and its declaration
   carries the model tier. On Claude Code the file omits the model, so the
   spawn call names it: pass `model` on every Agent call, or the subagent
   inherits the coordinator's model. Effort follows that model through
   `modelSettings` unless the file declares its own. The brief names the
   agent and the model, and it states none of the rest, so it cannot omit
   one. Spawn the verifier half by
   name for the review; never a generic agent — see Roles. Docs and
   configuration work has no domain pair. The generic `docs-config` verifier
   covers it. An explicit "none — <reason>" stays valid for a task that no
   domain pair fits (a verbatim refactor, for one); silent omission is not.
5. **Acceptance criteria** — testable; "matches X" names X, "tests pass" names
   the suites.
5b. **Source artifacts** — for tasks with a visual specification, the
   coordinator provides the canonical source files on disk (design HTML,
   screenshots, token files). The brief references exact file paths. The
   acceptance criteria say "matches file X", not "matches the design."
   Do not describe a visual layout in prose and expect the specialist to
   reconstruct it. Prose descriptions of visual layouts lose fidelity at
   every transfer. Provide the actual source files.
6. **Verification gates** — the exact commands (from the repo's delegation doc).
7. **Report format** — branch/worktree, applied vs skipped (with reasons),
   VERBATIM gate output, every judgement call, anything unverifiable. "Done"
   without evidence is not a report.

Specialist git mechanics: isolated worktree, feature branch, never push. No
commit trailer is required. Serialize git ops across worktrees.

### Git commands run in the agent's own worktree
A subagent's shell can return to the primary checkout between two calls. A
`git checkout` or `git switch` there moves the coordinator's HEAD. A later
merge then lands on the wrong ref.

- An agent runs every git command from its own worktree. It starts each git
  command with `cd <worktree> &&`, or it checks `pwd` first.
- An agent never runs `git checkout`, `git switch`, or `git commit` in the
  primary checkout.
- Put both rules in each brief, under Constraints.

### A copy of a worktree is not a worktree
`cp -R` of a worktree also copies its `.git` file. That file points at the
gitdir of the real worktree. A `git add` or `git commit` in the copy then
writes the index and the branch of the real worktree.

1. After the copy, remove `<copy>/.git` before any git command in the copy.
2. If the copy needs its own repository, run `git init` in the copy after the
   removal.

## Merge protocol (sequential, coordinator-owned)
Before each merge and each push, run `git branch --show-current` in the
primary checkout. The output must be the base branch. Any other output is a
stop: find the cause before the merge. Read each push output too. "Everything
up-to-date" after a new merge is a stop. A hash in the pushed range that you
do not know is a stop.

1. Verifier pass on the branch. Generate the brief, never compose it by hand:
   `verifier-brief full --repo <worktree> --branch <name> --base main --task <text or @file>`.
   It emits the task, the three-dot diff `main...<branch>`, and the repo's
   gates. It carries no findings section, so the verifier runs a full pass.
   In a monorepo doc with one gate block for each stack, the brief carries the
   blocks whose path holds a changed file. Add `--stack <heading>` to select a
   block by name instead.
2. Rebase onto current main; rerun the gates after rebase — a clean textual
   merge proves nothing about semantics.
3. Merge, watch for breakage. A broken main/deploy halts all further delegation
   until fixed.
4. One branch at a time — never merge two agent branches without re-verifying
   between them.

## Fix-up protocol
A fix-up pass is scoped. Blind means the verifier does not see the specialist's
report. It does not mean the verifier reads everything again. A full re-read of
an already-accepted diff spends tokens and finds nothing.

When a verifier REJECTs:
1. Disregard the current verifier's ACCEPT/REJECT state.
2. Send the fix-up to the specialist with the verifier's specific findings. The
   specialist commits the fix-up as a NEW commit on top of the rejected commit.
   It never amends. An amend destroys the delta the scoped verifier reads.
3. Wait for the specialist to report the correction.
4. Write the first verifier's findings to a file, verbatim. Then generate the
   brief:
   ```bash
   verifier-brief fixup --repo <worktree> --branch <name> \
     --rejected <sha> --findings <file> [--authorised <text>] [--stack <heading>]
   ```
   It exits non-zero when the rejected commit is not an ancestor of the branch
   tip, and when the delta is empty. Both states mean the specialist rewrote
   history. Send it back; do not hand-build a brief around the loss.
5. Spawn a fresh verifier and give it that output, unchanged. Never a
   hand-composed brief. Never the specialist's report. The original verifier
   does not re-verify — fresh eyes catch what anchoring hides.
6. The scoped verifier checks three things and stops: each finding is fixed,
   with evidence; the delta touches nothing beyond the findings and anything
   `--authorised` marks; the gates and the first pass's automated checks are
   green again. It does not re-read the full diff.
7. A delta that reaches beyond the findings is a scope finding for you, not a
   verdict the verifier escalates on its own. You read it and decide whether a
   full pass follows.

`verifier-brief` ships with the `agent-definitions` package. An install of the
package puts both commands on PATH.

## Shared mutable resources are single-writer
Even with disjoint file boundaries, actions against a shared live resource (a
deploy target, a prod DB, an external service) must be serialized to the
coordinator — no file boundary prevents two concurrent deploys from racing.
