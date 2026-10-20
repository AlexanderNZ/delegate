# ADR 0006: A load-time block is one plain command

- Status: accepted
- Date: 2026-10-19
- Applies to: the context skill that `bootstrap` writes, and its load-time blocks on Claude Code

## Context

A context skill reads each reference document when Claude Code loads the skill. Each read is a load-time block: a fenced block that opens with `` ```! ``. Claude Code runs the command of the block and puts the output into the context of the agent.

The first template of the context skill used a compound block of the form `R=$(...); F=...; if ...; fi`. In a subagent this block stopped the agent before it started.

No Claude Code documentation states this rule. The rule is a measured behaviour. The measurements are in the issue tracker of a consumer repository, issues 171, 145 and 196. The coordinator of this kit read these issues on 2026-10-19. This ADR records the measurements, so that a contributor can read them without access to that tracker.

## The measurements

Issue 171, 2026-09-26, Claude Code 2.1.283, on WSL. A probe subagent preloads a probe skill. The table gives the result of the load-time block in each permission mode:

| Permission mode | Compound block (`R=$(...); F=...; if ...; fi`) | One plain command |
|---|---|---|
| manual (default) | The subagent does not start: "Shell command permission check failed ... Contains shell syntax (string) that cannot be statically analyzed" | Runs |
| acceptEdits | The subagent does not start (the same error) | Runs |
| auto | The subagent does not start (the same error) | Runs |
| bypassPermissions | Runs | Runs |
| main thread, manual | Does not run: the model gets the raw command | Runs |

A comment on issue 171, on the same day (Claude Code 2.1.283, manual mode), corrects the table. The checker accepts some compound commands: `R=$(...); cat "$R/..."` started, and the form of the template did not. Thus the only form that is known to be safe is one plain command, for example `sed -n`, `grep -n`, `ls` or `git grep`.

Issue 145, a report of 2026-09-25, gives the refusal of a compound block in auto mode: "The server-side auto mode classifier gave no verdict for Bash". A comment of 2026-10-18 on the same issue (auto mode; the comment gives no Claude Code version): a verifier whose context skill has plain blocks started as a subagent. It quoted the first heading of its reference document from the output of the block.

Issue 196, a comment of 2026-10-08, Claude Code 2.1.287. A headless `claude -p --permission-mode auto` run from a subdirectory of the repository spawned a verifier whose plain blocks used `git grep` with a `:/` pathspec. The verifier started and quoted the correct lines. Limit: a `-p` run does not prove that the agent saw the output of the load-time block. The interactive check is still open.

### Note of 2026-10-20: `EnterWorktree` and an isolated agent

A consumer repository reported on 2026-10-20 that no pair agent could start. The load-time block of the context skill was refused with `Shell command permission check failed for pattern "<the block>": Permission to use Bash has been denied because Claude Code is running in don't ask mode.` A repro on the agent host the same day, Claude Code 2.1.283, narrowed the trigger. The trigger is not the `dontAsk` permission mode that the message names: in `dontAsk` mode with no allow rule, a read-only block loaded. The trigger is a coordinator session that called `EnterWorktree` and then started an agent with worktree isolation, on the Agent call or in the frontmatter. That agent did not start. Each of these loaded: no `EnterWorktree`; `EnterWorktree` and then an agent with no isolation; `EnterWorktree`, then `ExitWorktree`, then an isolated agent. The inline and the fenced forms of the block behaved the same. The upstream bug is reported separately.

This note does not change the decision below. It adds two consequences: the `agent-delegation` skill tells a coordinator to call `ExitWorktree` before it spawns an isolated pair specialist, and `bootstrap --context-load read` writes a context skill with no load-time block, for a host where a load-time block cannot run (issue #148).

## Decision

Each load-time block of a context skill is one plain command: one line, with no `$(...)`, no `;`, no `if`, no pipe, no redirect and no assignment prefix. `bootstrap` makes each decision that a compound block made at load time when it writes the skill. It counts the lines of each reference and writes the full-text block or the heading-index block. Each block is `git grep` with a `:/` pathspec, so that it reads the same file from the repository root and from each subdirectory.

## Rejected alternatives

- **Keep the compound block and tell the host to use bypassPermissions.** The coordinator does not control the mode of each host, and that mode turns off the permission checks for all tools.
- **Use a compound form that the checker accepted, such as `R=$(...); cat "$R/..."`.** The checker accepts some compound commands and refuses others, and no documentation states which. A form that starts on one version can stop on the next.

## Consequences

- A subagent that preloads a context skill with plain blocks starts in the manual, acceptEdits and auto modes, as measured on Claude Code 2.1.283.
- The skill cannot choose at load time between the full text and the heading index of a document. A document that grows past 200 lines after `bootstrap` ran is printed up to line 200 until the next `bootstrap` run.
- The rule depends on a version of Claude Code. Measure it again when a new version changes the permission check, and add the date and the version here.
- The test `test_every_live_read_block_is_one_plain_command` in [`tests/test_bootstrap.py`](../../tests/test_bootstrap.py) holds each block to one plain command. The test `test_the_context_skill_states_the_source_of_the_load_time_block_rule` holds that the rendered skill names this ADR, the version and the modes.
