import assert from 'node:assert/strict'
import { test } from 'node:test'
import { runDurationMs, runKindLabelKey, runStatusStyle, runningLabelKey } from './status.ts'

test('runningLabelKey maps each run kind to its own label', () => {
  assert.equal(runningLabelKey('cycle'), 'status.running')
  assert.equal(runningLabelKey('gc'), 'status.gcRunning')
  assert.equal(runningLabelKey('verify'), 'status.verifyRunning')
})

test('runningLabelKey falls back to the backup label for null/unknown', () => {
  assert.equal(runningLabelKey(null), 'status.running')
  assert.equal(runningLabelKey(undefined), 'status.running')
})

test('runKindLabelKey reads a backup cycle as a backup', () => {
  // The backup cycle is stored as "cycle"; only gc/verify get their own label.
  assert.equal(runKindLabelKey('cycle'), 'dashboard.kindBackup')
  assert.equal(runKindLabelKey('gc'), 'dashboard.kindGc')
  assert.equal(runKindLabelKey('verify'), 'dashboard.kindVerify')
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
