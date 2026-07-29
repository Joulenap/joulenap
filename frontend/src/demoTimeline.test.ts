import assert from 'node:assert/strict'
import { test } from 'node:test'
import { runFor, stateAt } from './demoTimeline.ts'

const cycle = runFor('cycle')

test('at t=0 only the first step exists, and it is running', () => {
  const s = stateAt(cycle, 0)
  assert.equal(s.running, true)
  assert.deepEqual(
    s.steps.map((x) => [x.name, x.status]),
    [['wake', 'running']],
  )
  assert.equal(s.pbsOnline, false, 'PBS is still asleep at t=0')
})

test('mid-run the current step is backup and earlier steps are done', () => {
  const s = stateAt(cycle, 20)
  assert.equal(s.running, true)
  assert.equal(s.pbsOnline, true)
  const byName = new Map(s.steps.map((x) => [x.name, x.status]))
  assert.equal(byName.get('wake'), 'success')
  assert.equal(byName.get('wait'), 'success')
  assert.equal(byName.get('backup'), 'running')
  assert.equal(byName.has('gc'), false, 'gc has not started yet')
})

test('the log is a growing prefix — lines never disappear or reorder', () => {
  let prev = stateAt(cycle, 0).events
  for (let t = 1; t <= cycle.seconds; t++) {
    const now = stateAt(cycle, t).events
    assert.ok(now.length >= prev.length, `line count shrank at t=${t}`)
    assert.deepEqual(now.slice(0, prev.length), prev, `earlier lines changed at t=${t}`)
    prev = now
  }
  assert.equal(prev.length, cycle.events.length, 'every line is emitted by the end')
})

test('past the end the run is over, every step succeeded and the PBS is off', () => {
  const s = stateAt(cycle, cycle.seconds + 10)
  assert.equal(s.running, false)
  assert.equal(s.pbsOnline, false)
  assert.equal(s.steps.length, cycle.steps.length)
  assert.ok(s.steps.every((x) => x.status === 'success' && x.to !== null))
})

test('the gc run skips the backup step', () => {
  const gc = runFor('gc')
  assert.equal(
    gc.steps.some((s) => s.name === 'backup'),
    false,
  )
  assert.ok(gc.seconds < cycle.seconds)
})
