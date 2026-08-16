import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Route, RouteSchedule } from '../api/types.ts'
import {
  routeDevices,
  routeKindBadge,
  routeLinks,
  routePbss,
  routeSourceIds,
  routeUsesPve,
  scheduleSummary,
} from './routes.ts'

const route = (over: Partial<Route>): Route => ({
  id: 'r',
  name: 'R',
  color: '#e8830f',
  enabled: true,
  notify: true,
  kind: 'backup',
  sources: [],
  source_pbs: '',
  target: 'pbs-01',
  schedule: { time: '02:00', days: [true, true, true, true, true, true, true], cron: '' },
  retention: { keep_last: 0, keep_daily: 7, keep_weekly: 4, keep_monthly: 6, keep_yearly: 0 },
  sync_direction: 'pull',
  options: {
    mode: 'snapshot',
    bwlimit: 0,
    min_free_percent: 0,
    gc: true,
    verify_after: false,
    reverify_days: 30,
    transfer_last: 0,
    remove_vanished: false,
  },
  ...over,
})

const src = (pve: string) => ({ pve, guests: { mode: 'all' as const, list: [] } })

test('a multi-source backup route draws one wire per PVE into the shared target', () => {
  const r = route({ sources: [src('pve-alpha'), src('pve-beta')], target: 'pbs-01' })
  assert.deepEqual(routeLinks(r), [
    ['pve:pve-alpha', 'pbs:pbs-01'],
    ['pve:pve-beta', 'pbs:pbs-01'],
  ])
})

test('a sync route draws one PBS-to-PBS wire from source_pbs, not from sources', () => {
  const r = route({ kind: 'sync', source_pbs: 'pbs-01', target: 'pbs-02' })
  assert.deepEqual(routeLinks(r), [['pbs:pbs-01', 'pbs:pbs-02']])
})

test('external and verify routes draw no wire — they only wake their target', () => {
  assert.deepEqual(routeLinks(route({ kind: 'external', sources: [] })), [])
  assert.deepEqual(routeLinks(route({ kind: 'verify', sources: [] })), [])
  // ...but the target is still theirs, so highlighting them lights that card up.
  assert.deepEqual(routeDevices(route({ kind: 'verify', target: 'pbs-02' })), ['pbs:pbs-02'])
})

test('a half-built route (no source picked yet) draws nothing rather than a dangling wire', () => {
  assert.deepEqual(routeLinks(route({ kind: 'sync', source_pbs: '' })), [])
  assert.deepEqual(routeLinks(route({ kind: 'backup', sources: [] })), [])
})

test('routeDevices lists the target plus every source, deduplicated', () => {
  const r = route({ sources: [src('pve-alpha'), src('pve-beta')] })
  assert.deepEqual(routeDevices(r).sort(), ['pbs:pbs-01', 'pve:pve-alpha', 'pve:pve-beta'])
})

test('routeUsesPve only matches backup sources', () => {
  const backup = route({ sources: [src('pve-alpha')] })
  assert.equal(routeUsesPve(backup, 'pve-alpha'), true)
  assert.equal(routeUsesPve(backup, 'pve-beta'), false)
  // A sync route has no PVE at all, even though it has a source.
  assert.equal(routeUsesPve(route({ kind: 'sync', source_pbs: 'pbs-01' }), 'pbs-01'), false)
})

test('routePbss names both boxes a sync route wakes', () => {
  assert.deepEqual(routePbss(route({ kind: 'sync', source_pbs: 'pbs-01', target: 'pbs-02' })), [
    'pbs-02',
    'pbs-01',
  ])
  assert.deepEqual(routePbss(route({ sources: [src('pve-alpha')] })), ['pbs-01'])
})

test('routeSourceIds gives the chips left of the arrow', () => {
  assert.deepEqual(routeSourceIds(route({ sources: [src('a'), src('b')] })), ['a', 'b'])
  assert.deepEqual(routeSourceIds(route({ kind: 'sync', source_pbs: 'pbs-01' })), ['pbs-01'])
})

test('each kind gets its own badge class', () => {
  assert.equal(routeKindBadge('backup').cls, '')
  assert.equal(routeKindBadge('sync').cls, 'sync')
  assert.equal(routeKindBadge('external').cls, 'ext')
  assert.equal(routeKindBadge('verify').cls, 'verify')
})

const sched = (over: Partial<RouteSchedule>): RouteSchedule => ({
  time: '04:00',
  days: [false, false, false, false, false, true, false],
  cron: '',
  ...over,
})

test('all seven days reads as "every day", not as a list of seven', () => {
  const s = scheduleSummary(sched({ days: Array(7).fill(true) }))
  assert.equal(s.daysKey, 'dashboard.everyDay')
  assert.equal(s.time, '04:00')
})

test('a partial week lists its days in Mon..Sun order', () => {
  const s = scheduleSummary(sched({ days: [false, false, false, false, false, true, true] }))
  assert.equal(s.daysKey, 'dashboard.onDays')
  assert.deepEqual(s.days, ['sat', 'sun'])
})

test('a cron-pinned route shows the cron and suppresses time/days', () => {
  // The backend ignores time/days entirely when cron is set, so rendering them would
  // claim a firing time the scheduler never uses.
  const s = scheduleSummary(sched({ cron: '0 23 * * 0,1,2,4,5', time: '04:00' }))
  assert.equal(s.cron, '0 23 * * 0,1,2,4,5')
  assert.equal(s.time, '')
  assert.equal(s.daysKey, 'dashboard.scheduleCron')
})
