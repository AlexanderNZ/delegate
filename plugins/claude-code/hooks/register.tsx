// The delegate plugin: it shows the progress of a delegate run of the repository in Claude Code.
//
// The plugin holds no progress logic. On a timer, it runs `<command> status --json` in the working directory of the
// session, and shows that snapshot: a status line entry, a warning band, toasts, and the pane of the `/delegate`
// command. It runs no other delegate command. When the command fails, or its output is not a snapshot with a
// `percent` field (an old version of delegate), the plugin shows nothing and reports no error.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { DelegateSnapshot, DelegateTicket } from '../types'

const PANE = 'delegate'
const POLL_MS = 5000
const COMMAND_TIMEOUT_MS = 20000
const RUNNING = 'running'
const GATES = 'gates'
// The states of a ticket that ended, as `delegate status --json` gives them.
const ENDED = ['built', 'failed', 'skipped']

const snapshotAtom = atom({ plugin: 'delegate', key: 'snapshot' } as const, null)

// A token count in a short form: `950`, `26k`, or `1.2M`, as `delegate status` shows it.
function tokens(count: number): string {
  if (count < 1000) return String(count)
  if (count < 1_000_000) return `${Math.round(count / 1000)}k`
  return `${(count / 1_000_000).toFixed(1)}M`
}

// A field that holds a number, or null. An old version of delegate can give no field, or a field of another type.
function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

// The two token counts: `302k new · 9.7M cache`, or null when the snapshot has no such counts (a version before #86).
function tokenCounts(item: { tokens_new: number | null; tokens_cache: number | null }): string | null {
  const fresh = numberOrNull(item.tokens_new)
  const cache = numberOrNull(item.tokens_cache)
  if (fresh === null || cache === null) return null
  return `${tokens(fresh)} new · ${tokens(cache)} cache`
}

// A duration in a short form: `45s`, `12m05s`, `2h03m`, or `3d04h`, as `delegate status` shows it.
function duration(seconds: number): string {
  const two = (n: number) => String(Math.floor(n)).padStart(2, '0')
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m${two(seconds % 60)}s`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h${two((seconds % 3600) / 60)}m`
  return `${Math.floor(seconds / 86400)}d${two((seconds % 86400) / 3600)}h`
}

// The ETA as a clock time in the local time of the session: `14:52`.
function clockTime(iso: string): string {
  const date = new Date(iso)
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

// The short id of a run: the part after the last `-` of `20261017T185517Z-1638`.
function shortId(runId: string): string {
  return runId.slice(runId.lastIndexOf('-') + 1)
}

// The seconds since the last activity of a running ticket: of its gate in the gates phase, else of its agent.
function idleSeconds(ticket: DelegateTicket): number | null {
  return numberOrNull(ticket.phase === GATES ? ticket.gate_idle_seconds : ticket.idle_seconds)
}

// The blockers that the `after` cell of each ticket names, from the `plan` field of the snapshot, as in the `after`
// column of `delegate watch`. The tickets are in plan order. A blocker that is not in the plan does not count. When the
// one blocker of a ticket is the ticket of the row above, the cell names no blocker. With no plan (a journal of an
// earlier version, or a version of delegate before #120), no ticket has a blocker.
function afterIds(snap: DelegateSnapshot): Map<string, string[]> {
  const blockers = new Map<string, string[]>()
  for (const entry of Array.isArray(snap.plan) ? snap.plan : []) {
    if (Array.isArray(entry?.blocked_by)) blockers.set(String(entry.id), entry.blocked_by.map(String))
  }
  const known = new Set(snap.tickets.map(ticket => ticket.id))
  const found = new Map<string, string[]>()
  snap.tickets.forEach((ticket, index) => {
    const own = (blockers.get(ticket.id) ?? []).filter(blocker => known.has(blocker))
    const joined = index > 0 && own.length === 1 && own[0] === snap.tickets[index - 1].id
    found.set(ticket.id, joined ? [] : own)
  })
  return found
}

function runningTickets(snap: DelegateSnapshot): DelegateTicket[] {
  return snap.tickets.filter(ticket => ticket.state === RUNNING)
}

// The first running ticket when its activity stopped for `limitSeconds` or more, else null.
function stalledTicket(snap: DelegateSnapshot, limitSeconds: number): DelegateTicket | null {
  const ticket = runningTickets(snap)[0]
  if (ticket === undefined) return null
  const idle = idleSeconds(ticket)
  return idle !== null && idle >= limitSeconds ? ticket : null
}

function statusText(snap: DelegateSnapshot, limitSeconds: number): string {
  const parts = [`delegate ${shortId(snap.run_id)}`, `${snap.percent}%`]
  if (snap.eta !== null) parts.push(`ETA ${clockTime(snap.eta)}`)
  const running = runningTickets(snap)
  const ticket = running[0]
  if (ticket !== undefined) {
    const more = running.length > 1 ? ` +${running.length - 1}` : ''
    parts.push(`▶ ${ticket.id}${ticket.phase ? ` ${ticket.phase}` : ''}${more}`)
    const idle = idleSeconds(ticket)
    if (idle !== null) parts.push(idle >= limitSeconds ? `⚠ idle ${duration(idle)}` : `active ${duration(idle)} ago`)
  } else if (snap.phase) {
    parts.push(`▶ ${snap.phase}`)
  }
  const counts = tokenCounts(snap)
  if (counts !== null) parts.push(counts)
  return parts.join(' · ')
}

// The snapshot in the output of the command, or null when the output is not a snapshot that has a `percent` field.
function parse(stdout: string): DelegateSnapshot | null {
  let value: unknown
  try {
    value = JSON.parse(stdout)
  } catch {
    return null
  }
  if (typeof value !== 'object' || value === null) return null
  const snap = value as Partial<DelegateSnapshot>
  if (typeof snap.percent !== 'number' || typeof snap.run_id !== 'string' || !Array.isArray(snap.tickets)) return null
  return snap as DelegateSnapshot
}

// The toasts for the change from the last snapshot to this one: one for each ticket that ended, one when the run ended.
// A snapshot of a different run, or the first snapshot of the session, gives no toast.
function toasts(last: DelegateSnapshot | null, snap: DelegateSnapshot | null): string[] {
  if (last === null || snap === null || last.run_id !== snap.run_id) return []
  const id = shortId(snap.run_id)
  const endedBefore = new Set(last.tickets.filter(ticket => ENDED.includes(ticket.state)).map(ticket => ticket.id))
  const lines = snap.tickets
    .filter(ticket => ENDED.includes(ticket.state) && !endedBefore.has(ticket.id))
    .map(ticket => `delegate ${id}: ticket ${ticket.id} ${ticket.state}`)
  if (last.state === RUNNING && snap.state !== RUNNING) lines.push(`delegate ${id}: the run ended, ${snap.state}`)
  return lines
}

// What a poll needs: the options, and what the last poll saw. The last snapshot is a module value: after a reload,
// the first poll again gives no toast.
type Poller = { command: string; limitSeconds: number; last: DelegateSnapshot | null; inFlight: Promise<void> | null }

async function fetchSnapshot($: EngineInterface, command: string): Promise<DelegateSnapshot | null> {
  try {
    const ran = await $.process.run([command, 'status', '--json'], { timeoutMs: COMMAND_TIMEOUT_MS })
    return ran.exitCode === 0 ? parse(ran.stdout) : null
  } catch {
    return null
  }
}

// One poll: run the command, show the toasts of the change, set the status line entry, and keep the snapshot for the
// band and the pane.
async function pollOnce($: EngineInterface, poller: Poller): Promise<void> {
  const snap = await fetchSnapshot($, poller.command)
  for (const line of toasts(poller.last, snap)) $.ui.toast(line)
  poller.last = snap
  const isActive = snap !== null && snap.state === RUNNING
  $.ui.status(isActive ? statusText(snap, poller.limitSeconds) : undefined)
  await update($, snapshotAtom, () => snap)
}

// A poll. While a poll runs, a new call gives that poll and starts no second command, so a caller that waits for the
// result (the `/delegate` command) sees the snapshot of the poll.
function poll($: EngineInterface, poller: Poller): Promise<void> {
  if (poller.inFlight === null) {
    poller.inFlight = pollOnce($, poller).finally(() => {
      poller.inFlight = null
    })
  }
  return poller.inFlight
}

export const register: Register = (on, options) => {
  const poller: Poller = {
    command: typeof options.command === 'string' && options.command !== '' ? options.command : 'delegate',
    limitSeconds: (typeof options.stall_minutes === 'number' ? options.stall_minutes : 5) * 60,
    last: null,
    inFlight: null,
  }
  const limitSeconds = poller.limitSeconds

  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({
      name: 'delegate',
      description: 'Show each ticket of the active delegate run in a pane',
    })
    // The first poll does not hold the start of the session: the command can take some seconds.
    void poll($, poller)
    $.clock.every(POLL_MS, () => poll($, poller))
    return started
  })

  on('command.run', { command: 'delegate' }, async $ => {
    await poll($, poller)
    const snap = poller.last
    if (snap === null || snap.state !== RUNNING) return { text: 'No delegate run is active in this directory.' }
    await $.ui.open({ id: PANE, title: `delegate ${shortId(snap.run_id)}` })
    return { text: `The pane of run ${shortId(snap.run_id)} is open.` }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const snap = await read($, snapshotAtom)
    const ticket = snap !== null && snap.state === RUNNING ? stalledTicket(snap, limitSeconds) : null
    if (snap === null || ticket === null || e.props.hasSurvey) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const idle = duration(idleSeconds(ticket) ?? 0)
    const phase = ticket.phase ? ` ${ticket.phase}` : ''
    return (
      <Box key="stall">
        <Text color="warning">
          ⚠ delegate {shortId(snap.run_id)}: no agent activity for {idle} (ticket {ticket.id}{phase})
        </Text>
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const snap = await read($, snapshotAtom)
    if (snap === null) {
      return (
        <Box>
          <Text dimColor>No delegate run is active in this directory.</Text>
        </Box>
      )
    }
    const head = [`run ${snap.run_id}`, snap.state, `${snap.percent}%`]
    if (snap.state === RUNNING && snap.eta !== null) head.push(`ETA ${clockTime(snap.eta)}`)
    const counts = tokenCounts(snap)
    if (counts !== null) head.push(counts)
    // Each row starts with the plan index of the ticket and its id. When the blocker of the ticket is not the row
    // above, the row ends with `after <id>`, one id for each blocker.
    const after = afterIds(snap)
    const plan = (ticket: DelegateTicket) => `ticket ${ticket.id}`
    const indexWidth = String(snap.tickets.length).length
    const planWidth = Math.max(...snap.tickets.map(ticket => plan(ticket).length))
    const tokensWidth = Math.max(...snap.tickets.map(ticket => (tokenCounts(ticket) ?? '').length))
    const afterCell = (ticket: DelegateTicket) => {
      const ids = after.get(ticket.id) ?? []
      return ids.length === 0 ? '' : `after ${ids.join(' ')}`
    }
    const row = (ticket: DelegateTicket, index: number) =>
      [
        String(index + 1).padStart(indexWidth),
        plan(ticket).padEnd(planWidth),
        ticket.state.padEnd(8),
        (ticket.phase ?? '-').padEnd(10),
        duration(numberOrNull(ticket.elapsed_seconds) ?? 0).padEnd(7),
        (tokenCounts(ticket) ?? '').padEnd(tokensWidth),
        afterCell(ticket),
      ].join(' ').trimEnd()
    return (
      <Box flexDirection="column">
        <Box key="run">
          <Text bold>{head.join(' · ')}</Text>
        </Box>
        {snap.tickets.map((ticket, index) => (
          <Box key={`ticket-${ticket.id}`}>
            <Text dimColor={ENDED.includes(ticket.state)}>{row(ticket, index)}</Text>
          </Box>
        ))}
      </Box>
    )
  })
}
