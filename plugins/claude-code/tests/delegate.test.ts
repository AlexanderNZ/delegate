// The tests of the delegate plugin. Each test answers the host beneath the plugin: `process.run` gives the recorded
// output of `delegate status --json` (tests/snapshot.ts), and the UI calls are recorded. The tests then assert what the
// plugin shows: the status line text, the toasts, the pane, and the band.

import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine, MockClock } from 'claude-code/testing'
import type { On, RenderElement } from 'claude-code'

import { RECORDED_RUNNING } from './snapshot'

const POLL_MS = 5000
const SURFACES = ['terminal', 'desktop'] as const

type Answer = { exitCode: number; stdout: string } | Error

// A snapshot made from the recorded one, with some fields changed.
function changed(edit: (snap: typeof RECORDED_RUNNING) => void): string {
  const snap = structuredClone(RECORDED_RUNNING)
  edit(snap)
  return JSON.stringify(snap, null, 2)
}

function ticket(snap: typeof RECORDED_RUNNING, id: string) {
  const found = snap.tickets.find(one => one.id === id)
  if (found === undefined) throw new Error(`no ticket ${id} in the snapshot`)
  return found
}

// The clock time of the ETA, as the plugin shows it: hours and minutes in the local time of the environment.
function clockTime(iso: string): string {
  const date = new Date(iso)
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

const RUNNING = JSON.stringify(RECORDED_RUNNING, null, 2)
const EXPECTED_ENTRY =
  `delegate 1638 · 84% · ETA ${clockTime(RECORDED_RUNNING.eta)} · ▶ 60 specialist · active 18s · 1.9M new · 53.0M cache`

// The host beneath the plugin. `answer` is what the next `process.run` gives; change it to change the next poll.
function host(on: On, first: Answer, clock?: MockClock, delayMs = 0) {
  const seen = {
    answer: first,
    argv: [] as string[][],
    statuses: [] as (string | undefined)[],
    toasts: [] as string[],
    opened: [] as string[],
    commands: [] as string[],
  }
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('command.register', ($, e) => {
    seen.commands.push(e.name)
    return { value: { command: e.name } }
  })
  on('process.run', async ($, e) => {
    seen.argv.push([...e.argv])
    if (clock !== undefined && delayMs > 0) await clock.sleep(delayMs)
    if (seen.answer instanceof Error) throw seen.answer
    return { value: { ...seen.answer, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('ui.status', ($, e) => {
    seen.statuses.push(e.text)
    return { value: undefined }
  })
  on('ui.toast', ($, e) => {
    seen.toasts.push(e.text)
    return { value: undefined }
  })
  // The engine draws nothing of its own where the plugin passes.
  on('ui.render', ($, e) => {
    const { Box } = $.ui.resolve(e)
    return h(Box, {}) as RenderElement
  })
  on('ui.open', ($, e) => {
    seen.opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  return seen
}

// The session starts, and the first poll, which the start does not wait for, ends.
async function start($: Engine, clock: MockClock) {
  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await clock.settle()
}

// The person types `/delegate`.
function typed($: Engine) {
  return $.command.run({
    command: 'delegate', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 160 },
  })
}

describe('the status line', () => {
  test('shows the run, the progress, the running ticket, the activity and the tokens', async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: RUNNING })

    await start($, clock)

    expect(seen.statuses.at(-1)).toBe(EXPECTED_ENTRY)
  })

  test('changes on the next poll when the snapshot changes', async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: RUNNING })
    await start($, clock)

    seen.answer = { exitCode: 0, stdout: changed(snap => {
      snap.percent = 91
      ticket(snap, '60').phase = 'verifier'
      ticket(snap, '60').idle_seconds = 3
    }) }
    await clock.advance(POLL_MS)

    expect(seen.statuses.at(-1)).toContain('91%')
    expect(seen.statuses.at(-1)).toContain('▶ 60 verifier · active 3s')
  })

  test('shows the idle time of the gate while the ticket is in its gates phase', async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: changed(snap => {
      Object.assign(ticket(snap, '60'), { phase: 'gates', idle_seconds: null, gate: 'pytest', gate_idle_seconds: 7 })
    }) })

    await start($, clock)

    expect(seen.statuses.at(-1)).toContain('▶ 60 gates · active 7s')
  })

  test('shows no token counts when the JSON has the one `tokens` field of a version before #86', async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: changed(snap => {
      const old = snap as Partial<typeof snap> & { tokens?: number }
      for (const item of [old, ...snap.tickets] as { tokens_new?: number; tokens_cache?: number; tokens?: number }[]) {
        item.tokens = 891000
        delete item.tokens_new
        delete item.tokens_cache
      }
    }) })

    await start($, clock)

    expect(seen.statuses.at(-1)).toBe(EXPECTED_ENTRY.replace(' · 1.9M new · 53.0M cache', ''))
  })

  test('runs no command other than `delegate status --json`', async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: RUNNING })
    await start($, clock)
    await clock.advance(3 * POLL_MS)
    await typed($)

    expect(seen.argv.length).toBeGreaterThanOrEqual(4)
    for (const argv of seen.argv) expect(argv).toEqual(['delegate', 'status', '--json'])
  })

  test('runs the command that the option names', { options: { command: '/opt/delegate/bin/delegate' } }, async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: RUNNING })

    await start($, clock)

    expect(seen.argv).toEqual([['/opt/delegate/bin/delegate', 'status', '--json']])
    expect(seen.statuses.at(-1)).toBe(EXPECTED_ENTRY)
  })
})

describe('with no active run', () => {
  const NO_RUN: Record<string, Answer> = {
    'the command finds no run': { exitCode: 2, stdout: '' },
    'the command cannot start': new Error('spawn delegate ENOENT'),
    'the command prints text, not JSON (an old version)': { exitCode: 0, stdout: 'run 20261017T185517Z-1638: running\n' },
    'the JSON has no percent field (an old version)': { exitCode: 0, stdout: changed(snap => {
      delete (snap as Partial<typeof snap>).percent
    }) },
    'the run has ended': { exitCode: 0, stdout: changed(snap => {
      snap.state = 'built'
    }) },
  }

  for (const [name, answer] of Object.entries(NO_RUN)) {
    test(`shows no status line entry, no toast and no pane when ${name}`, async ($, on) => {
      const clock = mock.clock(on)
      const seen = host(on, answer)
      await start($, clock)
      await clock.advance(2 * POLL_MS)

      const reply = await typed($)

      expect(seen.statuses.every(text => text === undefined)).toBe(true)
      expect(seen.toasts).toEqual([])
      expect(seen.opened).toEqual([])
      expect(reply).toEqual({ text: 'No delegate run is active in this directory.' })
    })
  }

  test('clears the status line entry when the run is no longer active', async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: RUNNING })
    await start($, clock)

    seen.answer = { exitCode: 2, stdout: '' }
    await clock.advance(POLL_MS)

    expect(seen.statuses.at(-1)).toBeUndefined()
  })
})

describe('the warning', () => {
  const STALLED = changed(snap => {
    ticket(snap, '60').idle_seconds = 400
  })

  for (const surface of SURFACES) {
    test(`shows in the status line and in the warning colour on ${surface} after the limit`, async ($, on) => {
      const clock = mock.clock(on)
      const seen = host(on, { exitCode: 0, stdout: STALLED })
      await start($, clock)

      const band = await $.ui.mount({ plugin: 'delegate', surface, component: 'AbovePrompt', props: BAND_PROPS })
      const warning = await band.find({ type: 'Text', text: /no agent activity/ })

      expect(seen.statuses.at(-1)).toContain('⚠ idle 6m40s')
      expect(await band.find({ key: 'stall' })).toBeDefined()
      expect(warning?.props.color).toBe('warning')
      expect(warning?.text).toBe('⚠ delegate 1638: no agent activity for 6m40s (ticket 60 specialist)')
    })
  }

  test('does not show before the limit of the option', { options: { stall_minutes: 10 } }, async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: STALLED })
    await start($, clock)

    const band = await $.ui.mount({ plugin: 'delegate', surface: 'terminal', component: 'AbovePrompt', props: BAND_PROPS })

    expect(seen.statuses.at(-1)).toContain('active 6m40s')
    expect(seen.statuses.at(-1)).not.toContain('⚠')
    expect(await band.find({ key: 'stall' })).toBeUndefined()
  })

  test('does not show while the agent is active', async ($, on) => {
    const clock = mock.clock(on)
    host(on, { exitCode: 0, stdout: RUNNING })
    await start($, clock)

    const band = await $.ui.mount({ plugin: 'delegate', surface: 'terminal', component: 'AbovePrompt', props: BAND_PROPS })

    expect(await band.find({ key: 'stall' })).toBeUndefined()
  })
})

describe('the toasts', () => {
  test('show nothing for the tickets that ended before the first poll', async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: RUNNING })
    await start($, clock)
    await clock.advance(2 * POLL_MS)

    expect(seen.toasts).toEqual([])
  })

  test('show when a ticket ends, and when the run ends', async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: RUNNING })
    await start($, clock)

    seen.answer = { exitCode: 0, stdout: changed(snap => {
      Object.assign(ticket(snap, '60'), { state: 'failed', phase: null, idle_seconds: null })
    }) }
    await clock.advance(POLL_MS)
    expect(seen.toasts).toEqual(['delegate 1638: ticket 60 failed'])

    seen.answer = { exitCode: 0, stdout: changed(snap => {
      Object.assign(ticket(snap, '60'), { state: 'failed', phase: null, idle_seconds: null })
      snap.state = 'failed'
    }) }
    await clock.advance(POLL_MS)
    expect(seen.toasts).toEqual(['delegate 1638: ticket 60 failed', 'delegate 1638: the run ended, failed'])

    await clock.advance(POLL_MS)
    expect(seen.toasts).toHaveLength(2)
  })

  test('show nothing for the tickets of a new run', async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: RUNNING })
    await start($, clock)

    seen.answer = { exitCode: 0, stdout: changed(snap => {
      snap.run_id = '20261018T090000Z-abcd'
    }) }
    await clock.advance(POLL_MS)

    expect(seen.toasts).toEqual([])
    expect(seen.statuses.at(-1)).toMatch(/^delegate abcd · /)
  })
})

describe('the /delegate command', () => {
  test('is registered when the session starts', async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: RUNNING })

    await start($, clock)

    expect(seen.commands).toEqual(['delegate'])
  })

  test('waits for the poll that runs when the person types it', async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: RUNNING }, clock, 3000)
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })

    const reply = typed($)
    await clock.advance(3000)

    expect(await reply).toEqual({ text: 'The pane of run 1638 is open.' })
    expect(seen.opened).toEqual(['delegate'])
  })

  for (const surface of SURFACES) {
    test(`opens a pane with one row for each ticket on ${surface}`, async ($, on) => {
      const clock = mock.clock(on)
      const seen = host(on, { exitCode: 0, stdout: RUNNING })
      await start($, clock)

      const reply = await typed($)
      const pane = await $.ui.mount({
        plugin: 'delegate', surface, component: 'Pane', requestId: 'delegate', props: PANE_PROPS,
      })
      const rows = await pane.findAll({ type: 'Text', text: / ticket / })

      expect(seen.opened).toEqual(['delegate'])
      expect(reply).toEqual({ text: 'The pane of run 1638 is open.' })
      // Each row starts with the position of the ticket in the plan. The recorded snapshot has no `plan`: no tree.
      expect(rows.map(row => row.text.match(/^ *(\d+) ticket (\S+) /)?.slice(1))).toEqual(
        RECORDED_RUNNING.tickets.map((one, index) => [String(index + 1), one.id]),
      )
      expect((await pane.find({ key: 'ticket-60' }))?.text).toMatch(
        /^12 ticket 60 +running +specialist +3m29s +76k new · 1\.0M cache$/,
      )
      expect((await pane.find({ key: 'ticket-90' }))?.text).toMatch(/^ 1 ticket 90 +built +- +57m22s +259k new · 10\.9M cache$/)
      expect((await pane.find({ key: 'run' }))?.text).toBe(
        `run 20261017T185517Z-1638 · running · 84% · ETA ${clockTime(RECORDED_RUNNING.eta)} · 1.9M new · 53.0M cache`,
      )
    })
  }

  // The `plan` field of `delegate status --json` (#120): 92 waits on 90, 89 on 92, 85 on 82 and 89, and 60 on 76 and
  // on 99, which is not in the plan.
  const BLOCKED_BY: Record<string, string[]> = { '92': ['90'], '89': ['92'], '85': ['82', '89'], '60': ['76', '99'] }
  const WITH_PLAN = changed(snap => {
    Object.assign(snap, { plan: snap.tickets.map(one => ({ id: one.id, blocked_by: BLOCKED_BY[one.id] ?? [] })) })
  })

  for (const surface of SURFACES) {
    test(`draws the tree of the blockers in the plan column of each row on ${surface}`, async ($, on) => {
      const clock = mock.clock(on)
      host(on, { exitCode: 0, stdout: WITH_PLAN })
      await start($, clock)

      await typed($)
      const pane = await $.ui.mount({
        plugin: 'delegate', surface, component: 'Pane', requestId: 'delegate', props: PANE_PROPS,
      })
      const rows = await pane.findAll({ type: 'Text', text: / ticket / })

      // A ticket sits one level under its deepest blocker in the plan, two cells for each level. The index and the
      // plan column are 2 + 1 + 16 cells: the widest plan cell is the one of 85, at depth 3.
      expect(rows.map(row => row.text.slice(0, 19).trimEnd())).toEqual([
        ' 1 ticket 90',
        ' 2 └─ ticket 92',
        ' 3   └─ ticket 89',
        ' 4 ticket 82',
        ' 5     └─ ticket 85',
        ' 6 ticket 84',
        ' 7 ticket 81',
        ' 8 ticket 86',
        ' 9 ticket 87',
        '10 ticket 80',
        '11 ticket 76',
        '12 └─ ticket 60',
      ])
      expect((await pane.find({ key: 'ticket-60' }))?.text).toBe(
        '12 └─ ticket 60     running  specialist 3m29s   76k new · 1.0M cache',
      )
      expect((await pane.find({ key: 'ticket-85' }))?.text).toBe(
        ' 5     └─ ticket 85 built    -          51m26s  238k new · 6.9M cache',
      )
      // The rows of the tickets that ended stay dim.
      expect((await pane.find({ type: 'Text', text: / ticket 85 / }))?.props.dimColor).toBe(true)
      expect((await pane.find({ type: 'Text', text: / ticket 60 / }))?.props.dimColor).toBe(false)
    })
  }

  test('draws no tree when the plan is null: the journal of an earlier version records no plan', async ($, on) => {
    const clock = mock.clock(on)
    host(on, { exitCode: 0, stdout: changed(snap => {
      Object.assign(snap, { plan: null })
    }) })
    await start($, clock)

    await typed($)
    const pane = await $.ui.mount({
      plugin: 'delegate', surface: 'terminal', component: 'Pane', requestId: 'delegate', props: PANE_PROPS,
    })
    const rows = await pane.findAll({ type: 'Text', text: / ticket / })

    expect(rows.map(row => row.text.slice(0, 12))).toEqual(
      RECORDED_RUNNING.tickets.map((one, index) => `${String(index + 1).padStart(2)} ticket ${one.id}`),
    )
  })

  test('updates the pane on the next poll', async ($, on) => {
    const clock = mock.clock(on)
    const seen = host(on, { exitCode: 0, stdout: RUNNING })
    await start($, clock)
    await typed($)
    const pane = await $.ui.mount({
      plugin: 'delegate', surface: 'terminal', component: 'Pane', requestId: 'delegate', props: PANE_PROPS,
    })

    seen.answer = { exitCode: 0, stdout: changed(snap => {
      ticket(snap, '60').phase = 'gates'
    }) }
    await clock.advance(POLL_MS)

    expect((await pane.find({ key: 'ticket-60' }))?.text).toMatch(/^12 ticket 60 +running +gates /)
  })
})

const SCROLL = { bodyRows: 20, offset: 0 }
const VIEW = {}
const BAND_PROPS = { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 120, scroll: SCROLL, view: VIEW }
const PANE_PROPS = { title: 'delegate', isFocused: false, bodyColumns: 100, placement: 'dock' as const, scroll: SCROLL, view: VIEW }
