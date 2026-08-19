import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Route, RouteSchedule } from '../api/types.ts'
import { nextOccurrences, upcomingRows } from './upcoming.ts'

const sched = (over: Partial<RouteSchedule>): RouteSchedule => ({
  time: '02:00',
  days: Array(7).fill(true),
  cron: '',
  ...over,
})

const route = (id: string, over: Partial<Route> = {}): Route => ({
  id,
  name: id,
  color: '#e8830f',
  enabled: true,
  notify: true,
  kind: 'backup',
  sources: [],
  source_pbs: '',
  target: 'pbs-01',
  schedule: sched({}),
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

// --- nextOccurrences ---------------------------------------------------------

// Local time throughout: schedule.time is wall-clock, so the tests build local dates too.
const local = (y: number, mo: number, d: number, h = 0, mi = 0) => new Date(y, mo - 1, d, h, mi)

test('a daily schedule yields consecutive days at its time', () => {
  const out = nextOccurrences(sched({ time: '02:00' }), local(2026, 8, 3, 2, 0), 3)
  assert.deepEqual(
    out.map((d) => `${d.getDate()} ${d.getHours()}:${d.getMinutes()}`),
    ['4 2:0', '5 2:0', '6 2:0'],
  )
})

test('the anchor firing itself is excluded — the caller already has that row', () => {
  // 2026-08-03 is a Monday. Asking from exactly 02:00 must not hand back 02:00 again.
  const out = nextOccurrences(sched({ time: '02:00' }), local(2026, 8, 3, 2, 0), 1)
  assert.equal(out[0].getDate(), 4)
})

test('a Saturdays-only schedule skips a whole week between firings', () => {
  const satOnly = sched({ time: '04:00', days: [false, false, false, false, false, true, false] })
  const out = nextOccurrences(satOnly, local(2026, 8, 8, 4, 0), 2) // Sat 8 Aug 2026
  assert.deepEqual(
    out.map((d) => d.getDate()),
    [15, 22],
  )
})

test('a cron-pinned route contributes nothing — the backend owns its firing times', () => {
  assert.deepEqual(nextOccurrences(sched({ cron: '0 23 * * 1' }), local(2026, 8, 3), 3), [])
})

test('a malformed time yields nothing instead of Invalid Date rows', () => {
  assert.deepEqual(nextOccurrences(sched({ time: 'nope' }), local(2026, 8, 3), 3), [])
})

test('half a malformed time is still malformed', () => {
  // Either field alone being unparseable has to stop it: an hour with a junk minute would
  // otherwise render an Invalid Date row on the dashboard.
  assert.deepEqual(nextOccurrences(sched({ time: '04:xx' }), local(2026, 8, 3), 3), [])
  assert.deepEqual(nextOccurrences(sched({ time: 'xx:30' }), local(2026, 8, 3), 3), [])
})

test('asking for no occurrences returns none, and asking for one returns one', () => {
  // `count <= 0` is the guard: with a strict `<`, a zero-length panel would still scan
  // seven days per requested row and hand back a firing nobody asked for.
  assert.deepEqual(nextOccurrences(sched({}), local(2026, 8, 3), 0), [])
  assert.deepEqual(nextOccurrences(sched({}), local(2026, 8, 3), -1), [])
  assert.equal(nextOccurrences(sched({}), local(2026, 8, 3), 1).length, 1)
})

test('a schedule with every day switched off never fires', () => {
  assert.deepEqual(
    nextOccurrences(sched({ days: Array(7).fill(false) }), local(2026, 8, 3), 3),
    [],
  )
})

// --- upcomingRows ------------------------------------------------------------

const nightly = route('nightly', { color: '#e8830f' })
const lab = route('lab', {
  color: '#3b82f6',
  schedule: sched({ time: '04:00', days: [false, false, false, false, false, true, false] }),
})

const NEXT = [
  { route_id: 'lab', route_name: 'Lab', at: local(2026, 8, 8, 4, 0).toISOString() },
  { route_id: 'nightly', route_name: 'Nightly', at: local(2026, 8, 4, 2, 0).toISOString() },
]

test('the running row comes first and carries no timestamp', () => {
  const rows = upcomingRows({
    running: { routeId: 'nightly', routeName: 'Nightly' },
    nextRuns: NEXT,
    routes: [nightly, lab],
    capacity: 3,
  })
  assert.equal(rows[0].now, true)
  assert.equal(rows[0].at, null)
  assert.equal(rows[0].color, '#e8830f')
})

test('every armed route keeps its row even when the panel is too short', () => {
  // capacity 1 would fit one row; dropping a route would read as "not scheduled".
  const rows = upcomingRows({
    running: { routeId: 'nightly', routeName: 'Nightly' },
    nextRuns: NEXT,
    routes: [nightly, lab],
    capacity: 1,
  })
  assert.equal(rows.length, 3)
  assert.deepEqual(rows.map((r) => r.routeId), ['nightly', 'lab', 'nightly'])
})

test('spare capacity is filled with later occurrences, in chronological order', () => {
  const rows = upcomingRows({
    running: null,
    nextRuns: NEXT,
    routes: [nightly, lab],
    capacity: 5,
  })
  assert.equal(rows.length, 5)
  // The two guaranteed rows keep next_runs' own order, then the pool is sorted by time.
  const extra = rows.slice(2).map((r) => r.at as string)
  assert.deepEqual([...extra].sort(), extra)
  // Nightly is daily and Lab is weekly, so the filler is all Nightly here.
  assert.deepEqual(rows.slice(2).map((r) => r.routeId), ['nightly', 'nightly', 'nightly'])
})

test('a filler row never duplicates a route\'s guaranteed row', () => {
  const rows = upcomingRows({ running: null, nextRuns: NEXT, routes: [nightly, lab], capacity: 6 })
  assert.equal(new Set(rows.map((r) => r.key)).size, rows.length)
})

test('an unknown route id still renders, with a neutral dot', () => {
  // A route deleted between two polls: status.next_runs can still name it for one tick.
  const rows = upcomingRows({
    running: null,
    nextRuns: [{ route_id: 'gone', route_name: 'Gone', at: local(2026, 8, 4, 2, 0).toISOString() }],
    routes: [],
    capacity: 4,
  })
  assert.equal(rows.length, 1)
  assert.equal(rows[0].color, 'var(--jn-text-muted)')
})

test('no routes and nothing running gives an empty list, not a fabricated row', () => {
  assert.deepEqual(upcomingRows({ running: null, nextRuns: [], routes: [], capacity: 8 }), [])
})
