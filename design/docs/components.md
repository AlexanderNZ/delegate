# Component registry

Numbers are permanent. C01–C11 come from v0.1.0; C12 on are new in v0.2.0. Every entry is what the repo prints today (commit `ce17ee5`).

## Terminal
| No. | Name | Code path | Purpose | Props | Frames |
|---|---|---|---|---|---|
| C01 | RunLine | status.py `_run_line` | First line of the live view. | state: running, pausing, paused, halted, stopped, abandoned, built, failed; phase (economy chain); percent; ended/total; used; eta (running/pausing only); tokens or absent; bar at ≥ 100 cols | W1, E3–E13 |
| C02 | TicketLine (row) | status.py `_row`, `_rows` | One row per ticket in plan order. | position; rail; node+id; state; phase; after; used; new; cache; tail | W1, W2, E3–E13 |
| C03 | FeedLine | live.py `_Line` | One event line under the divider. | time HH:MM; subject `#id` or `stack <name>` or none; text; role | W1, E4–E13 |
| C04 | AgentResult | live.py `_line` (AGENT_OF_RESULT) | Agent finished, joined with its report. | role; endState: finished, failed, capped, timed out; duration; tokens; report | W1, E4, E8 |
| C05 | GatesLine | live.py (GATES) | One round of gates. | colour: green, red; items | W1, E4, E5, E8 |
| C06 | VerdictLine | live.py (VERDICT) | Verdict joined with the step end. | ACCEPT, REJECT · n findings; stepState | E4, E8, E13 |
| C07 | ProblemLine | live.py problem branch | `✗ #id <problem>` in `warn`; the run goes on (#222). | subject; problem text | E5 |
| C08 | StallLine / TimeLimitLine | live.py `stall`, `time_limit` | Exit reasons in the feed. | minutes | E6 |
| C09 | EventLine | live.py `_text` | Any other event. | run-start, resume, step-start, retry, continuation, continuation-limit, hotspot-finding, rebase, verify-start, invariant-violation (2 kinds), fixup-start, fixup-refused, skip, not skipped, stack-change | W1, E4 |
| C10 | PositionLine | cli/watch.py | `position <seq>` on exit. | seq | E5–E8, E10 |
| C11 | EndedRunLine | run/watch.py `EndedRun` | One line when the newest run ended. | runId; result; local time; reason (abandoned); first problem | E2, E12 |
| C12 | TableHeader | status.py `_table` | Dim column names; rail and tail have none. | columns after fit | W1, W2 |
| C13 | PhaseSubRow | status.py `_rows` | One row per phase after the first, under the running ticket. | rail; phase cell; tail (gate detail) | W1, W2, E3–E6, E9 |
| C14 | FoldRow | status.py `_fold_line` | Rows that do not fit the height. | tail or middle; counts by state; id range; totals (middle); `rows a–b folded` | E3 |
| C15 | Divider | live.py | `─` across the width, dim. | width | W1 |
| C16 | LiveTail | status.py `_activity_tail`, `liveness`, `_gate_tail`, `_reason` | Last cell of a row. | continuation; liveness; gate; reason (+traceback); `⚠` mark; pause cell | W1, E3–E13 |
| C17 | ChainRow | status.py `_chain_row` | Verification of one stack of an economy chain (#196). | stack; state running/accepted/rejected; phase; used; tokens; tail | E13 |
| C18 | StatusHeader | status.py `render` | `run …:` / `note:` / `progress:` / `journal` / stack-change / kept-branch lines. | state; note; progress; journal; changes; kept branches | W2, E10–E12, E14 |
| C19 | RunPicker | status.py `picker_lines`, cli/watch.py | Several active runs: title, table, prompt; non-TTY one stderr line. | runs | W4 |
| C20 | RunLines | cli/run_command.py `_start_lines`, `_end_lines` | `delegate run` start hint, summary, retry hints, stderr lines. | runId; result; counts; tokens; retries | W5 |
| C21 | PluginEntry / Band / Toast / Pane | plugins/claude-code/hooks/register.tsx | Claude Code plugin surfaces. | snapshot from `status --json` | W6 |
| C22 | JsonLine | run/watch.py `JsonSink` | `watch --json`: one object per event, then `watch-end`. | event; reason; exit_code | W7 |
| C23 | StoppedRunLine | run/watch.py `StoppedRun` | One line when the newest run stopped with no run-end. | runId; time; resume | E11 |
| C24 | FleetLine (proposed) | none yet | First two lines of `runs`: reachable runs, bar, percent, ended/total, latest ETA and runs with none, token totals; then wall-clock span, runs in trouble, fleet trend. | reachable/total; percent; ended/total; eta; noEta; tokens; span; trouble; trend | W8, W9, E15–E22 |
| C25 | RunsTable (proposed) | none yet | One row per run, grouped by host; a totals row per host. | host; repo; run; state; ended; ETA; slip; new; cache; trend; alert; do | W8, E15–E18 |
| C26 | TimelineLanes (proposed) | none yet | One lane per run over the trend window, to the latest ETA; an `all` lane of tickets ended per hour. | label; lane; ended; ETA; state; alert | W9, E19–E22 |
| C27 | DoLegend (proposed) | none yet | The command behind each `do` word, once, under the table. | watch; resume; retry; merge; check host | W8, E16–E18 |

Retired: none. C01–C11 keep their numbers; their v0.1.0 props are superseded by the rows above.

### Specs
**Run line (C01).** `run <short id bold>  <state[ · phase]>  <bar> N%  X/Y ended  <used> used  ETA e  <new> new · <cache> cache`. State roles: paused/pausing `warn`, halted `bad bold`, built `ok bold`, failed `bad bold`, others plain. Bar 16 cells (`█` ok, `░` dim) only at ≥ 100 columns. ETA only while running or pausing; `-` before the first ticket ends. Used and tokens dim. Tokens absent when the run has no stream reader.

**Ticket table, watch (C02, C12, C13, C16).** Columns `# rail plan state phase after used new cache tail`, tracks in tokens.md. Rows:
- built: `● <id dim>` (ok node), `✓ built` (✓ ok, word dim)
- running: `▶ <id>` and `running` in `now bold`
- failed: `✗ <id>` and `failed` in `bad bold`; tail: reason in `bad`, ` (traceback <file>)` dim
- skipped: `– <id>`, `skipped`, reason, all dim
- pending: `· <id>`, `pending`, dim; no used, no tokens
- rail `│` (plan) when the only blocker is the row above; `after` lists the blockers otherwise (a join lists each).
- phases, running rows only: the row carries the first; each further phase is a sub-row. Done `specialist ✓` (name dim, ✓ ok); active `gates ● 2/3` (now); to come `verifier ○` (dim); active fix-up `fix-up ● 1/2` (warn); done fix-up `fix-up ✓ 1/2`. Economy: specialist and gates only.
- tail of the running row: `cont 2/2` (warn) · liveness. Liveness: spinner frame (now) under 30 s; `quiet 45s` (dim) to the idle limit; `⚠ idle 6m40s` (warn) past it (default 5 min, or `--stall-minutes`). With `NO_COLOR` or no terminal: `active 2s ago`.
- gate detail on the sub-row of the active phase: `gate pytest · log 30s ago` (or `· ⚠ idle …`).
- a ticket whose newest event is a problem: tail starts with `⚠` (warn) (#222).
- pause cells (warn): paused `paused before this step` / `paused before the gate` / `paused before the verifier`; pausing, on the next pending row: `pauses before this step`.

**Fit to height (C14).** Room = rows − 3 (run line, blank, header) − divider − feed (≥ 4). The anchor (running ticket, else the ticket paused before, else the first pending, else the last) never folds. If the top-to-anchor rows plus the 3 nearest after it plus a tail fold fit: show from the top and fold the tail. Else also fold the middle, keeping the first 3 and the 3 before the anchor. Fold line: `✓ 17 built[, 1 failed …]` in plan+state (glyph of the first state), `104–120` in after, middle fold totals in used/new/cache and `rows 4–20 folded` in the tail; tail fold `· 11 pending`, `134–150`, `rows 30–40 folded` from the first numeric column. One row: `row 30 folded`. All dim; rail through a middle fold.

**Fit to width.** Drop `new` and `cache`, then `used`. The live tail with a reason does not count.

**Feed (C03–C10).** Only the group of the ticket that runs now (a step-start or retry starts a group; economy chain verification is one group named `stack <name>`). Tracks: time 5 · 2 · subject (≥ 3) · 2 · text. Time dim; subject dim, `now bold` on a `started` line. Waiting line while an agent runs: `<role> running · 4m · 20k new · 315k cache · <liveness>` in `now`. Problem: `✗ #70 <problem>` in `warn`; the live view goes on. Stall `✗ stall · no change for 30m` in `bad`. Run end `run built · 3h46m · tokens 3.2M new, 44.6M cache` (`ok bold` / `bad`), optional `· reason: …`. Paused `paused · before <where> · go on with delegate run --resume <id>`. Halted `halted · <where> · <message> · go on with delegate run --resume <id>`.

**Status (C18, W2).** Header: `run <id>: <state> (mode <m>, adapter <a>)`, `note: <note>` when there is one, `progress: [<phase>, ]N%, X of Y ended, <used> used[, ETA e][, tokens N new, M cache]`, `journal <path>`, then `stack <s> changed: <fields>` and `kept: branch …` lines. Blank line. Table: watch columns plus `verdict` (bold) and `branch` (dim unless running) after `after`, and `last event` (ISO, dim) before the tail; `state` 8. Phase sub-rows as in watch. Tail holds only reasons. Empty cells stay empty. Not cut, no drop.

**Chain row (C17, economy, #196).** Under the rows of the last ticket of the stack. plan `▶ python` (now bold) / `● python` (ok node, dim) / `✗ python` (bad bold); state `running` / `✓ accepted` / `rejected`; phase `gates ●`, `verifier ●` or `fix-up ● 1/2` (warn); used since the chain started; tokens of its agents; tail liveness · gate, or `verdict ACCEPT` (ok) / `verdict REJECT` (bad) when ended. `status` puts the verdict in `verdict`.

**Run picker (C19, W4).** `<n> runs are active in this repository`, then columns `# run "run branch" mode since progress now`: full id with the short id bold; since `HH:MM` today, else `Ddd HH:MM` (dim); progress `X/Y ended · N%`; now `▶ <id> <phase>[, chain <phase>]` (now) or `no ticket running` (dim), preceded by the state in `warn` when not running, then ` · active 2s ago`. Prompt `watch which? [1] `. Not a terminal: one stderr line, exit 2.

**Run (C20, W5).** Start: `run <id>`, `journal <path>`, `follow it with delegate watch · pause it with delegate pause <short id>`. Per ticket on stderr: `delegate run: ticket <t>: <reason>` / `…: skipped: <reason>`. End: `run <result> · <used> · n built · n failed · n skipped · <new> new · <cache> cache`, then one `retry with delegate run --resume <short id> --retry <t>[ --coordinator-commit <sha>]` per retry. Halt and crash: `delegate run: halted at <where>: <message>`, `delegate run: crashed at <where>: <error>`.

**Plugin (C21, W6).** Status line `delegate <short> · N% · ETA HH:MM · ▶ <id> <phase> · active 2s ago · <new> new · <cache> cache`; past `stall_minutes`: `⚠ idle 6m40s` and the band `⚠ delegate <short>: no agent activity for 6m40s (ticket 70 specialist)`. Toasts `delegate <short>: ticket 69 built`, `delegate <short>: the run ended, built`. Pane rows end with `after <ids>`.

**Runs (C24–C27, W8, W9; proposed, no code yet).** One kind of value per column; a blank cell means none.
- Fleet line (C24): `runs  <reachable> of <total> reachable  <bar> N%  X/Y ended  ETA <latest> · <n> with no ETA  <new> new · <cache> cache`, then `<earliest start>–<now>  ⚠ <n> in trouble  <fleet trend>`. Progress is ended over total of reachable runs that have not ended, never a mean of percents. Tokens sum `new` and `cache` apart. Time is wall clock. Unreachable and ended runs are out of every sum. In trouble: idle past the limit, stopped, halted or failed-to-start runs.
- Table (C25): `host repo run state ended ETA slip new cache trend alert do`. Hosts are groups, this machine first, a blank line between; the first row of a group has the host and the totals of its active runs (ended summed, the latest ETA, tokens summed); run rows have the rail `│` under the host. `state` is the run state word (`paused` warn, `stopped` and `halted` bad, `unreachable` warn, `built` ok bold, `failed` bad bold). `ended` is `X/Y`, dim when last-known or ended. `ETA` is a clock time, `-` when past its estimate, blank when the run has none. `slip` is how much later the ETA is than it read 2h ago, from 30m (threshold provisional), warn. `trend` is a sparkline of tickets ended per hour over the 6h window, dim, one scale for all runs. `alert` is blank unless something is wrong: `fix-up 1/2`, `⚠ idle 6m40s`, `heard 47m ago`. `do` is `watch`, `resume`, `retry`, `merge` or `check host`, blank when nothing is needed. An ended run in the window sits under its host with `built` or `failed` and is not counted. An unreachable host says `unreachable` on its own row; its last-known run is dim under it. No phase, no spinner, no percent per run, no reason, no path.
- Fit to width: the bar and the legend (C27) drop under 100 columns; then `new` and `cache`, `trend`, `slip`, `repo`. Fit to height: ended runs fold into one row per host (`✓ 1 built`), then a host with no `alert` and no `do` folds to its totals row.
- Timeline (C26): label `host repo run`, a lane, then `ended ETA state alert` as in the table. One cell is 10m at 120 columns and 12m at 80. `─` the life of the run, `●` a ticket that ended built (ok), `✓`/`✗` the end of an ended or stopped run, `▶` at now for a running run (now, or warn with an alert), `░` the projection to the ETA (dim), `┊` now (now). The `all` lane is the sparkline of tickets ended per hour, then `░` to the latest ETA. At 80 the label keeps the run id, the host is a line above its lanes, and `ETA` drops.
- `delegate runs` prints the frame once; `--watch` redraws it in place. Neither has a spinner.

## Docs site
| No. | Name | Code path | Purpose | Frames |
|---|---|---|---|---|
| C30 | Header | overrides/partials/header.html | "delegate" + "Reference Guide", version pill `main`, repo link, palette toggle, "Contents" under the drawer breakpoint. 56px, 1px rule. | D1–D6 |
| C31 | Sidebar | overrides/partials/nav.html | "Quick search" field (opens the search dialog), "Table of contents", numbered nav from `zensical.toml`, h2s of the current page. 290px, `side` bg. | D1–D5 |
| C32 | Breadcrumb | overrides/partials/dlg-breadcrumb.html | `delegate › <sections> › <page>`. | D1–D6 |
| C33 | Headings | delegate.css Type | h1 38px; h2 27px brick, numbered `1.`; h3 21px `1.1.`; ¶ on hover; `heading_numbers: false` turns numbers off. | D1–D4 |
| C34 | CodeBlock | delegate.css Code, delegate.js | Language label top-right; copy button. | D1–D3 |
| C35 | Admonition | delegate.css | NOTE / IMPORTANT label cell, no box. | D3 |
| C36 | GuideTable | delegate.css | Bordered table, `side` th. | D1, D4 |
| C37 | ManHeader | overrides/main.html | `DELEGATE-RUN(1) · delegate Manual · DELEGATE-RUN(1)` from front matter `man`. | D3 |
| C38 | NameSynopsis | overrides/main.html | NAME / SYNOPSIS from `man_name`, `synopsis`. | D3 |
| C39 | TableLabel | delegate.js | Nearest h2/h3 text over each reference table. | D3 |
| C40 | RefTable | delegate.css | Striped, no vertical borders. | D3 |
| C41 | Pager | overrides/partials/dlg-pager.html | `← Previous` / `Next →` with titles; `Version main`. | D1–D4 |
| C42 | SearchDialog | delegate.js SEARCH_CSS | Zensical's search modal, restyled: count, results with title, path, snippet, `mark`. | D5 |
| C43 | GlossaryTerm | delegate.js, glossary.js, delegate.css | A glossary word links its entry: ink colour, dotted `muted` underline, `link` on hover; tooltip with the definition. Not on the glossary page, not in links or headings. | D7 |
| C44 | DiagramFigure | delegate.js, delegate.css Diagrams | A Mermaid diagram in a `dlg-figure` box that scrolls sideways; nodes `side`, edges `muted`, labels `ink`, Open Sans. | D8 |
| C45 | Drawer | delegate.css Narrow screens | Under the breakpoint the sidebar is the drawer; header 52px `side` bg; "Contents" button. | D6 |
