# Screen map

| Frame | Surface | Source of truth in repo | State shown |
|---|---|---|---|
| current | `watch` live view, today | `src/delegate/run/live.py`, `run/status.py:view_lines`, owner screenshot | running, 11/13, ticket 70 specialist |
| 2a | `watch` — table + current ticket only (chosen) | same | #70 in the verifier |
| 2b | `watch` picker when several runs are active; non-TTY line | `status-and-watch.md` "the newest run that ended" (extended) | 3 active runs: 2 running, 1 paused |
| 1d–1h | 2a in other states | `status-and-watch.md` (problem events, stall, pause, run-end) | REJECT→fix-up, problem exit (continuation 2), stall exit, paused, run built (final frame), 80 columns |
| 1i | `status` one-shot | `status.py:render` | running |
| 1j | `watch --raw` | `cli/watch.py` | running, exit at problem |
| 1k | `run` output + Ctrl-C line | `run.md` "Output and exit codes" | ended failed, one skipped |
| 1l | ended-run line, errors | `status-and-watch.md` "The newest run that ended" | no active run |
| 1m | Claude Code plugin: status line, band, toast, pane | `plugins/claude-code/hooks/register.tsx` | running, stalled |

Blocked-by relations in the mock (illustrative; the real values come from the workflow): 72 after 66 · 67 after 72 · 70 after 69 · 71 after 70.
