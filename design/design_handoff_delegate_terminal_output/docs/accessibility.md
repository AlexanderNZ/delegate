# Accessibility — terminal

- Colour is never the only signal. Every state has a glyph or a word: `✓ built`, `▶ running`, `✗ failed`, `· pending`, `REJECT`. A monochrome terminal reads the same.
- Theme colours: using ANSI roles means contrast is whatever the user's theme gives; we do not set backgrounds, so there is no fg/bg pairing we can break. `dim` is used only for secondary facts.
- `NO_COLOR` env var and non-TTY stdout: emit no SGR codes. `--raw` is already plain.
- Glyph fallback: when `LANG`/`LC_ALL` is not UTF-8, use ASCII: `ok`→`+`, `x`, `>`, `.`, `[#  ]` bar, `|` rail, ids as-is in `after`.
- Line length: every line still cut to columns; nothing wraps, so screen readers that read line by line get one fact per line.
- Redraw: the frame replaces itself in place (as today) — no scrolling flicker; the final frame is left in scrollback.
- Minimum: readable at 80 columns × 24 rows (see frame 2h).
