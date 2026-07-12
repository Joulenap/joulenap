import assert from 'node:assert/strict'
import { test } from 'node:test'
import { runningLabelKey } from './status.ts'

test('runningLabelKey maps each run kind to its own label', () => {
  assert.equal(runningLabelKey('cycle'), 'status.running')
  assert.equal(runningLabelKey('gc'), 'status.gcRunning')
  assert.equal(runningLabelKey('verify'), 'status.verifyRunning')
})

test('runningLabelKey falls back to the backup label for null/unknown', () => {
  assert.equal(runningLabelKey(null), 'status.running')
  assert.equal(runningLabelKey(undefined), 'status.running')
})
