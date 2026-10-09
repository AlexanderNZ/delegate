# Glossary

This page explains the terms that the docs use. The entries are in alphabetical order. Each entry says what the term is and why it is important. Then it links the page that explains the term in full. When the docs use more than one word for a concept, the entry has the preferred word, and a line "Not:" lists the other words. For the kit at a glance, read [the overview](overview.md) first.

## ACCEPT

ACCEPT is the verdict that a verifier gives when the work does what the ticket asks. Only an ACCEPT moves the run branch, so work that nobody checked cannot reach it. See [the verifier step](reference/run.md#the-verifier-step).

## Adapter

An adapter is a Python object that drives one harness through the headless command line of that harness. The engine is the same for each harness, so a new harness needs only a new adapter. See [the adapter interface](reference/run.md#the-adapter-interface).

## ADR

An ADR (architecture decision record) is a short file that gives the context, the decision and the consequences of one design decision. A contributor can read the reason for a rule without access to a private tracker. See [the decision records](explanation/decision-records.md).

## Agent pair

An agent pair is a specialist and its verifier, which one declaration renders for each harness. The two halves come from one file, so they cannot drift apart.

Not: pair, specialist-verifier pair. Write "agent pair" in full.

See [why each specialist has a verifier twin](explanation/why-each-specialist-has-a-twin.md).

## Allowlist

The allowlist is the file in the kit that names each permitted exception to the neutrality check. Each entry is a permanent exception with a reason, so the list stays short and visible. It is not the list of commands of the [verifier guard](#verifier-guard). See [the neutrality check](../skills/agent-definitions/SKILL.md#neutrality-check).

## Anchoring

Anchoring is the effect of a report on a reviewer: the reviewer looks where the report points, and not at what the report omits. The verifier is blind to prevent anchoring. See [why the verifier is blind](explanation/why-the-verifier-is-blind.md#the-report-is-a-claim).

## ASD-STE100

ASD-STE100 Simplified Technical English is a standard of controlled English: short sentences, active voice, and one meaning for each word. A repository can select it as the output language of its agents, because ambiguous text from one agent causes a misparse in the next agent.

Not: STE. Use "STE" only for the option value `ste`.

See [the output language](../skills/agent-delegation/SKILL.md#output-language-asd-ste100-simplified-technical-english).

## `assure`

`assure` is the mode that verifies each ticket branch at once, before the next ticket builds on it. It finds a defect early, and it costs more tokens and more time than `economy`. See [the mode trade-off](explanation/the-mode-trade-off.md#what-each-mode-gives-up).

## Base branch

The base branch is the branch that a run starts from, for example `main`. The engine never merges into it, because the merge is an action of the coordinator. See [the fields of a workflow](reference/workflow.md#top-level-fields).

## Blind

A blind verifier gets the ticket and the diff, and never the report of the specialist. The report is a claim, and a verifier that reads it looks only where it points. See [why the verifier is blind](explanation/why-the-verifier-is-blind.md).

## Blocker

A blocker is a ticket that must be built before another ticket can start. The engine builds the tickets in the order that the blockers set, and it skips a ticket whose blocker failed.

Not: blocking edge, dependency. Use `blocked-by` only for the field of a workflow.

See [the tickets of a workflow](reference/workflow.md#tickets).

## Bootstrap

Bootstrap is the command `delegate bootstrap`, which writes the context skill, the declaration and the agent pair of a repository from one set of arguments. The docs and the agents come from the same answers, so they cannot drift apart. See [the bootstrap rules](../skills/agent-definitions/SKILL.md#bootstrap).

## Brief

A brief is the prompt that an agent gets for one task: the ticket, the file boundary, the gates and the report path. The engine writes each brief, so no agent gets a brief that a person wrote by hand. See [the specialist brief](reference/run.md#the-specialist-brief).

## Brief generator

The brief generator (`delegate brief`, or `verifier-brief`) builds the brief of a verifier from the task, the diff and the gates. It never puts a line of the specialist report in the brief, so the verifier stays blind. See [the brief reference](reference/commands.md#delegate-brief).

## Built

Built is the state of a ticket that passed each part of its step. Together with the verdict ACCEPT, it tells you that the ticket is done and checked. See [the states of `delegate status`](reference/status-and-watch.md#delegate-status).

## Chain

A chain is the sequence of tickets in `economy` mode: each ticket branch starts from the branch of the previous ticket. One verifier checks each stack at the end of the chain, so a long chain costs fewer tokens. See [the economy chain](reference/run.md#the-economy-chain).

## Context skill

A context skill is the skill that `delegate bootstrap` writes for a repository. It reads the reference documents of the repository when the agent loads it, so no copy of a document goes out of date.

Not: repo-context skill.

See [the bootstrap rules](../skills/agent-definitions/SKILL.md#bootstrap).

## Continuation

A continuation is a new run of a specialist in the same worktree, after the specialist stopped capped or failed, or left a red gate. The commits stay, so no finished work is lost, and each mode sets a limit. See [the continuation](reference/run.md#the-continuation).

## Contract test

A contract test replays a recorded event stream through a stand-in harness command, and checks the result of the adapter. It shows that the adapter reads the stream of the real harness, with no model and no tokens. See [the contract-test pattern](how-to/add-a-harness-adapter.md#the-contract-test-pattern).

## Coordinator

The coordinator is the person who directs the work, from a terminal or from an interactive harness session. The coordinator owns each action that the engine never does: the merge, the push, and the close of a ticket. See [the roles](../skills/agent-delegation/SKILL.md#roles).

## Declaration

A declaration is a TOML file that describes an agent pair: the domain, the tier, the skills, the references and the gate commands. The renderer writes both halves of the pair from it, for each harness. See [what a declaration is](../skills/agent-definitions/SKILL.md#what-a-declaration-is).

## Delegation document

The delegation document is `docs/agents/delegation.md` in a repository: the gates, the hotspots and the rules that bind each agent. The brief generator reads the gates from it, so the verifier runs the gates of that repository.

Not: delegation doc.

See [how to write it](how-to/bootstrap-a-single-stack-repository.md#1-write-the-delegation-document).

## Delta

The delta is the diff from the rejected commit to the tip of the branch after a fix-up. The fix-up verifier reads only the delta, so it does not read again the work that the first verifier checked. See [the fix-up round](reference/run.md#the-fix-up-round).

## Denylist

The denylist is a private list of terms that must not appear in the kit, for example the names of persons and companies. It stays outside the repository, and the neutrality check reads it from a path. See [the neutrality check](../skills/agent-definitions/SKILL.md#neutrality-check).

## `economy`

`economy` is the mode that builds the tickets as a chain and verifies once for each stack at the end. It costs the fewest tokens, and a defect shows only at the end of the chain. See [the mode trade-off](explanation/the-mode-trade-off.md#what-each-mode-gives-up).

## End state

The end state is how an agent run stopped, as the adapter reads it from the event stream: `finished`, `failed` or `capped`. The engine uses it to decide whether the step goes on, continues the specialist, or fails. See [the adapter interface](reference/run.md#the-adapter-interface).

## Engine

The engine is the program behind `delegate run`. It makes the worktrees, spawns the agents through an adapter, runs the gates, writes the briefs, and records each step in the journal. The most important rules are in code, not in a model session that can forget them.

Not: workflow engine. Use the long form only in a title.

See [the `delegate run` reference](reference/run.md).

## Event stream

The event stream is the line-delimited JSON that a harness writes in a headless run. The adapter keeps a copy beside the report. A recorded stream is an event stream that a test replays, and it is the base of each contract test.

Not: stream, fixture. Say "recorded stream" for a stream that a test replays.

See [the event stream of the `claude-code` adapter](reference/claude-code-adapter.md#the-event-stream).

## Fail closed

Fail closed is the behaviour of a guard that blocks a call that it cannot check, for example a call with no command text. The cost of a closed failure is a lost convenience, not a hole in the guard. See [how I read the limits](explanation/the-enforcement-model-and-its-limits.md#how-i-read-the-limits).

## File boundary

The file boundary is the part of the brief that names where the specialist works and which paths it must not change. The ticket holds no file path, so the engine adds the boundary when it starts the step. See [the specialist brief](reference/run.md#the-specialist-brief).

## Finding

A finding is one defect that a verifier reports in its verdict. A REJECT needs at least one finding, and the fix-up specialist gets the findings verbatim. A finding is not a [finding code](#finding-code) of the validator, and not a hotspot finding. See [the verdict report](reference/run.md#the-verdict-report).

## Finding code

A finding code is the code that the validator writes for one problem in a rendered agent file, for example `MISSING_TWIN`. Each code names one rule of a harness schema, so you know what to correct. See [the finding codes](reference/commands.md#the-finding-codes).

## Fix-up

A fix-up is the round that follows a REJECT: the specialist adds a new commit for the findings, and a fresh verifier checks the delta. A fix-up never amends, because an amend destroys the delta. Each mode has a limit of rounds.

Not: fixup, fix-up pass, scoped fix-up. Use `fixup` only in the command `delegate brief fixup`.

See [the fix-up round](reference/run.md#the-fix-up-round).

## Gate

A gate is a command that proves that a change is good in a repository, for example the test suite or the build. The engine runs each gate itself, so the evidence never comes from the agent that wrote the code. The verifier can run the gates of its repository, and no other build command.

Not: gate command. Use "gate command" only for the option `--gate-command` and the key `gateCommands`.

See [the stacks of a workflow](reference/workflow.md#stacks).

## Gateway

A gateway is a service that serves models under its own names, for example the LLM gateway of a company. A tier file points each tier at a model of the gateway, so no workflow changes. See [how to point the tiers at a gateway](how-to/point-the-tiers-at-a-gateway.md).

## Generated section

A generated section is the part of a reference page between the `generated:begin` and `generated:end` markers, which `delegate docs` writes from the code. A test fails when a section differs from the code, so the reference cannot drift. See [`delegate docs`](reference/docs.md).

## GET-only command

A GET-only command is a command that the verifier can run only while it reads, for example a script that sends an HTTP GET. The guard denies it when an argument sets a method, a body or an output file. See [gate commands](../skills/agent-definitions/SKILL.md#gate-commands).

## Harness

A harness is the tool that runs a coding agent, for example Claude Code, OpenCode or the `agent` CLI of Cursor. The engine drives each harness through an adapter, so the protocol is the same in every harness. See [the adapters](reference/workflow.md#adapters).

## Harness hook

A harness hook is a guard in an agent file that the harness runs before a tool call: a push guard for the specialist, and the verifier guard. It is a second guard, because a hook does not always run in a headless run.

Not: frontmatter hook.

See [why git, and not the harness](explanation/the-enforcement-model-and-its-limits.md#why-git-and-not-the-harness).

## Headless

A headless run is a harness run with no interactive terminal: the harness takes a prompt, writes its events, and exits. The engine drives each harness this way, so a run needs no person at the terminal of each agent.

Not: print mode.

See [the `claude-code` command](reference/claude-code-adapter.md#the-command).

## Hotspot

A hotspot is a path that only the coordinator changes, for example the licence or the CI configuration. The engine stops a step whose diff touches a hotspot, and no verifier starts.

Not: single-writer file. A hotspot finding is the record of a diff that touches a hotspot.

See [the hotspot guard](reference/run.md#the-hotspot-guard).

## Invariant

An invariant is one of the seven rules that hold in every mode, for example "the verifier is blind". No setting changes an invariant, so a cheaper mode never gives a weaker check. The [worktree invariant](#worktree-invariant) is a different check. See [the invariants of every mode](../skills/agent-delegation/SKILL.md#the-invariants-of-every-mode).

## Journal

The journal is the append-only JSONL file of a run, with one line for each event. The engine resumes a stopped run from it, and `delegate status` and `delegate watch` read it. See [the journal](reference/run.md#the-journal).

## Live smoke run

A live smoke run is one run of an adapter against the real harness on a real machine. The reference page records the harness version and the date. It shows what a recorded stream cannot show: that the real harness gives that stream. See [the live smoke run](reference/claude-code-adapter.md#the-live-smoke-run).

## Mode

The mode of a run is `assure` or `economy`. It sets when the verifier runs, the tier of the specialist, and the limits, and it never changes an invariant. The [verifier mode](#verifier-mode) is a different term. See [the mode trade-off](explanation/the-mode-trade-off.md).

## Neutrality check

The neutrality check is a test that fails when a term of the denylist appears in the kit. The kit holds no personal or company value, so each repository keeps its own values in its own configuration. See [the neutrality check](../skills/agent-definitions/SKILL.md#neutrality-check).

## Protocol

The protocol is the set of rules for delegation in the `agent-delegation` skill: the roles, the brief template, blind verification, fix-ups and hotspots. The engine applies the same rules, and you can apply them by hand for one task. See [the agent-delegation skill](../skills/agent-delegation/SKILL.md).

## Push guard

The push guard is a `pre-push` hook that the engine sets in each worktree that it makes, and the hook refuses each push. It uses git, so it holds in every harness, also in a headless run.

Not: pre-push hook, push hook. Say "push guard" for the guard, and `pre-push` for the git hook that holds it.

See [the push guard](reference/run.md#the-push-guard).

## Red proof

A red proof shows that a test can fail: the verifier breaks the code that the test covers, and the test goes red. A test that passes before and after a change protects nothing. See [why the verifier runs the gates](explanation/why-the-verifier-is-blind.md#why-the-verifier-runs-the-gates-as-well).

## REJECT

REJECT is the verdict that a verifier gives when it finds at least one defect. The run branch does not move, and a fix-up round starts. See [the fix-up round](reference/run.md#the-fix-up-round).

## Renderer

The renderer (`delegate render`) writes the agent files of each harness from a declaration. Nobody writes an agent file by hand, so the two halves of a pair stay in step. See [the rendering rules](../skills/agent-definitions/SKILL.md#rendering-rules).

## Report

The report is the JSON file that the specialist writes at the end of its run: its status, its commits, and its claim about the gates. The engine checks its form, and it never shows it to the verifier.

Not: specialist report. The file of the verifier is the [verdict](#verdict) report.

See [the specialist report](reference/run.md#the-specialist-report).

## Resume

A resume (`delegate run --resume`) goes on with a stopped run from its journal, and it never builds a finished step again. An adapter can also resume a harness session for a continuation, which is a different use of the word. See [the resume](reference/run.md#the-resume).

## Run

A run is one execution of `delegate run` over a workflow, with its own run id and its own journal. A run that stops goes on with a resume. See [what a run does](reference/run.md#what-a-run-does).

## Run branch

The run branch is the branch that the engine owns, and only accepted work lands on it. The coordinator merges the run branch into the base branch. See [the fields of a workflow](reference/workflow.md#top-level-fields).

## Run lock

The run lock is a file that stops a second run on one run branch. Two runs cannot race on one branch, and `--break-lock` removes the lock of a process that no longer exists. See [the run lock](reference/run.md#the-run-lock).

## Session

A session is one conversation of a harness with an agent, with a session id. The adapter resumes the session for a continuation when the harness supports it, so the agent keeps its context. See [the continuation](reference/run.md#the-continuation).

## Skill

A skill is a directory with a `SKILL.md` file of instructions that an agent loads. The kit ships two skills, and each agent pair carries every skill of its stack. See [how to write a skill](how-to/bootstrap-a-single-stack-repository.md#2-write-the-skill-with-your-style-rules).

## Skip

A skip is the state of a ticket whose blocker is not built. The engine makes no worktree for the ticket and records the reason, so the journal shows why the ticket did not run. See [the skip rule](reference/run.md#the-skip-rule).

## Spec

A spec is the plan of a piece of work: the problem, the solution and the decisions, from which the tickets come. The kit takes a spec and its tickets as input, and it replaces only the build step. See [how to use the kit after `to-spec` and `to-tickets`](how-to/use-the-kit-after-to-spec-and-to-tickets.md).

## Specialist

The specialist is the agent that builds one ticket, in its own worktree, inside its file boundary. It commits and never pushes, and its report is a claim that the engine checks.

Not: implementer. Use "implementer" only for the agent of another tool.

See [the roles](../skills/agent-delegation/SKILL.md#roles).

## Stack

A stack is one technology of a repository, for example a Python package or a web front end, with its own gates and hotspots. Each stack has its own agent pair, so each specialist carries every skill of its stack. See [the stacks of a workflow](reference/workflow.md#stacks).

## State directory

The state directory is the directory `delegate/` in the git directory of the repository. The engine writes the journals, the reports, the worktrees and the locks there, and the working tree stays clean. See [where the engine writes](reference/run.md#where-the-engine-writes).

## Step

A step is the work of the engine for one ticket: the worktree, the specialist, the checks, the gates and, in `assure` mode, the verifier. A failed step does not end the run. See [what a run does](reference/run.md#what-a-run-does).

## Temporary copy

The temporary copy is a clone of the ticket branch that the engine makes for the verifier. The verifier can break the copy for a red proof, and the real worktree stays as it was.

Not: verifier copy, copy.

See [the verifier step](reference/run.md#the-verifier-step).

## Three-dot diff

A three-dot diff (`git diff <base>...<branch>`) shows the changes of a branch since the point where it left the base. The verifier then sees only the work of the ticket. See [the verifier step](reference/run.md#the-verifier-step).

## Ticket

A ticket is one unit of work, written as behaviour, with the tickets that block it. It holds no file path, because the engine adds the file boundary when it starts the step.

Not: task, issue. Use "task" only for the section of a brief that holds the ticket text.

See [the tickets of a workflow](reference/workflow.md#tickets).

## Tier

A tier is a strength that a workflow names in place of a model: `strong`, `standard`, `cheap` or `verifier`. The tier table maps it to a model for each harness, so a change of model changes no workflow.

Not: strength. The tier `verifier` is not the [verifier](#verifier) agent.

See [the tier overrides](reference/workflow.md#tier-overrides).

## Tier file

A tier file is your own copy of the tier table, which you give with `--tiers`. It points each tier at the models that you can use, for example the models of a gateway. See [how to point the tiers at a gateway](how-to/point-the-tiers-at-a-gateway.md).

## Tier table

The tier table (`tiers.toml`) maps each tier to one model for each harness, and lists the models that the validator accepts. Each adapter reads its own column, so an adapter never chooses a model. See [the tier table](../skills/agent-definitions/SKILL.md#the-tier-table).

## `to-spec`

`to-spec` is a planning skill of [mattpocock/skills](https://github.com/mattpocock/skills) that writes a spec. The kit takes its output as input, and replaces only the build step. See [how to use the kit after `to-spec` and `to-tickets`](how-to/use-the-kit-after-to-spec-and-to-tickets.md).

## `to-tickets`

`to-tickets` is a planning skill of mattpocock/skills that writes tickets with blockers from a spec. The kit builds those tickets in the order that the blockers set. See [how to use the kit after `to-spec` and `to-tickets`](how-to/use-the-kit-after-to-spec-and-to-tickets.md).

## Turn cap

A turn cap is the maximum number of turns of an agent run (`maxTurns` on Claude Code, `steps` on OpenCode). It is a stop for cost, not a lost deliverable, because the engine continues a capped specialist in the same worktree.

Not: step cap, max turns. Use "step cap" only for the OpenCode `steps` setting.

See [the limits of the guards](explanation/the-enforcement-model-and-its-limits.md#the-limits-of-the-guards).

## Validator

The validator (`delegate validate`) checks rendered agent files against the schema of each harness, and writes a finding code for each problem. A harness skips some bad agent files with no error, so the validator makes the problem visible. See [`delegate validate`](reference/commands.md#delegate-validate).

## Verdict

The verdict is the result of a verifier: ACCEPT or REJECT, with the evidence for each acceptance criterion, the gate output, the findings, and the items that it cannot verify. The engine reads it from the verdict report. See [the verdict report](reference/run.md#the-verdict-report).

## Verifier

The verifier is the agent that checks the work of a specialist and gives a verdict. It holds the same skills as its specialist, it is blind and read-only, and it runs on the tier `verifier` in every mode.

Not: twin, verifier twin, reviewer. Say "verifier". Use "twin" only where the text is about the skills that the verifier shares with its specialist.

See [the roles](../skills/agent-delegation/SKILL.md#roles).

## Verifier guard

The verifier guard is the hook in each rendered verifier that permits a fixed list of commands. The list holds read commands, temp writes, and the gates of the repository. It is a contract for the verifier, not a sandbox, so the engine also checks the worktree.

Not: command guard.

See [the verifier's shell](../skills/agent-definitions/SKILL.md#the-verifiers-shell).

## Verifier mode

The verifier mode is `full` or `fix-up`, and the shape of the brief sets it. A full verifier reads the whole diff, and a fix-up verifier checks only the findings and the delta. See [the verifier modes](../skills/agent-definitions/SKILL.md#verifier-modes).

## Workflow

A workflow is the TOML file that describes one run: the base branch, the run branch, the mode, the adapter, the stacks and the tickets. The coordinator writes it, and `delegate run --dry-run` checks it before a build. See [the workflow file](reference/workflow.md).

## Worktree

A worktree is a git working tree with its own branch, which shares the repository with the main checkout. Each specialist works in its own worktree, so its work stays apart from the main checkout and from the other tickets.

Not: working copy, checkout, clone. A clone is the [temporary copy](#temporary-copy) of the verifier.

See [what a run does](reference/run.md#what-a-run-does).

## Worktree invariant

The worktree invariant is the check that the verifier left the real worktree as it was: the same HEAD and the same status. A difference is an invariant violation, and the run halts, whatever the verdict.

Not: unchanged-worktree check, invariant check.

See [the worktree invariant](reference/run.md#the-worktree-invariant).
