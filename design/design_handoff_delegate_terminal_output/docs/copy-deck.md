# Copy deck

Existing words stay (they are documented and tested): `built`, `failed`, `skipped`, `pending`, `running`, `specialist`, `gates`, `verifier`, `fix-up`, `ACCEPT`, `REJECT`, `report ok`, `gates green`, `gates red`, `stall`, `time limit`, `position <seq>`.

New or changed strings

| Where | Today | Proposed | Why |
|---|---|---|---|
| run line | `tokens 2.9M new, 40.9M cache` | `2.9M new · 40.9M cache` | the plugin already uses this form; `tokens` is implied by `new`/`cache` |
| ticket row | `12m38s used, tokens 149k new, 1.4M cache` | `12m38s  149k · 1.4M` under column headers `used` / `new · cache` | columns replace the labels |
| ticket row | — | `after 69` / `→ 71` | shows what blocks what |
| ticket row | — | plan index `12` | shows plan order |
| running row | `last activity 2s ago`, then `active 2s` | live view on a terminal: a spinner `⠋` under 30 s since the last activity, `quiet 45s` from 30 s to the stall limit, `⚠ idle 6m40s` past it; a snapshot (`status`, the run picker, the plugin, the live view with `NO_COLOR`): `active 2s ago` | #147: `active 2s` read as a duration ("active for 2 seconds") next to `19m` used; the row and the waiting line already show the duration, so the live view shows a sign of life, and a snapshot says `ago` |
| running row, gate | `gate python3 -m pytest -q, gate log changed 30s ago` | `gate pytest · log 30s ago` | uses `gate_name()` already in live.py |
| folded pending tail (last row above the divider) | — | `· 11 pending` · `134–150` · `rows 30–40 folded` | the remaining work, always visible |
| folded built rows (middle) | — | `✓ 17 built` · `104–120` · totals · `rows 4–20 folded` | only when the tail fold is not enough |
| pause note on the next row | `paused before this step` | `pauses before this step` (live tail) | present tense while pausing; `paused before this step` once paused |
| column headers | — | `#  ticket  state  phase  used  new · cache` | dim, one line |
| idle past threshold | — | `⚠ idle 6m40s` | matches plugin |
| no run active | unchanged | unchanged | contract-ish; coordinators read it |
| `status` text lines | `ticket <id>: state …` + `  progress:` | the table (components.md) | decided: changelog entry, no `--plain` |
| `run` start | `run <id>`, `journal <path>` | + `follow it with delegate watch · pause it with delegate pause <id>` | decided |
| `run` end | per-ticket stderr lines only | + `run <result> · <used> · n built · n failed · n skipped · <new> new · <cache> cache` and, on a failure, `retry with delegate run --resume <id> --retry <ticket>` | decided |

| several active runs, TTY | — | `3 runs are active in this repository` + table + `watch which? [1]` | picker; default is the newest |
| several active runs, non-TTY | — | `delegate watch: 3 runs are active in this repository; give a run id: <id> (<branch>, <state>), …` exit 2 | coordinators cannot answer a prompt |
| run id argument | full id | full id or its short form (`7f21`) | less typing; the short id is already shown in titles |

Never: paths, `None`, cost, exclamation marks.
