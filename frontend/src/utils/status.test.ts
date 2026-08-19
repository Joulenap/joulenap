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

const lastRun = (over: Partial<StatusResponse['last_run'] & object>) => ({
  id: 41,
  kind: 'cycle',
  trigger: 'schedule',
  status: 'success',
  started_at: '2026-08-08T00:00:00Z',
  finished_at: '2026-08-08T00:30:00Z',
  route_id: 'nightly',
  route_name: 'Nightly',
  guests_ok: 5,
  error: null,
  ...over,
})

test('headerPill goes red when the last finished run failed, naming route and time', () => {
  const p = headerPill(status({ last_run: lastRun({ status: 'failure' }) }))
  assert.deepEqual(p, {
    labelKey: 'status.lastRunFailed',
    tone: 'red',
    busy: false,
    route: 'Nightly',
    failedAt: '2026-08-08T00:30:00Z',
  })
})

test('headerPill red state falls back to the start time and to the routeless key', () => {
  // The app died mid-run: finished_at never landed, started_at is the only timestamp.
  const crashed = headerPill(
    status({ last_run: lastRun({ status: 'failure', finished_at: null }) }),
  )
  assert.equal(crashed.failedAt, '2026-08-08T00:00:00Z')
  // An ad-hoc GC has no route to name.
  const adhoc = headerPill(
    status({ last_run: lastRun({ status: 'failure', route_id: null, route_name: null }) }),
  )
  assert.equal(adhoc.labelKey, 'status.lastRunFailedRun')
  assert.equal(adhoc.route, undefined)
})

test('headerPill goes green when the last run succeeded', () => {
  const p = headerPill(
    status({
      last_run: lastRun({}),
      next_runs: [{ route_id: 'lab', route_name: 'Lab', at: '2026-08-09T04:00:00Z' }],
    }),
  )
  assert.deepEqual(p, {
    labelKey: 'status.okNext',
    tone: 'green',
    busy: false,
    nextAt: '2026-08-09T04:00:00Z',
  })
  assert.deepEqual(headerPill(status({ last_run: lastRun({}) })), {
    labelKey: 'status.ok',
    tone: 'green',
    busy: false,
  })
})

test('headerPill treats an aborted run as plain idle — a deliberate stop is not a failure', () => {
  assert.equal(
    headerPill(status({ last_run: lastRun({ status: 'aborted' }) })).labelKey,
    'status.idle',
  )
})

test('headerPill lets running and paused win over a failed last run', () => {
  const failed = lastRun({ status: 'failure' })
  assert.equal(
    headerPill(
      status({
        state: 'running',
        last_run: failed,
        running: {
          run_id: 9,
          kind: 'cycle',
          started_at: '2026-08-08T02:00:00Z',
          route_id: 'lab',
          route_name: 'Lab',
        },
      }),
    ).tone,
    'blue',
  )
  assert.equal(headerPill(status({ state: 'paused', last_run: failed })).tone, 'amber')
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

test('the routeless failed pill is shaped like its named twin', () => {
  // The named branch is deep-equalled above; this one was only checked for its label, so
  // `busy` was free to drift and spin the header on a run that is long over.
  const adhoc = headerPill(
    status({ last_run: lastRun({ status: 'failure', route_id: null, route_name: null }) }),
  )
  assert.deepEqual(adhoc, {
    labelKey: 'status.lastRunFailedRun',
    tone: 'red',
    busy: false,
    failedAt: '2026-08-08T00:30:00Z',
  })
})
