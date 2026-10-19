# Screen map

| Frame | Surface | Source of truth in repo | State shown |
|---|---|---|---|
| current | `watch` live view, today | `src/delegate/run/live.py`, `run/status.py:view_lines`, owner screenshot | running, 11/13, ticket 70 specialist |
| 3a | `watch` — flat plan table + current ticket (chosen) | same | run b9cb pausing, #128 specialist, 11-ticket chain |
| 3b | `watch` — 40-ticket plan with two branches, fitted to 24 rows | same; `components.md` Fit to height | #124 gates 2/3; middle fold of built rows, tail fold of pending |
| 2b | `watch` picker when several runs are active; non-TTY line | `status-and-watch.md` "the newest run that ended" (extended) | 3 active runs: 2 running, 1 paused |
| 1d–1h | 3a in other states | `status-and-watch.md` (problem events, stall, pause, run-end) | REJECT→fix-up, problem exit (continuation 2), stall exit, paused, run built (final frame), 80 columns |
| 1i | `status` one-shot | `status.py:render` | running |
| 1j | `watch --raw` | `cli/watch.py` | running, exit at problem |
| 1k | `run` output + Ctrl-C line | `run.md` "Output and exit codes" | ended failed, one skipped |
| 1l | ended-run line, errors | `status-and-watch.md` "The newest run that ended" | no active run |
| 1m | Claude Code plugin: status line, band, toast, pane | `plugins/claude-code/hooks/register.tsx` | running, stalled |

Blocked-by relations in the mock (illustrative; the real values come from the workflow): 72 after 66 · 67 after 72 · 70 after 69 · 71 after 70.

3b relations: 131 after 124 · 126 after 125 · 133 after 126 and 132; all others after the row above.
