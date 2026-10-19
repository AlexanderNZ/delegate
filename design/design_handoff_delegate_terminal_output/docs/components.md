# Components — terminal

All tables are column-aligned with spaces computed from the widest value in the column. Numbers right-align; text left-aligns. Track widths below are the design widths at 120 columns (`ch` = one cell). Columns are separated by 2 cells.

## Run line
`run <short id>  <state>  <bar> <pct>%  <ended>/<total> ended  <used> used  ETA <eta>  <new> new · <cache> cache`
- Short id in bold. State: running plain, paused/pausing `warn`, built `ok` bold, failed `bad` bold. Economy chain phase follows the state: `running · verifier`.
- Bar: 16 cells, `█` fill in `ok`, `░` rest in `dim`. Dropped under 100 columns.
- ETA omitted when ended or paused; `-` before the first ticket ends.
- One blank line below.

## Ticket table (watch)
Tracks: `#` 3 right · rail 2 · `plan` 6 · `state` 10 · `phase` 13 · `after` 8 · `used` 7 right · `new` 5 right · `cache` 6 right · live tail (rest). 62 cells before the tail, whatever the plan.
Header row in `dim`: `#  plan  state  phase  after  used  new  cache` (the rail has no header).

- `#`: plan position, `dim`.
- rail: `│` in `plan` colour when the row's blocker is the row directly above; otherwise blank. The rail also runs through the phase sub-rows of a running ticket when the next row connects. Depth never costs width: rows are flat and in plan order.
- `plan`: node glyph + id. Node glyph by state: `●` built (`ok`), `▶` running (`now`, id bold), `·` pending (`dim`), `✗` failed (`bad`, id bold), `–` skipped (`dim`).
- `state`: the word only: `✓ built` (dim, check in `ok`), `running` (`now` bold), `pending`, `failed` (`bad` bold), `skipped`.
- `phase`: running rows only, stacked vertically. The row carries the first phase (`specialist ✓`); each further phase is a sub-row below it with only the rail, the phase cell and, when there is one, a detail in the live tail (`dotnet test · log 4s ago`). Done `✓` in `ok`, active `●` in `now`, pending `○` in `dim`; a fix-up round `fix-up ● 1/2` in `warn`; a gate count `gates ● 2/3`. Nothing but phases goes in this column.
- `after`: the blocker's id in `plan` colour, only when it is not the row above (the rail covers that case). A join lists both: `126 132`. Nothing else goes in this column.
- Reason text (`the gates are not green: dotnet test` in `bad`; `blocked by 70, which failed` in `dim`; `pauses before this step` in `warn`) goes in the live tail.
- `used`, `new`, `cache`: `dim`. Durations as today (`12m38s`), tokens as today (`149k`, `10.7M`).
- live tail: the liveness of the agent (#147): a spinner `⠋` (`now`) under 30 s since its last activity; `quiet 45s` (`dim`, no animation) from 30 s to the stall threshold; `⚠ idle 6m40s` (`warn`) past the stall threshold. The spinner steps one frame about each 125 ms, from the snapshot of the frame, between two reads of the journal; the poll rate does not change, and the frame replaces itself in place. It turns only while the agent is active, so a still spinner never hides a hung agent. With `NO_COLOR` or no terminal, no animation: `active 2s ago`. `gate pytest · log 30s ago` while a gate runs; `cont 2/2 · ⠋` on the last continuation.
- Fit to height: table rows = terminal rows − run line − blank − header − divider − feed (the feed keeps at least 4). When the plan has more rows than that, rows are shown in plan order from the top and the tail folds into one last row, always directly above the divider: `· 11 pending` in plan+state, `134–150` in `after`, `rows 30–40 folded` in the tail. If that is still too many, built rows fold in the middle, keeping the first three and the three before the running ticket: `✓ 17 built` · `104–120` · totals in used/new/cache · `rows 4–20 folded`; the rail runs through it. The running ticket and the pending rows nearest to it are never folded. No TTY size: no folding.

## Divider
One line of `─` across the frame width, `dim`, between the table and the feed.

## Feed (watch)
Only the events of the ticket that runs now. When its step ends the row carries the result and the feed starts again with the next ticket's `started` line. In economy mode, the chain verification is one group headed `stack <name>`.

Row tracks: `time` 5 · `#id` 3 · text (rest). Joins and words unchanged from today.
- time `dim`; `#id` `dim`, except on the `started` line where it is `now` bold.
- `gates green` `ok`, parenthetical `dim`; `gates red` `bad` with the red gate's name in `bad` inside the parenthetical; `✓ ACCEPT` check `ok`, word bold; `REJECT · n findings` `warn` bold; `fix-up round n started` `warn`; `continuation n of m` `warn`; token parts `dim`.
- A waiting line (an agent that runs now) reads `verifier running · 4m · ⠋` in `now`, and ends with the liveness of the live tail: `quiet 45s` in `dim`, or `⚠ idle 6m40s` with the whole line in `warn`.
- Problem: `✗` in the id column, whole line `bad`. Stall: `✗ stall · no change for 30m`. Time limit plain.
- End: `run built · 3h46m · 3.2M new · 44.6M cache` (`ok` bold on the first part) or `run failed` (`bad`); then `position <seq>` plain.
- Paused: `paused · before ticket 71 · go on with delegate run --resume <id>` (`warn`), then `position <seq>`.

## Ticket table (status)
Same tracks as watch (`state` 8) with three more between `after` and `used`: `verdict` 7 · `branch` 15, and `last event` as the last column (ISO time, `dim`). No live tail: status is a snapshot. Under the three documented header lines (run, progress, journal). The per-ticket `progress:` sub-line is folded into the row. `--json` unchanged.

## Run picker (watch, several active runs)
Tracks: `#` 3 right · `run` 22 (full id, short id bold) · `run branch` 12 · `mode` 7 · `since` 9 · `progress` 22 · `now` (rest). Newest first. Prompt `watch which? [1]`; Enter takes 1. Not a terminal: one stderr line, exit 2 (see copy deck).

## 80 columns
Drop in this order: bar; `new` and `cache`; `used`. Phases already stack, so labels stay. Fit to height as above. Feed lines are cut at the width as today.

## Plugin
Text only, host colours. Status line unchanged. Pane: plan index and `after <id>` added; ended rows dim (already).
