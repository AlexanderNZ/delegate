# The concepts behind the words

The glossary gives each term one short entry. Some terms need more room than an entry has, because the idea behind the word is what a reader needs. This page gives them that room. For each term I say what it is, why the kit has it, and what it changes in a [**run**](../glossary.md#run). Each section ends with a pointer to the reference page that gives the exact rules.

The terms are in the order in which a run meets them: the tools, the plan, the run, and the checks of the text.

## Harness

A [**harness**](../glossary.md#harness) is the program that runs a coding agent. It sends the prompt to a model, gives the model tools, and runs the tool calls that the model asks for. Claude Code, OpenCode and the `agent` CLI of Cursor are harnesses. A harness is not a model, and it is not the kit.

I keep the harness outside the [**engine**](../glossary.md#engine) on purpose. Harnesses change fast, and each one starts, selects an agent, and reports its end in its own way. The engine drives a harness through its [**headless**](../glossary.md#headless) command line, and an [**adapter**](../glossary.md#adapter) holds each detail that belongs to one harness. A new harness is a new adapter, and the [**protocol**](../glossary.md#protocol) does not change.

The guards follow the same rule. Git enforces them, because a git hook works in every harness, and a hook in the agent file works only in some. See [the enforcement model](the-enforcement-model-and-its-limits.md#why-git-and-not-the-harness). For the list of adapters, see [the adapters of the workflow file](../reference/workflow.md#adapters).

## Skill

A [**skill**](../glossary.md#skill) is a directory with a `SKILL.md` file. The file holds instructions, and the agent loads them when it works on the kind of task that the skill covers. A skill is plain Markdown, so it ties the instructions to no model and to no harness.

The kit uses skills in two ways. It ships two of its own: one holds the protocol, and one holds the rules of the [**renderer**](../glossary.md#renderer). And a repository adds its own skills, for example one for its style rules. The [**declaration**](../glossary.md#declaration) of an [**agent pair**](../glossary.md#agent-pair) lists the skills, and the renderer preloads the same list into both halves of the pair.

That shared list is the reason for the design. The [**verifier**](../glossary.md#verifier) holds every skill of the [**specialist**](../glossary.md#specialist), so it judges the work by the same standard. See [why each specialist has its own verifier](why-each-specialist-has-its-own-verifier.md#a-verifier-needs-the-skills-of-the-specialist), and [how to write the skill of a repository](../how-to/bootstrap-a-single-stack-repository.md#2-write-the-skill-with-your-style-rules).

## Session

A [**session**](../glossary.md#session) is one conversation of a harness with an agent. The harness keeps the messages of the conversation under a session id. With the id, a later command can go back into the same conversation.

The engine uses this for a [**continuation**](../glossary.md#continuation). A specialist that stops [**capped**](../glossary.md#end-state) or [**failed**](../glossary.md#end-state), or that leaves a red [**gate**](../glossary.md#gate), gets a second run. If the harness can resume the session, the agent keeps what it read and what it tried, and the prompt holds only the reason for the new run. If the harness cannot, or gave no id, the engine starts a new agent, and the prompt holds the full [**brief**](../glossary.md#brief) with a continuation section. The agent then has to read the work again.

The engine does not depend on the [**resume**](../glossary.md#resume), because the commits on the branch carry the work in both cases. See [the continuation](../reference/run.md#the-continuation).

## Gateway

A [**gateway**](../glossary.md#gateway) is a service between a harness and the models. It serves the models under its own names, and a company can run one, for example to control the access and the cost of its model use. A model that has one name at the vendor can have another name at the gateway.

A [**workflow**](../glossary.md#workflow) never names a model, so a gateway changes no workflow. The [**tier table**](../glossary.md#tier-table) maps each [**tier**](../glossary.md#tier) to one model for each harness. A [**tier file**](../glossary.md#tier-file) is a copy of the table with the names of the gateway, and `--tiers` gives its path. The names live in one file, and the workflows stay as they were. See [how to point the tiers at a gateway](../how-to/point-the-tiers-at-a-gateway.md).

## Spec

A [**spec**](../glossary.md#spec) is the plan of a piece of work: the problem, the solution, and the decisions that shaped it. The [**tickets**](../glossary.md#ticket) come from it. Each ticket is one unit of work, written as behaviour, with the tickets that block it.

The kit does not write the spec, and it does not choose the tickets. Those are the work of planning, and I do that work with the planning skills of another project. The kit starts where the planning ends: it takes the tickets, and it replaces only the build step. I keep the line there, so the kit does one job: it builds and checks what a person already decided. See [how to use the kit after `to-spec` and `to-tickets`](../how-to/use-the-kit-after-to-spec-and-to-tickets.md), and [prior art](prior-art.md).

## Step

A [**step**](../glossary.md#step) is the work of the engine for one ticket, from the [**worktree**](../glossary.md#worktree) to the [**verdict**](../glossary.md#verdict). The engine makes the worktree and a branch. It spawns the specialist, checks the commits and the [**hotspots**](../glossary.md#hotspot), runs the gates, and, with [**`assure`**](../glossary.md#assure), runs the verifier.

A step is the unit of failure. A step that fails stays as it was, with its branch and its worktree, and the run goes on with the tickets that do not depend on it. A ticket whose [**blocker**](../glossary.md#blocker) failed is skipped, and the [**journal**](../glossary.md#journal) records why. I chose this because one bad ticket in a long run should cost that ticket, not the run. See [what a run does](../reference/run.md#what-a-run-does).

## Three-dot diff

A [**three-dot diff**](../glossary.md#three-dot-diff) is the command `git diff <base>...<branch>`. It shows the changes of the branch since the point where it left the base, which is the merge base. A two-dot diff compares the two tips. If the base moved on after the branch started, the two-dot diff also shows the work that landed on the base as if the branch had undone it.

The verifier needs the three-dot form. It must see the work of one ticket and nothing else, so it can answer one question: does this diff do what the ticket asks? The engine builds the diff, and the verifier gets it in the [**brief**](../glossary.md#brief), with the ticket and without the [**report**](../glossary.md#report). See [why the verifier is blind](why-the-verifier-is-blind.md#the-report-is-a-claim), and [the verifier step](../reference/run.md#the-verifier-step).

## Event stream

An [**event stream**](../glossary.md#event-stream) is the line-delimited JSON that a harness writes to standard output in a headless run. Each line is one event: a step of the agent, some text, a tool call, a result or an error. The last result event says how the run ended.

The engine cannot see inside the harness, so the stream is the only account of the run that the harness gives. The adapter reads it for the [**end state**](../glossary.md#end-state) and the session id, and keeps a copy beside the report. The copy is the record of what the agent did, and a test can replay it. A recorded stream played through a stand-in command is the base of each [**contract test**](../glossary.md#contract-test), so the adapter is checked against the real format and not against my idea of it.

The two formats differ, and the reference pages give each one: see [the event stream of the `claude-code` adapter](../reference/claude-code-adapter.md#the-event-stream) and [the event stream of the `opencode` adapter](../reference/opencode-adapter.md#the-event-stream).

## The neutrality check

The kit is for every repository, so it holds no personal or company value. A repository gives its own values in its own configuration. The [**neutrality check**](../glossary.md#neutrality-check) is the test that holds this rule.

The check reads a [**denylist**](../glossary.md#denylist), a list of terms such as the names of persons and companies. The list is private. If it were in the kit, the kit would hold the terms that it must not hold. So the check takes the path of the list from the variable `AGENT_DEFINITIONS_DENYLIST`, and it skips, with a notice, when the variable is not set. A term of the list in a file of the kit fails the test.

The [**allowlist**](../glossary.md#allowlist) is the one way to keep such a term. An entry names a file and a term, with the reason in a comment line above it. I want each exception to be permanent and visible, so the check reports an entry whose file no longer holds its term. The kit has no exception today. See [the neutrality check in the skill](https://github.com/AlexanderNZ/delegate/blob/main/skills/agent-definitions/SKILL.md#neutrality-check).

## ASD-STE100

[**ASD-STE100**](../glossary.md#asd-ste100) is Simplified Technical English, a standard of controlled English: short sentences, the active voice, and one meaning for each word. It began in aerospace maintenance manuals, where a misread step has a high cost.

I use it for the text that agents write. An agent that reads the text of another agent has the same problem as a technician with a manual. A sentence with two readings gets one of them, and the next agent acts on it. A repository selects it with `outputLanguage = "ste"` in a declaration, or in its [**delegation document**](../glossary.md#delegation-document), and the default is no language. Agent-written text then follows the binding subset in the protocol skill. The `--output-language` option of `delegate bootstrap` sets the same field. See [the output language](https://github.com/AlexanderNZ/delegate/blob/main/skills/agent-delegation/SKILL.md#output-language-asd-ste100-simplified-technical-english).
