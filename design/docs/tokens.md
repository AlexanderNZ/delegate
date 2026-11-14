# Tokens

Token values only. The terminal never sets an RGB value or a background: its tokens are ANSI roles (`src/delegate/run/output.py` `ROLES`). The docs site tokens are CSS custom properties (`docs/stylesheets/delegate.css`).

## Terminal: colour roles
The mock colour is the owner's dark navy theme, used only to draw the frames. It does not ship.

| Token | ANSI (output.py) | Mock colour | Used for |
|---|---|---|---|
| `fg` | default foreground | `#dde5ee` | event text, run-branch names, numbers on the running row |
| `dim` | faint, SGR 2 (not bright black: it can be the background in some themes) | `#7f8fa0` | time, tokens, `used`, ended and pending rows, column headers, the divider, parentheticals, `quiet 45s`, fold rows |
| `bold` | SGR 1 | — | short run id, the running ticket id and `running`, the run result, `ACCEPT`/`REJECT`, `#id` on a `started` line, verdicts in `status` |
| `ok` | green, 32 | `#8fd19e` | `✓`, `●` built node, `gates green`, done phases, bar fill, `run built`, `✓ accepted` |
| `now` | cyan, 36 | `#7dcfff` | `▶`, the running ticket id, `running`, the active phase `●`, the spinner, `active 2s ago`, the waiting line |
| `warn` | yellow, 33 | `#e8c56a` | `REJECT`, `fix-up`, `continuation`, `cont 2/2`, `paused`/`pausing`, `⚠ idle`, `⚠` problem mark, problem lines (#222), pause cells |
| `bad` | red, 31 | `#f07a7a` | `✗`, `failed`, `rejected`, `halted`, `gates red`, `stall`, `run failed`, failure reasons, error lines |
| `plan` | blue, 34 | `#8ab4f8` | the rail `│` and the ids in `after` |

Rules
- At most one accent per line besides `dim`. Exceptions: a phase track mixes `ok` and `now`; a `gates red` parenthetical names the red gate in `bad`.
- `dim` for facts read second and for rows that are over. Never dim a state word on a running or failed row.
- Bold marks the one thing running, the verdict word and the run result.
- `NO_COLOR` set and not empty, or the stream not a terminal: no SGR codes. Standard error is checked on its own.

## Terminal: glyphs (`output.Glyphs`)
| Glyph | Unicode | ASCII | Use |
|---|---|---|---|
| built | `✓` | `+` | built, ACCEPT |
| failed | `✗` | `x` | failed, problem |
| running | `▶` | `>` | running ticket |
| pending | `·` | `.` | pending ticket, and the separator ` · ` |
| node | `●` | `*` | built node, active phase |
| open | `○` | `o` | phase to come |
| skipped | `–` | `-` | skipped |
| fill / empty | `█` / `░` | `#` / `.` | bar |
| rail | `│` | `\|` | blocker is the row above |
| rule | `─` | `-` | divider |
| idle | `⚠` | `!` | idle past threshold; problem mark |
| dash | `–` | `-` | id and row ranges |
| spinner | `⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏` | `\| / - \` | active agent, one frame each 125 ms |
| sparkline (proposed, `runs`) | `▁▂▃▄▅▆▇` | `.:-=+*#` | tickets ended in a cell, scaled to the largest cell; blank is none; `dim` |
| now column (proposed, `runs --timeline`) | `┊` | `:` | the present, through every lane; `now` |

## Terminal: spacing and widths
- Columns padded to the wider of the track and the widest value; 2 cells between columns; numbers right-align.
- One blank line under the run line; the divider between table and feed.
- Tracks (watch): `#` 3 · rail 2 · `plan` 6 · `state` 10 · `phase` 13 · `after` 8 · `used` 7 · `new` 5 · `cache` 6 · tail. `status`: `state` 8, `verdict` 7, `branch` 15.
- Bar 16 cells, only at 100 columns or more. Fit to width: drop `new` and `cache`, then `used`.
- Feed tracks: time 5 · 2 · subject (≥ 3, widest subject) · 2 · text.
- Feed keeps at least 4 lines; fold keeps 3 first and 3 before the running ticket.

## Docs site: colour
Light is `[data-md-color-scheme="default"]`, dark is `slate`. Mapped onto Zensical `--md-*` variables in `delegate.css`.

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--dlg-bg` | `#ffffff` | `oklch(0.2 0.006 60)` | page background |
| `--dlg-ink` | `oklch(0.27 0.01 60)` | `oklch(0.9 0.006 80)` | body text, h1 |
| `--dlg-ink-soft` | `oklch(0.36 0.01 60)` | `oklch(0.82 0.006 80)` | admonition body, search snippets |
| `--dlg-head` | `oklch(0.5 0.14 35)` | `oklch(0.75 0.12 40)` | h2, h3, NAME/SYNOPSIS labels, table labels, IMPORTANT |
| `--dlg-link` | `oklch(0.48 0.13 255)` | `oklch(0.76 0.1 255)` | links, NOTE, glossary terms |
| `--dlg-muted` | `oklch(0.52 0.01 60)` | `oklch(0.66 0.01 70)` | breadcrumbs, numbers, ¶, captions, th text |
| `--dlg-rule` | `oklch(0.9 0.005 80)` | `oklch(0.32 0.008 60)` | 1px rules and borders |
| `--dlg-side` | `oklch(0.975 0.004 80)` | `oklch(0.23 0.006 60)` | sidebar, guide th, diagram nodes |
| `--dlg-code-bg` | `oklch(0.965 0.004 80)` | `oklch(0.25 0.006 60)` | code |
| `--dlg-code-ink` | `oklch(0.36 0.03 35)` | `oklch(0.85 0.05 40)` | inline code, SYNOPSIS, code strings |
| `--dlg-stripe` | `oklch(0.975 0.004 80)` | `oklch(0.23 0.006 60)` | reference table stripes |
| `--dlg-mark` | `oklch(0.93 0.09 95)` | `oklch(0.45 0.08 95)` | search highlight |

## Docs site: type, radii
- `--dlg-serif` Noto Serif (body), `--dlg-sans` Open Sans (headings, chrome), `--dlg-mono` Noto Sans Mono (code). Google Fonts.
- Radii 3px (inline code, version pill), 4px (code blocks, inputs, buttons). No shadows.
