# Accessibility

## Terminal
A1. Every fact is in text; no fact is carried by colour alone. Checked by: read the frame with colour stripped. Today: passes.
A2. Every glyph has an ASCII fallback of the same width (tokens.md). Checked by: `LANG=C`. Today: passes (`output.ASCII`, `SPINNER_ASCII`).
A3. At 80 columns the run state, each ticket's state and phase, and each feed subject show without cutting. Today: passes (bar, new, cache, used drop; phases stack).
A4. Order in a line: who, what, then numbers. Today: passes.
A5. Local time HH:MM on every feed line. Today: passes.
A6. No path, no None in the live view. Today: passes (`output.without_paths`).
A7. Problems are distinguishable with colour off and on 16 colours: `✗` and `⚠` glyphs, `warn` / `bad` roles. Today: passes.
A8. Screen reader: the final frame is plain text in scrollback; the cursor shows again on every end. Today: passes.
A9. The spinner is never the only sign of liveness: it turns only while the agent is active, then `quiet 45s`, then `⚠ idle`; no animation with `NO_COLOR` or no terminal (`active 2s ago`). Today: passes (#147).

## Terminal invariants
T1. Single-frame redraw in place, no alternate screen, no mouse. The first frame (and a frame after a resize) clears and draws in full; later frames are one synchronized update (`?2026h`/`?2026l`) that writes only changed lines (#280, #282).
T2. The frame fits the height: the table folds (components.md, Fit to height); the feed keeps at least 4 lines.
T3. Each line cut to the terminal width.
T4. Title set with OSC 2 (short run id, percent, ETA; the result at the end); off with DELEGATE_NO_TITLE=1.
T5. `--raw`, `--json` and non-TTY print one line per event; the view never changes them.
T6. Ctrl-C, SIGTERM, SIGHUP: last frame, show cursor, one stderr line, exit 130.

## Docs site
A10. Body text and `muted` captions meet 4.5:1 in light and dark. Checked by: contrast of each token pair in tokens.md.
A11. Heading numbers, table labels and the man header are decoration: the man header is `aria-hidden`; headings keep their text.
A12. Glossary terms read as prose: a dotted underline marks them, the link colour shows on hover, and the tooltip repeats the glossary definition.
A13. Each diagram has the same information as text or a table next to it (CONTRIBUTING rule); a wide diagram scrolls sideways in its figure.
A14. The quick-search field is reachable by keyboard and opens the search dialog with Enter or Space.

## Rulings
| Date | Ruling | Affects |
|---|---|---|
| 2026-11-14 | The repo is the source of truth for every frame; the frames show what commit `ce17ee5` prints. | all |
