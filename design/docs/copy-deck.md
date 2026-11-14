# Copy deck

Keys are permanent. A value is what the repo prints today (commit `ce17ee5`). v0.1.0 keys keep their names; changed values are marked "changed in v0.2.0".

## Voice rules
V1. Short, plain, lower case except ids, verdicts and proper names. No exclamation marks.
V2. One line per fact; parts joined by ` · ` in the feed and on the run line, `, ` in the `status` header.
V3. State failures as the engine's: "the step failed", never "you".
V4. Numbers in short form: feed durations 45s / 36m / 10h42m; table durations 45s / 12m05s / 2h03m / 3d04h; tokens 950 / 26k / 1.2M.
V5. No path (except `status`, `run` and errors), no None, no tool call, no cost.

## common
| Key | Value | Note |
|---|---|---|
| common.separator | ` · ` | live.py SEPARATOR; ASCII ` . ` |
| common.accepted | ✓ | |
| common.problem | ✗ | |
| common.none | - | status.py NONE |
| common.tokens | tokens {new} new, {cache} cache | `status` progress line and the feed run-end line |
| common.used | {duration} used | |
| common.eta | ETA {duration} | |
| common.lastActivity | active {duration} ago | changed in v0.2.0 (#147); snapshots only |
| common.gate | gate {name} | short name or workflow `name` (#223) |
| common.gateLogChanged | log {duration} ago | changed in v0.2.0 |
| common.tokensShort | {new} new · {cache} cache | new: run line, waiting line, run summary, plugin |
| common.quiet | quiet {duration} | new (#147) |
| common.idle | ⚠ idle {duration} | new |
| common.spinner | ⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏ | new; ASCII `\| / - \` |

## W1 Live view
| Key | Value | Note |
|---|---|---|
| W1.run.line | run {shortId}  {state}[ · {phase}]  {bar} {percent}%  {ended}/{total} ended  {used} used  ETA {eta}  {tokensShort} | changed in v0.2.0 |
| W1.ticket.built | ● {id}  ✓ built  {used}  {new}  {cache} | changed in v0.2.0 (row) |
| W1.ticket.running | ▶ {id}  running  {phase}  {used}  {new}  {cache}  {tail} | changed in v0.2.0 |
| W1.ticket.pending | · {id}  pending | changed in v0.2.0: no used, no tokens |
| W1.ticket.failed | ✗ {id}  failed  …  {reason}[ (traceback {file})] | new |
| W1.ticket.skipped | – {id}  skipped  …  {reason} | new |
| W1.table.header | #  plan  state  phase  after  used  new  cache | new |
| W1.phase.done | {phase} ✓ | new |
| W1.phase.active | {phase} ● | new |
| W1.phase.pending | {phase} ○ | new |
| W1.phase.gates | gates ● {n}/{total} | new |
| W1.phase.fixup | fix-up ● {round}/{limit} | new; done: `fix-up ✓ {round}/{limit}` |
| W1.tail.continuation | cont {count}/{limit} | new |
| W1.tail.gate | gate {name} · log {duration} ago | new |
| W1.tail.problemMark | ⚠ {tail} | new (#222) |
| W1.tail.pausing | pauses before this step | new |
| W1.tail.paused | paused before this step / paused before the gate / paused before the verifier | new |
| W1.fold.built | ✓ {n} built[, {n} failed …] | new; glyph of the first state |
| W1.fold.pending | · {n} pending | new |
| W1.fold.range | {first}–{last} | new |
| W1.fold.rows | rows {first}–{last} folded / row {n} folded | new |
| W1.feed.runStart | run {runId} started · {mode} · {n} tickets | singular "ticket" when n=1 |
| W1.feed.resumed | run resumed | |
| W1.feed.stepStart | started · {agent} | |
| W1.feed.retry | retry at the {phase} | |
| W1.feed.continuation | continuation {count} of {limit} · {trigger} | |
| W1.feed.continuationLimit | continuation limit {limit} reached | |
| W1.feed.hotspot | hotspot finding · {n} paths | |
| W1.feed.rebase | rebase {result} | |
| W1.feed.verifierStarted | verifier started | |
| W1.feed.scopedVerifierStarted | scoped verifier started · round {round} | |
| W1.feed.chainVerifierStarted | verifier started ({tickets}) | economy |
| W1.feed.invariantViolation | invariant violation: the verifier changed the worktree | |
| W1.feed.writerViolation | invariant violation: another writer changed the journal | new |
| W1.feed.fixupStarted | fix-up round {round} started | |
| W1.feed.fixupRefused | fix-up round {round} refused | |
| W1.feed.skipped | skipped · {reason} | |
| W1.feed.notSkipped | not skipped · {outlook} | new |
| W1.feed.agentResult | {role} {endState} · {duration} · {tokensShort} · report {reportState} | endState adds `timed out` |
| W1.feed.waiting | {role} running · {duration} · {tokensShort} · {liveness} | new |
| W1.feed.gates | gates {colour} ({items}) | |
| W1.feed.accept | ✓ ACCEPT · {stepState} | |
| W1.feed.reject | REJECT · {n} findings | |
| W1.feed.stepFailed | failed · {reason} | |
| W1.feed.runEnd | run {result} · {duration}[ · reason: {reason}] · {tokens} | changed in v0.2.0: reason |
| W1.feed.stackVerdict | stack {name} ✓ ACCEPT · run built · {duration} · {tokens} | |
| W1.feed.problem | ✗ {subject} {problem} | changed in v0.2.0: `warn`, the run goes on (#222) |
| W1.feed.stall | ✗ stall · no change for {minutes}m | |
| W1.feed.timeLimit | time limit · {minutes}m used · the run goes on | |
| W1.feed.paused | paused · before {where} · go on with delegate run --resume {runId} | new |
| W1.feed.halted | halted · {where} · {message} · go on with delegate run --resume {runId} | new |
| W1.feed.stackChange | stack {stack} changed: {fields} | new |
| W1.position | position {seq} | |
| W1.interrupted.stderr | watch: stopped at position {seq}; the run goes on. Resume with delegate watch {runId} --from {seq} | |

## W2 status
| Key | Value | Note |
|---|---|---|
| W2.header.run | run {runId}: {state} (mode {mode}, adapter {adapter}) | |
| W2.header.note | note: {note} | |
| W2.header.progress | progress: [{phase}, ]{percent}%, {ended} of {total} ended, {used} used[, ETA {eta}][, {tokens}] | |
| W2.header.journal | journal {path} | |
| W2.header.stackChange | stack {stack} changed: {fields} | #290 |
| W2.header.keptBranch | kept: branch {branch} holds the old work of ticket {ticket}, which a retry started again; delegate clean removes it | |
| W2.table.header | #  plan  state  phase  after  verdict  branch  used  new  cache  last event | |
| W2.note.pauseRequest | a pause request waits; delegate run --resume removes it | |
| W2.note.stopped | no process works on the run; go on with delegate run --resume {runId}[; the resume removes the pause request that waits] | |
| W2.note.halted | halted at the {role} of ticket {ticket}: {message} | |
| W2.note.crashed | crashed at {where}: {error}: {message}[ (traceback {file})]; go on with delegate run --resume {runId}[ --retry {ticket}] | |
| W2.note.setupProblem | the setup check stopped the run before the first agent session: {problems}; change the setup, then go on with delegate run --resume {runId} | |
| W2.note.abandoned | abandoned: {reason} | |
| W2.chain.accepted | ✓ accepted | economy chain row |
| W2.chain.rejected | rejected | |

## W4 Run picker
| Key | Value | Note |
|---|---|---|
| W4.title | {n} runs are active in this repository | |
| W4.header | #  run  run branch  mode  since  progress  now | |
| W4.progress | {ended}/{total} ended · {percent}% | |
| W4.now | [{state} · ]▶ {id} {phase} · active {duration} ago | |
| W4.nowIdle | [{state} · ]no ticket running · active {duration} ago | |
| W4.prompt | watch which? [1] | |
| W4.nonTty | delegate watch: {n} runs are active in this repository; give a run id: {runId} ({branch}, {state}), … | exit 2 |

## W5 run
| Key | Value | Note |
|---|---|---|
| W5.start.run | run {runId} | |
| W5.start.journal | journal {path} | |
| W5.start.hint | follow it with delegate watch · pause it with delegate pause {shortId} | |
| W5.ticket.failed | delegate run: ticket {ticket}: {reason} | stderr |
| W5.ticket.skipped | delegate run: ticket {ticket}: skipped: {reason} | stderr |
| W5.end.summary | run {result} · {used} · {n} built · {n} failed · {n} skipped · {tokensShort} | |
| W5.end.retry | retry with delegate run --resume {shortId} --retry {ticket}[ --coordinator-commit <sha>] | |
| W5.halted | delegate run: halted at {where}: {message} | stderr |
| W5.crashed | delegate run: crashed at {where}: {error} | stderr |
| W5.interrupted | delegate run: interrupted; the run {runId} stopped. Go on with delegate run --resume {runId} | stderr, 130 |

## W6 Plugin
| Key | Value | Note |
|---|---|---|
| W6.statusLine | delegate {shortId} · {percent}% · ETA {clock} · ▶ {id} {phase} · active {duration} ago · {tokensShort} | |
| W6.band | ⚠ delegate {shortId}: no agent activity for {duration} (ticket {id} {phase}) | |
| W6.toast.ticket | delegate {shortId}: ticket {id} {state} | |
| W6.toast.runEnd | delegate {shortId}: the run ended, {state} | |
| W6.pane.after | after {ids} | |

## W7 watch --json
| Key | Value | Note |
|---|---|---|
| W7.watchEnd | {"event": "watch-end", "reason": "{reason}", "exit_code": {code}} | only for an end that is not the run end |

## W8 / W9 runs (proposed, no code yet)
| Key | Value | Note |
|---|---|---|
| W8.fleet.line | runs  {reachable} of {total} reachable  {bar} {percent}%  {ended}/{total} ended  ETA {eta} · {n} with no ETA  {tokensShort} | |
| W8.fleet.second | {start}–{now}  ⚠ {n} in trouble  trend: tickets ended an hour, last 6h · all {sparkline} {trend} | |
| W8.fleet.trend | slowing / steady / speeding up | words provisional |
| W8.table.header | host  repo  run  state  ended  ETA  slip  new  cache  trend  alert  do | |
| W8.state.unreachable | unreachable | |
| W8.alert.fixup | fix-up {round}/{limit} | |
| W8.alert.idle | ⚠ idle {duration} | |
| W8.alert.heard | heard {duration} ago | |
| W8.slip | +{duration} | |
| W8.do.watch | watch | delegate watch {run} |
| W8.do.resume | resume | delegate run --resume {run} |
| W8.do.retry | retry | delegate run --resume {run} --retry {ticket} |
| W8.do.merge | merge | git merge --ff-only {runBranch} |
| W8.do.checkHost | check host | ssh {host} |
| W8.legend.otherHost | on another host, put ssh {host} in front of each command | |
| W8.empty | runs  0 of {n} hosts have an active run | |
| W8.empty.ended | no run ended in the last {window} | |
| W8.noIndex | no run index is set up: these are the runs of this machine | |
| W9.fleet.legend | ● a ticket ended · ░ to the ETA · one cell is {minutes}m | |
| W9.axis.label | last {window}, to the latest ETA | |
| W9.lane.all | all | |
| W9.table.header | ended  ETA  state  alert | |
| W9.footer | detail  delegate watch {run}; on another host, ssh {host} delegate watch {run} | |

## E2 / E11 No active run
| Key | Value | Note |
|---|---|---|
| E2.ended | no run is active; the newest run {runId} ended {result} at {time}[; reason: {reason}][; first problem: {problem}] | |
| E2.fromBeyond | delegate watch: journal {path} holds {n} events, so --from {position} is beyond its end | exit 2 |
| E11.stopped | no run is active; the newest run {runId} stopped at {time} with no run-end, and no process works on it; {resume} | |

## docs (D1–D8)
| Key | Value | Note |
|---|---|---|
| docs.header.guide | Reference Guide | |
| docs.header.version | main | |
| docs.header.contents | Contents | drawer button |
| docs.side.search | Quick search | uppercase by CSS |
| docs.side.placeholder | Search the guide | |
| docs.side.toc | Table of contents | uppercase by CSS |
| docs.man.manual | {site} Manual | |
| docs.man.name | NAME | |
| docs.man.synopsis | SYNOPSIS | |
| docs.pager.previous | ← Previous | Zensical `footer.previous` |
| docs.pager.next | Next → | Zensical `footer.next` |
| docs.pager.version | Version main | |

Never: paths in the live view, `None`, cost, exclamation marks.
