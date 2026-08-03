import assert from 'node:assert/strict'
import { test } from 'node:test'
import { crossColumnMx, fanSpread, sameColumnMx, wirePath } from './topology.ts'

test('a lone wire lands dead centre on its target', () => {
  assert.equal(fanSpread(1, 1), 0)
})

test('two wires into one PBS split symmetrically around the centre', () => {
  assert.equal(fanSpread(1, 2), -7)
  assert.equal(fanSpread(2, 2), 7)
})

test('an odd fan-in keeps its middle wire centred', () => {
  assert.deepEqual([fanSpread(1, 3), fanSpread(2, 3), fanSpread(3, 3)], [-14, 0, 14])
})

test('the spread always sums to zero, so the bundle stays centred on the card', () => {
  for (const n of [1, 2, 3, 4, 5]) {
    let sum = 0
    for (let i = 1; i <= n; i++) sum += fanSpread(i, n)
    assert.equal(sum, 0, `n=${n}`)
  }
})

test('a cross-column wire bends on the midpoint between the two cards', () => {
  const s = { x: 100, y: 50 }
  const t = { x: 300, y: 90 }
  assert.equal(crossColumnMx(s, t), 200)
  assert.equal(wirePath(s, t, crossColumnMx(s, t)), 'M100 50 C200 50 200 90 300 90')
})

test('a same-column wire bows out past both cards instead of cutting through them', () => {
  // Both ends anchored on the right edge of the PBS column (a sync route). A midpoint
  // control would put the curve straight over the cards between them.
  const s = { x: 300, y: 40 }
  const t = { x: 300, y: 160 }
  const mx = sameColumnMx(s, t)
  assert.equal(mx, 362)
  assert.ok(mx > s.x && mx > t.x)
  assert.equal(wirePath(s, t, mx), 'M300 40 C362 40 362 160 300 160')
})
