# Tokens — terminal

Colour is semantic and named by ANSI role, so it follows any terminal theme. The implementation never sets a background or an RGB value. The mock renders one sample theme (the owner's) only to show it; the hex values below are that theme's and are not to be shipped.

| Token | ANSI | Mock colour | Used for |
|---|---|---|---|
| `fg` | default foreground | `#dde5ee` | event text, run-branch names, numbers on the running row |
| `dim` | faint (SGR 2) or bright black (90) | `#7f8fa0` | time, tokens, `used`, every cell of an ended or pending row, column headers, the divider, parentheticals |
| `bold` | SGR 1 | — | short run id, the running ticket id and its `running`, the run result word, `ACCEPT`/`REJECT`, `#id` on a `started` line |
| `ok` | green (32) | `#8fd19e` | `✓`, `●` built node, `gates green`, done phases, bar fill, `run built` |
| `now` | cyan (36) | `#7dcfff` | `▶`, the running ticket id, `running`, the active phase `●`, `active 2s`, the waiting line |
| `warn` | yellow (33) | `#e8c56a` | `REJECT`, `fix-up`, `continuation`, `cont 2/2`, `paused`/`pausing`, `⚠ idle`, `paused before this step` |
| `bad` | red (31) | `#f07a7a` | `✗`, `failed`, `gates red` and the red gate's name, problem lines, `stall`, `run failed`, error lines on stderr |
| `plan` | blue (34) | `#8ab4f8` | the rail `│` and the ids in the `after` column |

Rules
- At most one accent colour per line besides `dim`; a line is `ok`, `now`, `warn`, `bad` or plain. Exception: a phase track mixes `ok` (done) and `now` (active), and a `gates red` parenthetical names the red gate in `bad`.
- `dim` is for facts you read second, and for whole rows that are over. Never dim a state word on a running or failed row.
- Bold marks the one thing running, the verdict word and the run result. Nothing else.
- No background colours. Reverse video only for the bar fill, if the terminal has no block glyphs.
- `NO_COLOR` set, or stdout not a terminal: no SGR codes at all.

Glyphs
`✓` built/accept · `✗` failed/problem · `▶` running · `·` pending, and the separator · `●` built node and active phase · `○` pending phase · `–` skipped · `█ ░` bar · `│` rail (blocker is the row above) · `─` divider · `⚠` idle past threshold.
ASCII fallback (no UTF-8 locale): `+ x > . * o - # . \-- - !`.

Spacing
- Columns are padded with spaces to the widest value in the column; 2 cells between columns; numbers right-align.
- One blank line under the run line; the divider between table and feed; no other blank lines.
- Feed: `HH:MM␣␣#id␣text`, parts joined with `␣·␣` (unchanged).

Widths
- Design width 120 columns. At 80: drop the bar, then `new` and `cache`, then phase labels (keep glyphs), then `used`. Fold ended rows first when the height is short.
