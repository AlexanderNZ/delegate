# delegate design context, glossary and rules

Product: delegate (watch, terminal output and docs site). Repo: `AlexanderNZ/delegate`, branch `main`. The repo is the source of truth: its code, `design/` folder and docs win over these files. Last read: 2026-11-14, commit `ce17ee5`.

Two surfaces share one vocabulary:
- **Terminal output** (frames W, E): what `delegate` prints. Code: `src/delegate/run/status.py`, `run/live.py`, `run/watch.py`, `run/output.py`, `cli/watch.py`, `cli/status.py`, `cli/run_command.py`, `plugins/claude-code/hooks/register.tsx`.
- **Docs site** (frames D): the Zensical site at alexandernz.github.io/delegate. Code: `zensical.toml`, `docs/stylesheets/delegate.css`, `docs/javascripts/delegate.js`, `docs/javascripts/glossary.js`, `overrides/`.

## Terminal: what it is
The text that `delegate` prints on a terminal:
1. `delegate watch` live view: run line, ticket table, divider, feed; redrawn in place each poll (W1).
2. `delegate status` one-shot report (W2).
3. `delegate watch --raw` and non-TTY lines: a contract, unchanged (W3). `delegate watch --json`: one JSON object per event (W7).
4. `delegate run` stdout and stderr (W5).
5. The run picker when several runs are active (W4), and the one-line answers when no run is active (E2, E11).
6. The Claude Code plugin: status line entry, warning band, toasts, `/delegate` pane (W6). It reads `status --json`; text only.
7. Proposed, not in the repo: `delegate runs`, every run of the run index on every host (W8), and its `--timeline` view (W9).

Reader: one person glancing at a terminal while agents work. Debugging is `--raw`, `--json` and the journal.

Shape (shipped, #143 #144): the ticket table is the record. Rows are flat and in plan order; a rail `│` joins a row to the one above when that is its only blocker, and `after` names the blockers otherwise. The running ticket stacks its phases under its row. Width is fixed by the tracks; height is fitted by folding. Below a divider, the feed holds only the events of the ticket that runs now.

Constraints: 16 ANSI colours, bold and faint only; no background colour. No alternate screen, no mouse. Every line cut to the width; the frame fits the height. Only facts that the snapshot and journal hold; no cost, no path, no `None`. `--raw` and `status --json` are contracts.

## Docs site: what it is
Theme "2c, Guide + quiet reference" (shipped): serif body, light sans headings, numbered sections and a numbered table of contents in the sidebar; reference pages add a man header line, NAME/SYNOPSIS, labelled striped tables. Light and dark palettes. Stock Zensical underneath, changed only through `extra_css`, `extra_javascript` and `overrides/`. Docs content is never edited to fit the theme; `generated:begin`/`generated:end` sections are written by `delegate docs`.

## Nouns
| Term (code) | Term (UI) | Definition | Notes |
|---|---|---|---|
| run | run | One execution of `delegate run` over a workflow, with a run id and a journal. | Run id `20261016T030000Z-abcd`; short id = part after the last `-`. |
| ticket | ticket, `#<id>` in the feed | One unit of work with its blockers. | Shown in plan order. |
| step | step | The work of the engine for one ticket: worktree, specialist, checks, gates, verifier. | |
| phase | specialist / gates / verifier / fix-up | The phase of a running ticket's step. | `economy` tickets have specialist and gates only. |
| state (ticket) | pending / running / built / failed / skipped | From the journal events. | |
| state (run) | running / pausing / paused / halted / stopped / abandoned / built / failed | `status.py` `_state`. | halted: harness failure (#177). stopped: no run-end, no process (crash, kill, setup check; #193 #281 #296). abandoned: `delegate clean --abandon` (#201). |
| note | `note: …` | A line under the run line of `status` for pausing requests, stopped, halted, crashed and abandoned runs. | |
| chain row | `▶ python` | One row per stack for the verification of an `economy` chain, under the last ticket of the stack (#196). | states running / accepted / rejected. |
| specialist | specialist | The agent that builds one ticket. | Not: implementer. |
| verifier | verifier | Blind, read-only agent that gives a verdict. | Not: twin, reviewer. |
| fix-up | fix-up | Round after a REJECT: new commit, fresh verifier on the delta. | |
| gate | gate | A command that proves a change is good. | Short name from `gate_name()`, or the workflow `name` (#223). |
| verdict | ACCEPT / REJECT | Result of a verifier. | |
| finding | finding | One defect in a verdict. | |
| chain | chain | Ticket sequence in `economy` mode. | The chain verification is one more progress unit. |
| stack | stack | One technology with its own agent pair and gates. | |
| mode | assure / economy | When the verifier runs. | |
| adapter | adapter | Drives one harness (claude-code, opencode). | Named in the `status` first line only. |
| journal | journal | Append-only JSONL of a run. | Path in `status` and `run` only. |
| continuation | continuation | New specialist run in the same worktree. | |
| tokens new / cache | `N new` / `N cache` | input + cache writes + output / cache reads. | Short form 950, 26k, 1.2M. |
| ETA | ETA | Time to the end of the run. | `-` before the first ticket ends; only while running or pausing. |
| liveness | spinner / `quiet 45s` / `⚠ idle 6m40s` / `active 2s ago` | Time since the last activity of an agent (#147). | Spinner under 30 s; `active … ago` in snapshots. |
| problem | `✗` line | A problem event. | The live view goes on (#222); `--raw`, `--json` and non-TTY exit 3 unless `--follow`. |
| stall | `✗ stall · no change for Nm` | Nothing changed for `--stall-minutes`. | watch exits 5. |
| position | `position <seq>` | seq of the last reported event. | Last line on exit. |
| run index (proposed) | — | Where each run registers its host, repo, start time and last-known state: a shared directory or the local state root. | No paths in the view. |
| slip (proposed) | `+55m` | How much later the ETA of a run is than it read 2h ago. | Shown from 30m; threshold provisional. |
| alert (proposed) | `⚠ idle 6m40s` | The one thing wrong with a run, in `runs`. | Blank when nothing is wrong. |
| glossary term | linked term | A word of `docs/glossary.md`, linked to its entry on every page but the glossary. | `delegate docs` writes `includes/abbreviations.md`. |

## Roles
| Role | Can | Cannot |
|---|---|---|
| Coordinator (person) | Read the live view, status and docs; act on exit codes; pause and resume. | Change the run from the view. |
| Coordinator agent | Read raw lines, JSON lines and status as-is. | Read the journal with its own script. |

## Rules
R1. The view shows only data: no path, no `None`, no tool call; an absolute path is cut to its file name (`status` keeps the traceback path).
R2. A feed line starts with local time HH:MM, then `#<id>` (or `stack <name>`), then what happened; parts joined by ` · `.
R3. Tickets appear in plan order; a plan change can add a ticket.
R4. Token counts never show a cost.
R5. Failures belong to the engine and are stated plainly; copy never blames the user.
R6. A frame that goes on fits the terminal height; the last frame keeps the whole feed.
R7. Each line is cut to the terminal width.
R8. Events with no line of their own: agent-start, gate-start, idle-timeout, agent-timeout, run-branch-advance, lock-broken, pause-requested, cleanup.
R9. Durations: feed `45s / 36m / 10h42m`; table and run line `45s / 12m05s / 2h03m / 3d04h`.
R10. Docs: the theme never changes docs content or nav titles; numbering, labels and the man header come from CSS, templates and front matter.

## UI word choices
| Code word | UI word | Why | Ruling date |
|---|---|---|---|
| adapter-result | specialist finished | role + end state reads as a sentence | existing |
| PROBLEM mark | ✗ | glyph | existing |
| last activity | spinner / `quiet` / `active … ago` | `active 2s` read as a duration (#147) | repo #147 |

## Open
- "Cursor up to an ended ticket to see what happened in it": parked. Likely `watch --ticket <id>` before any key handling.

## Non-goals
Web dashboard, mouse interaction, new data the engine does not record.
