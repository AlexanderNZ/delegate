# Components — terminal

All tables are column-aligned with spaces computed from the widest value in the column. Numbers right-align; text left-aligns. Track widths below are the design widths at 120 columns (`ch` = one cell). Columns are separated by 2 cells.

## Run line
`run <short id>  <state>  <bar> <pct>%  <ended>/<total> ended  <used> used  ETA <eta>  <new> new · <cache> cache`
- Short id in bold. State: running plain, paused/pausing `warn`, built `ok` bold, failed `bad` bold. Economy chain phase follows the state: `running · verifier`.
- Bar: 16 cells, `█` fill in `ok`, `░` rest in `dim`. Dropped under 100 columns.
- ETA omitted when ended or paused; `-` before the first ticket ends.
- One blank line below.

## Ticket table (watch)
Tracks: `#` 3 right · `plan` 12 · `state` 10 · `phase` 46 · `used` 7 right · `new` 5 right · `cache` 6 right · live tail (rest).
Header row in `dim`: `#  plan  state  phase  used  new  cache`.

- `#`: plan position, `dim`.
- `plan`: tree gutter + node glyph + id. The tree follows `blocked-by`: a ticket with a blocker is drawn `└─` under it, a grandchild `  └─` (two cells per depth). A ticket with no blocker sits at the root column. Tree glyphs are `plan` colour. Node glyph by state: `●` built (`ok`), `▶` running (`now`, id bold), `·` pending (`dim`), `✗` failed (`bad`, id bold), `–` skipped (`dim`).
- `state`: the word only: `✓ built` (dim, check in `ok`), `running` (`now` bold), `pending`, `failed` (`bad` bold), `skipped`.
- `phase`: running rows only. Track `specialist ✓  gates ✓  verifier ●  fix-up ○`: done `✓` in `ok`, active `●` in `now`, pending `○` in `dim`. A fix-up round shows `fix-up ● 1/2` in `warn`. Failed/skipped/paused rows use this cell for the reason text (`the gates are not green: dotnet test` in `bad`; `blocked by 70, which failed` in `dim`; `paused before this step` in `warn`).
- `used`, `new`, `cache`: `dim`. Durations as today (`12m38s`), tokens as today (`149k`, `10.7M`).
- live tail: `active 2s` (`now`); `gate pytest · log 30s ago` while a gate runs; `⚠ idle 6m40s` (`warn`) past the stall threshold; `cont 2/2 · active 5s` on the last continuation.
- Folding: when the table would not fit the frame, ended rows fold to one line `✓ built 11   59 60 61 62 64 65 66 72 67 68 69` (ids in plan order). Running and pending rows never fold. A child whose parent is folded still draws its `└─`; the fold line ends with the parent, so it reads correctly.

## Divider
One line of `─` across the frame width, `dim`, between the table and the feed.

## Feed (watch)
Only the events of the ticket that runs now. When its step ends the row carries the result and the feed starts again with the next ticket's `started` line. In economy mode, the chain verification is one group headed `stack <name>`.

Row tracks: `time` 5 · `#id` 3 · text (rest). Joins and words unchanged from today.
- time `dim`; `#id` `dim`, except on the `started` line where it is `now` bold.
- `gates green` `ok`, parenthetical `dim`; `gates red` `bad` with the red gate's name in `bad` inside the parenthetical; `✓ ACCEPT` check `ok`, word bold; `REJECT · n findings` `warn` bold; `fix-up round n started` `warn`; `continuation n of m` `warn`; token parts `dim`.
- A waiting line (an agent that runs now) reads `verifier running · 4m · active 3s` in `now`.
- Problem: `✗` in the id column, whole line `bad`. Stall: `✗ stall · no change for 30m`. Time limit plain.
- End: `run built · 3h46m · 3.2M new · 44.6M cache` (`ok` bold on the first part) or `run failed` (`bad`); then `position <seq>` plain.
- Paused: `paused · before ticket 71 · go on with delegate run --resume <id>` (`warn`), then `position <seq>`.

## Ticket table (status)
Same tracks as watch with three more between `phase` and `used`: `verdict` 7 · `branch` 15, and `last event` as the last column (ISO time, `dim`); the live tail follows the time. Under the three documented header lines (run, progress, journal). The per-ticket `progress:` sub-line is folded into the row. `--json` unchanged.

## Run picker (watch, several active runs)
Tracks: `#` 3 right · `run` 22 (full id, short id bold) · `run branch` 12 · `mode` 7 · `since` 9 · `progress` 22 · `now` (rest). Newest first. Prompt `watch which? [1]`; Enter takes 1. Not a terminal: one stderr line, exit 2 (see copy deck).

## 80 columns
Drop in this order: bar; `new` and `cache`; phase labels (keep glyphs `✓ ✓ ● ○`); `used`. Ended rows fold. Feed lines are cut at the width as today.

## Plugin
Text only, host colours. Status line unchanged. Pane: plan index and tree gutter added; ended rows dim (already).
