# delegate design handoff v0.3.0

Product: delegate (watch, terminal output and docs site).
Repo: AlexanderNZ/delegate, branch main, commit ce17ee5. Stack: Python terminal text (src/delegate/run, src/delegate/cli), a TypeScript Claude Code plugin (plugins/claude-code), and a Zensical docs site (zensical.toml, docs/stylesheets, docs/javascripts, overrides).

## What changed
- Minor version: new frames. One new surface, `delegate runs`: every run of the run index, on every host, in one terminal view. It is **proposed**: the run index and the command do not exist in the repo, and the command names (`delegate runs`, `--watch`, `--timeline`) are provisional.
- W8 is the default view (a table, grouped by host, with a totals row per host). W9 is the `--timeline` view (one lane per run over the last 6h, to the latest ETA). E15–E22 are their 80 × 24, NO_COLOR, empty and light-theme frames.
- Every other frame (W1–W7, E1–E14, D1–D8) still shows what the repo prints and renders at commit ce17ee5. Their content has not changed since v0.2.0. Two rendering fixes in the terminal mock only: bold text no longer has tightened letter-spacing, and glyphs that fall back to another font are pinned to one cell, so columns line up in any browser font.
- Docs: `docs/CONTEXT.md` (surface 7, three nouns), `docs/tokens.md` (two glyphs), `docs/components.md` (C24–C27 and their spec), `docs/copy-deck.md` (W8 and W9 keys), `docs/screen-map.md` (new ids, aliases, retired ids).

## Files
- `Delegate Watch.dc.html`: frames W1–W9, E1–E22. The last section, "Proposed: delegate runs", holds W8, W9 and E15–E22.
- `Theme 2c Guide and Quiet Reference.dc.html`, `content.js`, `assets/`: frames D1–D8
- `support.js`: runtime for the two HTML files, not part of the product
- `docs/CONTEXT.md`, `docs/tokens.md`, `docs/components.md`, `docs/copy-deck.md`, `docs/accessibility.md`, `docs/screen-map.md`
- `briefs/v0.3.0.md`

Against v0.2.0: `briefs/v0.2.0.md` is replaced by `briefs/v0.3.0.md`; no other file is added or removed.

## Changed frames
| Frame | Alias | Change |
|---|---|---|
| W8 | 5d | new: `delegate runs` |
| W9 | 4c | new: `delegate runs --timeline` |
| E15 | — | new: W8 at 80 × 24 |
| E16 | — | new: W8 with NO_COLOR=1 LANG=C |
| E17 | — | new: W8 with no run, then with no run index |
| E18 | — | new: W8 in the light sample theme |
| E19 | 4n | new: W9 at 80 × 24 |
| E20 | 4p | new: W9 with NO_COLOR=1 LANG=C |
| E21 | 4q | new: W9 with no run, then with no run index |
| E22 | 4r | new: W9 in the light sample theme |
| W1–W7, E1–E14 | | content unchanged; rendering fixes only |
| D1–D8 | | unchanged |

Retired frames (explorations of this version, never handed off): 4a with 4d–4h (fleet table), 4b with 4i–4m (first rollup tree), 4o (timeline one-shot), 4s (comparison grid), 5a, 5b, 5c.

## What to build (W8, W9)
The spec is `docs/components.md`, "Runs (C24–C27)". In short:
- A run index: each run registers its host, repo name, start time and last-known state (a shared directory, or the local state root). No paths in the view.
- `delegate runs` lists the runs of the index and reads each run's snapshot from its host. A host whose read fails or times out is `unreachable`, with its last-known values dim.
- Combined numbers: progress is tickets ended over tickets total across reachable runs that have not ended, never a mean of percents. The fleet ETA is the latest ETA, with the count of runs that have none. Tokens sum `new` and `cache` apart. Time is wall clock. Unreachable and ended runs are out of every sum.
- One kind of value per column. A blank cell means none. No phase, no spinner, no percent per run, no reason, no path, no cost.
- The same rules as the rest of the terminal output: ANSI roles only, no background, every line cut to the width, NO_COLOR and non-terminal output with no escape codes, an ASCII glyph of the same width for every glyph.
- `status --json`, `watch --raw` and the existing frames do not change.

## Owner rulings
| Date | Ruling | Recorded in |
|---|---|---|
| 2026-11-14 | Show only what a person can act on; each column holds one kind of value and lines up on every row. | components.md |
| 2026-11-14 | Host groups are set apart by a blank line; a host's first row carries its totals; the rail joins its runs. | components.md |
| 2026-11-14 | Token columns per run stay, in the colour of the row. A sparkline per run stays. | components.md |
| 2026-11-14 | A built run says `merge`. | components.md, copy-deck.md |
| 2026-11-14 | No row numbers and no copy-the-command prompt. The `do` column names the command; the legend spells it out once. | components.md |
| 2026-11-14 | The rollup tree (W8) is the default view; the timeline (W9) is `--timeline`. Turn ids stay as aliases. The other explorations are retired. | screen-map.md |
| 2026-11-14 | Earlier rulings of v0.2.0 stand (the repo is the primary source; one handoff folder; W/E/D frame ids). | accessibility.md, screen-map.md |

## Dev tickets implied
1. The run index: what a record holds, where it lives, how a run registers and updates it.
2. Reading a snapshot from another host, with a timeout and the `unreachable` state.
3. `delegate runs`: the fleet line, the table, the fit to width and height, the `do` legend, the empty and no-index forms.
4. `delegate runs --watch`: redraw in place, as `watch` does.
5. `delegate runs --timeline`: lanes from journal history (ticket end times), the projection to the ETA, the `all` lane.
6. The journal history that `slip` and `trend` need: the ETA as it read 2h ago, tickets ended per hour.

## Open questions
1. `slip`: the threshold (drawn: 30m later than 2h ago) and the look-back.
2. The fleet trend words (`slowing`, `steady`, `speeding up`) and the rule behind them.
3. "In trouble": drawn as idle past the limit, or stopped. Should paused, halted, failed and unreachable count?
4. How a snapshot is read from another host (the frames assume `ssh <host>`), and what the timeout is.
5. The sample numbers that the brief did not give (per-run tokens and percents, ticket end times, the ended runs' counts and ids) are illustrative.
6. The light theme colours (E18, E22) are a sample; the light SVGs in `docs/assets/terminal/` carry no colour values to copy.
7. Carried from v0.2.0: the search dialog wording in D5 is a guess; the halt and crash messages in E10 and E11 are illustrative; the repo's `design/` folder still holds the earlier terminal handoff; "cursor up to an ended ticket" stays parked.

## Copy keys: 144 (23 new: W8.* and W9.*; 0 retired; 0 values changed)
## Checks: 1 ✓ (file list against v0.2.0 recorded above) 2 ✓ (key count) 3 ✓ (no frame id renumbered; W8, W9, E15–E22 are new ids, turn ids kept as aliases) 4 ✓ (retired frames listed; no key retired) 5 ✓ (rulings dated) 6 partial (W8, W9, E15–E22 measured cell by cell for width and alignment, E16 and E20 for ASCII; not checked against code, since none exists)
