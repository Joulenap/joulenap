import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { StatusResponse } from '../api/types.ts'
import {
  headerPill,
  runDurationMs,
  runKindLabelKey,
  runStatusStyle,
  runningLabelKey,
} from './status.ts'

test('runningLabelKey maps each run kind to its own label', () => {
  assert.equal(runningLabelKey('cycle'), 'status.running')
  assert.equal(runningLabelKey('gc'), 'status.gcRunning')
  assert.equal(runningLabelKey('verify'), 'status.verifyRunning')
  assert.equal(runningLabelKey('sync'), 'status.syncRunning')
})

test('runningLabelKey falls back to the backup label for null/unknown', () => {
  assert.equal(runningLabelKey(null), 'status.running')
  assert.equal(runningLabelKey(undefined), 'status.running')
})

const status = (over: Partial<StatusResponse>): StatusResponse => ({
  state: 'idle',
  scheduler_enabled: true,
  running: null,
  queued: [],
  next_runs: [],
  pves: [],
  pbss: [],
  last_run: null,
  config_error: null,
  ...over,
})

test('headerPill names the running route and spins', () => {
  const p = headerPill(
    status({
      state: 'running',
      running: {
        run_id: 7,
        kind: 'cycle',
        started_at: '2026-08-03T02:00:00Z',
        route_id: 'nightly',
        route_name: 'Nightly',
      },
    }),
  )
  assert.deepEqual(p, { labelKey: 'status.runningRoute', tone: 'blue', busy: true, route: 'Nightly' })
})

test('headerPill falls back to the route id, then to the kind, when there is no name', () => {
  const running = { run_id: 7, kind: 'gc', started_at: '2026-08-03T02:00:00Z' }
  assert.equal(
    headerPill(status({ state: 'running', running: { ...running, route_id: 'lab', route_name: '' } }))
      .route,
    'lab',
  )
  // An ad-hoc PBS GC belongs to no route at all: it is named by its kind instead.
  const adhoc = headerPill(
    status({ state: 'running', running: { ...running, route_id: null, route_name: null } }),
  )
  assert.deepEqual(adhoc, { labelKey: 'status.gcRunning', tone: 'blue', busy: true })
})

test('headerPill reports the kill-switch, and running wins over it', () => {
  assert.deepEqual(headerPill(status({ state: 'paused', scheduler_enabled: false })), {
    labelKey: 'status.paused',
    tone: 'amber',
    busy: false,
  })
})

test('headerPill shows the soonest next run when idle, and nothing to show without one', () => {
  const p = headerPill(
    status({
      next_runs: [
        { route_id: 'lab', route_name: 'Lab', at: '2026-08-08T04:00:00Z' },
        { route_id: 'offsite', route_name: 'Offsite', at: '2026-08-09T05:00:00Z' },
      ],
    }),
  )
  // Raw ISO, not a formatted string: formatting is locale- and timezone-dependent.
  assert.deepEqual(p, {
    labelKey: 'status.idleNext',
    tone: 'neutral',
    busy: false,
    nextAt: '2026-08-08T04:00:00Z',
  })
  assert.equal(headerPill(status({})).labelKey, 'status.idle')
})

test('headerPill reads a missing status as idle, never as paused', () => {
  // First poll, or the backend gone: the stale banner says so — the pill must not claim the
  // scheduler is off, which is a statement about the config.
  assert.deepEqual(headerPill(null), { labelKey: 'status.idle', tone: 'neutral', busy: false })
})

test('runKindLabelKey reads a backup cycle as a backup', () => {
  // The backup cycle is stored as "cycle"; only gc/verify get their own label.
  assert.equal(runKindLabelKey('cycle'), 'dashboard.kindBackup')
  assert.equal(runKindLabelKey('gc'), 'dashboard.kindGc')
  assert.equal(runKindLabelKey('verify'), 'dashboard.kindVerify')
  assert.equal(runKindLabelKey('sync'), 'dashboard.kindSync')
  assert.equal(runKindLabelKey('something-new'), 'dashboard.kindBackup')
})

test('runStatusStyle gives each outcome its own colour, unknown reads as running', () => {
  assert.equal(runStatusStyle('success').labelKey, 'dashboard.runSuccess')
  assert.equal(runStatusStyle('failure').labelKey, 'dashboard.runFailure')
  assert.equal(runStatusStyle('aborted').labelKey, 'dashboard.runAborted')
  assert.notEqual(runStatusStyle('success').color, runStatusStyle('failure').color)
  assert.equal(runStatusStyle('nonsense').labelKey, 'dashboard.runRunning')
})

test('runDurationMs measures a finished run and elapsed time for one still going', () => {
  const started = '2026-06-28T04:00:00Z'
  assert.equal(
    runDurationMs({ started_at: started, finished_at: '2026-06-28T04:01:23Z' }),
    83_000,
  )
  // Unfinished: measured against the injected clock, not wall time.
  assert.equal(
    runDurationMs({ started_at: started, finished_at: null }, Date.parse('2026-06-28T04:00:30Z')),
    30_000,
  )
})

test('runDurationMs returns null on an unparseable timestamp and never goes negative', () => {
  assert.equal(runDurationMs({ started_at: 'not-a-date', finished_at: null }), null)
  // Clock skew (finished before started) must not render as a negative duration.
  assert.equal(
    runDurationMs({ started_at: '2026-06-28T04:01:00Z', finished_at: '2026-06-28T04:00:00Z' }),
    0,
  )
})
