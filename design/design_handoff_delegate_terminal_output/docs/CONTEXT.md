# Delegate terminal views — design context

Source repo: `example/delegate` (main). Design docs here mirror `design/` in the repo; the repo is canonical.

## What we are designing
The text that `delegate` prints on a terminal. Six surfaces, one vocabulary:

1. `delegate watch` live view (block + feed, redrawn each poll) — the primary surface.
2. `delegate status` one-shot report.
3. `delegate watch --raw` / non-TTY lines (machine-read by a coordinator; format is a contract, restyle only).
4. `delegate run` stdout/stderr and the end-of-run lines.
5. The "no run is active; the newest run … ended" line and error lines.
6. The Claude Code plugin: status-line entry, warning band, toasts, `/delegate` pane (reads `status --json`; text only, no colour except the band).

## Reader
One person, glancing at a terminal on a developer machine while agents work. Not debugging; debugging is `--raw` and the journal.

## Pain in the current output (from the owner)
- Hard to see where each ticket is right now.
- Ticket order vs plan order is confusing (ids are not in plan order; `72` runs before `67`).
- Nothing shows what blocks what.

## Constraints
- Terminal, single-frame redraw in place, no alternate screen, no mouse.
- Respect the user's terminal theme: use the 16 ANSI colours + bold/dim only. Never hard-code a background or foreground. Truecolour and Unicode box drawing/glyphs are available but colour must come from the theme palette.
- Every line is cut to the terminal width; the frame fits the terminal height (feed keeps newest lines).
- Facts shown are only those the snapshot and journal hold (see `docs/reference/status-and-watch.md` in the repo). No cost, no paths, no `None`.
- `--raw` lines and `status --json` are contracts: unchanged.
- The plugin shows text only; its colours are the host's.

## Decisions so far
- Direction: 3a — the ticket table is the record; the feed below the divider holds only the events of the ticket that runs now. Ended tickets get no feed lines.
- Tickets run one at a time (both modes), so the feed is one group. The economy chain verification is headed `stack <name>`.
- Stay a redrawn CLI frame, not a keyboard TUI, for now.
- `status` text form becomes the table (changelog, no `--plain`). `delegate run` gains the follow hint, the closing summary and the retry hint.

## Open
- "Cursor up to a ticket and see what happened in it" — owner wants a way to ask "did it blend" of an ended ticket, but can't yet say what it should show. Parked. Likely a flag (`watch --ticket 66` / `status 66`) before it is a key.

## Non-goals
Web dashboard, mouse interaction, new data the engine does not record.

## Files
- `Delegate Watch — Current.dc.html` — pixel recreation of today's `watch` frame (from the owner's screenshot).
- `Delegate Watch v2.dc.html` — three directions, states, and the other five surfaces.
- `docs/tokens.md`, `docs/components.md`, `docs/screen-map.md`, `docs/copy-deck.md`, `docs/accessibility.md`.


## Deep plans (turn 3)
The tree gutter cost one column per level of depth, so a linear chain of 40 tickets ran off the screen. Replaced by flat rows with a rail (`│` = blocked by the row above) and an `after` column (blocker id when it is not the row above). Phases now stack vertically under the running ticket instead of across a 46-cell column. Width is fixed; height is fitted by folding the pending tail into a remaining-work row, then built rows in the middle. Frames `3a` (the owner's 11-ticket chain) and `3b` (40 tickets, two branches, 24 rows).
