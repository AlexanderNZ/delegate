# Screen map

Prefixes: W terminal surface, E terminal state or edge case, D docs site. IDs are permanent. "Alias" is the earlier turn id that the frame still carries as an anchor in the design file.

Design files: `Delegate Watch.dc.html` (W, E), `Theme 2c Guide and Quiet Reference.dc.html` with `content.js` (D).

## Terminal
| Frame | Alias | Surface | Source in repo | State shown |
|---|---|---|---|---|
| W1 | 3a | `watch` live view | `run/live.py`, `run/status.py` `view_lines`, `cli/watch.py` | run b9cb pausing, #128 specialist, 11-ticket chain |
| W2 | 1i | `status` | `run/status.py` `render`, `cli/status.py` | running, #70 specialist |
| W3 | 1j | `watch --raw` | `run/watch.py` `format_event` | running, exit at problem |
| W4 | 2b | run picker; non-TTY line | `run/status.py` `picker_lines`, `choices_line`; `cli/watch.py` | 3 active runs |
| W5 | 1k | `run` output, Ctrl-C | `cli/run_command.py` | ended failed, one skipped |
| W6 | 1m | Claude Code plugin | `plugins/claude-code/hooks/register.tsx` | running, idle |
| W7 | — | `watch --json` | `run/watch.py` `JsonSink` | stall end |
| W8 | 5d | `runs` (proposed) | none yet: run index and command do not exist | 6 runs on 3 hosts, 2 ended |
| W9 | 4c | `runs --timeline` (proposed) | none yet | the same runs as lanes over the last 6h |
| E1 | 1l (part) | watch Ctrl-C stderr line | `cli/watch.py` | interrupted |
| E2 | 1l | ended-run line, errors | `run/watch.py` `EndedRun` | no active run |
| E3 | 3b | 40 tickets fitted to 24 rows | `status.py` `_fit` | middle and tail fold |
| E4 | 1d | REJECT → fix-up | `live.py`, `status.py` `_phases` | #72 fix-up 1/2 |
| E5 | 1e | problem, run goes on (#222) | `live.py` problem branch | #70 second continuation |
| E6 | 1e (stall) | stall exit | `live.py` `stall` | ⚠ idle, exit 5 |
| E7 | 1f | paused | `status.py` `_reason`, `live.py` `_paused` | paused before #71 |
| E8 | 1g | run built, final frame | `live.py` `_run_end` | 13/13 |
| E9 | 1h | 80 × 24 | `status.py` `FIT_STEPS` | bar, new, cache dropped |
| E10 | — | halted run | `status.py` `halt_note`, `live.py` halted | harness failure at the verifier of #70 |
| E11 | — | stopped run (crash, no process) | `status.py` `stopped_note`, `journal.py` `crash_note`, `run/watch.py` `StoppedRun` | crash in the gates of #70 |
| E12 | — | abandoned run | `journal.py` `abandon_note`, `run/watch.py` `EndedRun` | abandoned with a reason |
| E13 | — | economy chain rows (#196) | `status.py` `_chain_row` | stack python verifier, stack web accepted |
| E14 | — | `status` stack-change and kept-branch lines | `status.py` `render` | after a retry and a stack change |
| E15 | — | `runs` at 80 × 24 (proposed) | none yet | new, cache, trend, slip, repo dropped |
| E16 | — | `runs`, NO_COLOR LANG=C (proposed) | none yet | ASCII, no colour |
| E17 | — | `runs`: no run; no run index (proposed) | none yet | empty, then this machine only |
| E18 | — | `runs`, light sample theme (proposed) | none yet | as W8 |
| E19 | 4n | `runs --timeline` at 80 × 24 (proposed) | none yet | one cell 12m; ETA dropped |
| E20 | 4p | `runs --timeline`, NO_COLOR LANG=C (proposed) | none yet | ASCII, no colour |
| E21 | 4q | `runs --timeline`: no run; no run index (proposed) | none yet | empty, then this machine only |
| E22 | 4r | `runs --timeline`, light sample theme (proposed) | none yet | as W9 |

Retired: `current` (today's frame before #143; the repo keeps it in its history). The v0.1.0 transcription of W1 (`frames/W1-current-live-view.txt`) is retired with it; W1 is now the live view as shipped.

Blocked-by in the mocks (illustrative): W1 every ticket after the row above; E4–E9 72 after 66 · 67 after 72 · 70 after 69 · 71 after 70; E3 131 after 124 · 126 after 125 · 133 after 126 and 132.

`delegate runs` (W8, W9, E15–E22) is proposed: no code exists, and the command names are provisional. Owner rulings 2026-11-14: W8 (rollup tree) is the default view and W9 (timeline lanes) is `--timeline`; the turn ids 5d, 4c, 4n, 4p, 4q, 4r stay as aliases. Retired the same day: 4a with 4d–4h, 4b with 4i–4m, 4o, 4s, 5a, 5b, 5c. Sample: now 14:20, window last 6h; hosts laptop (this machine), build-host, ci-runner; runs 3c98, 1195, 36c5, a41e, 77b0, 5d02 (unreachable); ended e2b1 (built), 90fa (failed). Token counts, ticket end times and the ended runs' counts are illustrative.

## Docs site
| Frame | Alias | Page | Source in repo | State shown |
|---|---|---|---|---|
| D1 | docs 2c | Start here | `docs/overview.md`, `zensical.toml` nav | light/dark |
| D2 | docs 2c | Tutorial | `docs/tutorial.md` | `heading_numbers: false` |
| D3 | docs 2c | Reference: delegate run | `docs/reference/run.md`, `overrides/main.html` | man header, NAME/SYNOPSIS, labels |
| D4 | docs 2c | Glossary | `docs/glossary.md` | |
| D5 | docs 2c | Search | Zensical search dialog, `delegate.js` SEARCH_CSS | query "verifier" |
| D6 | docs 2c | Mobile | `delegate.css` narrow screens | drawer closed |
| D7 | — | Glossary term link (on D1) | `delegate.js`, `glossary.js`, `includes/abbreviations.md` | hover |
| D8 | — | Diagram figure (on D1) | `docs/overview.md` Mermaid, `delegate.js` | sequence diagram |

Retired: docs 1a, 1c, 2a, 2b (theme explorations; the repo keeps 2c).
