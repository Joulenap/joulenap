import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { DemoRun } from './demoTimeline.ts'
import { maintenanceRun, runFor, stateAt } from './demoTimeline.ts'

const ARCS: [string, DemoRun][] = [
  ['nightly', runFor('nightly')],
  ['lab', runFor('lab')],
  ['offsite', runFor('offsite')],
  ['gc:pbs-02', maintenanceRun('pbs-02', 'gc')],
  ['verify:pbs-01', maintenanceRun('pbs-01', 'verify')],
]

test('every arc is a well-formed script', () => {
  for (const [name, run] of ARCS) {
    const names = new Set(run.steps.map((s) => s.name))
    assert.equal(run.steps[0].from, 0, `${name}: the first step starts at 0`)
    assert.equal(run.steps.at(-1)!.to, run.seconds, `${name}: the last step ends at seconds`)
    run.steps.forEach((s, i) => {
      assert.ok(s.to > s.from, `${name}: step ${s.name} ends after it starts`)
      if (i > 0) assert.equal(s.from, run.steps[i - 1].to, `${name}: no gap before ${s.name}`)
    })
    for (const e of run.events) {
      assert.ok(e.at >= 0 && e.at <= run.seconds, `${name}: event at ${e.at} is inside the run`)
      assert.ok(names.has(e.step), `${name}: event names a real step (${e.step})`)
    }
    for (const [pbs, [up, down]] of Object.entries(run.online)) {
      assert.ok(up < down, `${name}: ${pbs} comes up before it goes down`)
      assert.ok(up >= 0 && down <= run.seconds, `${name}: ${pbs}'s window is inside the run`)
    }
  }
})

test('at t=0 only the first step exists, it is running, and every box is asleep', () => {
  for (const [name, run] of ARCS) {
    const s = stateAt(run, 0)
    assert.equal(s.running, true, `${name}: running`)
    assert.equal(s.steps.length, 1, `${name}: exactly one step has started`)
    assert.equal(s.steps[0].status, 'running', `${name}: it is running`)
    assert.equal(s.steps[0].to, null, `${name}: with no finish time yet`)
    // `lab` is scripted as a handover, so its box is already up at t=0.
    if (name !== 'lab') assert.deepEqual(s.online, [], `${name}: no box is online at t=0`)
  }
})

test('a step is absent until it starts', () => {
  for (const [name, run] of ARCS) {
    for (const step of run.steps) {
      const before = stateAt(run, Math.max(0, step.from - 1)).steps.map((x) => x.name)
      if (step.from > 0) assert.ok(!before.includes(step.name), `${name}: ${step.name} not early`)
      assert.ok(stateAt(run, step.from).steps.some((x) => x.name === step.name), `${name}: starts`)
    }
  }
})

test('the log is a growing prefix — lines never disappear or reorder', () => {
  for (const [name, run] of ARCS) {
    let prev = stateAt(run, 0).events
    for (let t = 1; t <= run.seconds; t++) {
      const now = stateAt(run, t).events
      assert.ok(now.length >= prev.length, `${name}: line count shrank at t=${t}`)
      assert.deepEqual(now.slice(0, prev.length), prev, `${name}: earlier lines changed at t=${t}`)
      prev = now
    }
    assert.equal(prev.length, run.events.length, `${name}: every line is emitted by the end`)
  }
})

test('past the end the run is over, every step succeeded and every box is off', () => {
  for (const [name, run] of ARCS) {
    const s = stateAt(run, run.seconds + 10)
    assert.equal(s.running, false, `${name}: finished`)
    assert.deepEqual(s.online, [], `${name}: nothing left online`)
    assert.equal(s.steps.length, run.steps.length, `${name}: every step is present`)
    assert.ok(
      s.steps.every((x) => x.status === 'success' && x.to !== null),
      `${name}: every step succeeded with a finish time`,
    )
  }
})

test('a finished step carries its detail, a running one does not', () => {
  const nightly = runFor('nightly')
  const wait = nightly.steps[0]
  assert.equal(stateAt(nightly, wait.to - 1).steps[0].detail, null)
  assert.equal(stateAt(nightly, wait.to).steps[0].detail, wait.detail)
})

test('steps are named after the devices the run touches', () => {
  const nightly = runFor('nightly')
  const names = nightly.steps.map((s) => s.name)
  assert.ok(names.includes('backup:pve-alpha') && names.includes('backup:pve-beta'), 'fan-in')
  assert.ok(names.includes('wait:pbs-01') && names.includes('poweroff:pbs-01'))
})

test('the lab route has no gc step — its fixture route disables GC', () => {
  assert.equal(
    runFor('lab').steps.some((s) => s.name === 'gc'),
    false,
  )
})

test('the sync route leases both boxes and releases them one at a time', () => {
  const offsite = runFor('offsite')
  assert.equal(offsite.kind, 'sync')
  assert.deepEqual(Object.keys(offsite.online).sort(), ['pbs-01', 'pbs-02'])

  const sync = offsite.steps.find((s) => s.name === 'sync')!
  const mid = stateAt(offsite, (sync.from + sync.to) / 2)
  assert.deepEqual([...mid.online].sort(), ['pbs-01', 'pbs-02'], 'both boxes are up to sync')

  const off1 = offsite.steps.find((s) => s.name === 'poweroff:pbs-01')!
  assert.deepEqual(stateAt(offsite, off1.to).online, ['pbs-02'], 'the source goes down first')
  assert.deepEqual(stateAt(offsite, 0).online, [], 'and nothing is up before the wake')
})

test('an ad-hoc maintenance run names every step after the box it was given', () => {
  const run = maintenanceRun('pbs-02', 'gc')
  assert.deepEqual(
    run.steps.map((s) => s.name),
    ['wait:pbs-02', 'gc', 'poweroff:pbs-02'],
  )
  assert.deepEqual(Object.keys(run.online), ['pbs-02'])
  assert.equal(run.guestsOk, null, 'no guests are backed up by a GC')
  assert.ok(run.events.every((e) => e.source === 'pbs'))
  assert.equal(maintenanceRun('pbs-01', 'verify').steps[1].name, 'verify')
})

test('backup arcs report a guest count, the others report none', () => {
  assert.equal(runFor('nightly').guestsOk, 5, 'three guests on alpha plus two on beta')
  assert.equal(runFor('lab').guestsOk, 3)
  assert.equal(runFor('offsite').guestsOk, null)
  for (const [, run] of ARCS) assert.ok(run.summary.length > 0, 'every arc has a summary line')
})

test('an unknown route id still gets an arc to replay', () => {
  const created = runFor('a-route-the-visitor-just-made')
  assert.ok(created.steps.length > 0)
  assert.equal(created.kind, 'cycle')
})
