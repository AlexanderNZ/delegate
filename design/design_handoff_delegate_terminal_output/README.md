# Handoff: delegate terminal output ("3a — plan table + current ticket")

## Overview
A redesign of what `delegate` prints on a terminal: `delegate watch` (live view), `delegate status`, the `delegate run` lines, the ended-run line, a picker for several active runs, and the Claude Code plugin pane. `watch --raw` and `status --json` do not change.

The idea: the ticket table is the record. It is in plan order, flat: a rail joins a row to the one above when that is its blocker, an `after` column names the blocker otherwise, and each row carries state, time and tokens; the running ticket stacks its phases vertically beneath its row. Below a divider, the feed holds only the events of the ticket that runs now. Once a step ends, its row carries the result and the feed starts again with the next ticket.

Repo: `AlexanderNZ/delegate`, branch `main`. Code that renders today: `src/delegate/run/live.py` (frame and feed), `src/delegate/run/status.py` (`view_lines`, `render`), `src/delegate/cli/watch.py`, `src/delegate/cli/status.py`, `plugins/claude-code/hooks/register.tsx`.

## About the design files
`Delegate Watch v2.dc.html` is a design reference built in HTML. It is a picture of terminal frames, not code to copy. Open it in a browser with `support.js` beside it. Frame ids:

- `3a` the live view, chosen direction (run b9cb, #128 in the specialist, 11-ticket chain).
- `3b` a 40-ticket plan with two branches fitted to 24 rows: tail fold, middle fold, rail gap + `after`.
- `2b` the picker when several runs are active, and the non-terminal line.
- `1d` REJECT → fix-up · `1e` the problem exit and the stall exit · `1f` paused · `1g` run built, final frame · `1h` 80×24.
- `1i` status · `1j` --raw (unchanged) · `1k` run · `1l` ended-run line and errors · `1m` plugin.

`Delegate Watch - Current.dc.html` is the frame as it prints today, for comparison.

The specs are in `docs/`: `CONTEXT.md` (what and why, decisions, open items), `tokens.md` (colour roles, glyphs, spacing), `components.md` (every table and line, with column tracks), `copy-deck.md` (strings that change), `accessibility.md` (NO_COLOR, ASCII fallback, 80 columns), `screen-map.md` (frame → source files).

## Fidelity
High for structure, columns, words and colour roles. The mock renders one theme (the owner's dark navy); the implementation sets no colours of its own, only ANSI roles (`tokens.md`), so it looks different in every theme on purpose. Pixel positions do not matter; cell positions do.

## Rules that matter most
1. Only the 16 ANSI colours, bold and faint. No background colours. Reverse video only for the bar fill. Honour `NO_COLOR` and a non-TTY stdout by writing no SGR codes.
2. Every frame is cut to the terminal width and fits its height. Width is fixed by the tracks (62 cells before the tail), so depth of the plan never widens the table. Height: the tail folds into the remaining-work row first, then built rows fold in the middle (`components.md`, Fit to height); at 80 columns the bar, `new`, `cache` and `used` drop in that order.
3. Column widths are computed from the widest value in the column, per frame. Numbers right-align.
4. Rail and `after` come from `blocked-by` in the plan that `run-start` (or the newest `plan-change`) records. Rail when the blocker is the previous plan row; `after <id>` otherwise. Width is fixed; height is fitted (`components.md`, Fit to height).
5. The feed logic in `live.py` (joins, waiting lines, problem lines, the words) stays. What changes: the feed keeps only the lines of the ticket that runs now (and the chain group in economy mode), and lines are coloured.
6. `watch --raw` and `status --json` are byte-identical to today. The `status` text lines change to the table (decided: changelog entry, no `--plain`).
7. The picker runs only on a terminal and only when more than one run has no `run-end`. Not a terminal: the one stderr line and exit 2. A run id argument accepts the short form (the part after the last `-`).
8. Glyphs: `✓ ✗ ▶ · ● ○ █ ░ │ ─ ⚠`. Without a UTF-8 locale use the ASCII set in `accessibility.md`.

## Acceptance checks
- Frames in `3a`, `3b`, `1d`–`1h`, `1i`, `2b` reproduce from a journal fixture at 120 and 80 columns, 24 rows, with the cells in the positions the mock shows. `3b` needs a 40-ticket fixture with the relations in `screen-map.md`.
- A linear chain of 40 tickets renders with the same column positions as a chain of 3.
- `NO_COLOR=1 delegate watch` and `delegate watch | cat` emit no escape codes; `--raw` output and `status --json` diff clean against the current version on the same journal.
- `LANG=C` renders the ASCII fallback with the same column positions.
- A journal with a REJECT, a continuation, a skip, a pause and a run-end produces the lines in `1d`, `1e`, `1f`, `1g` in that order of events.
- The plugin tests in `plugins/claude-code/tests` still pass; the pane gains the plan index and `after` only.

## Decided
- The `status` text form becomes the table; note it in the changelog.
- `delegate run` gains the three lines in `1k`: the follow/pause hint after `journal`, the closing `run <result> · …` summary, and `retry with …` after a failed step. The documented per-ticket stderr lines stay.

## Open
- "Cursor up to an ended ticket to see what happened in it" is parked (`CONTEXT.md`). Likely `watch --ticket <id>` before any key handling.

## Where to put it
Copy this folder to `design/` in the repo (replace its README with this file, or keep both and link). `support.js` is the runtime the HTML needs; it is not part of the product.

## Implementation notes for the Python
- `status.py`: add `plan_links(events)` that returns, per ticket in plan order, its blockers from `blocked-by` in the newest plan (`run-start.plan` or `plan-change.plan`) and whether the first blocker is the previous row (rail); `view_lines` becomes a column renderer that takes the snapshot, the links, `rows` and `columns`, emits one row per ticket plus phase sub-rows for the running one, folds the tail (then the middle of the built run) to fit `rows`, then drops columns to fit `columns` (order in `components.md`). `render` (the `status` text) reuses it with the three extra columns.
- `live.py`: keep `_read` and the joins. Keep per ticket the lines since its `step-start` (or `verify-start` of a chain); `frame` emits the run line, the table, the divider, and the lines of the running ticket only. Problem lines of a ticket that just ended stay until the next ticket's `started` line. Colour is applied at emit time by role (`tokens.md`), via a small `paint(role, text)` that returns plain text when `NO_COLOR` is set or stdout is not a TTY.
- `cli/watch.py`: with no run id, collect runs with no `run-end`; one → follow it; several and a TTY → print the picker, read one line, default 1; several and no TTY → the stderr line, exit 2. Accept a short id (suffix after the last `-`) wherever a run id is taken.
- `cli/run_command.py`: the three new lines (`copy-deck.md`). The per-ticket stderr lines are unchanged.
- Docs: `status-and-watch.md` and `run.md` prose for the table, the picker and the new lines; the generated sections do not change. Changelog entry for the `status` text form.
- Plugin: pane rows gain the plan index and `after <id>`; nothing else.

## Files in this folder
- `Delegate Watch v2.dc.html` — the design (frames 3a, 3b, 2b, 1d–1m)
- `Delegate Watch - Current.dc.html` — today's frame
- `support.js` — runtime for the two HTML files
- `docs/CONTEXT.md`, `docs/tokens.md`, `docs/components.md`, `docs/copy-deck.md`, `docs/accessibility.md`, `docs/screen-map.md`
