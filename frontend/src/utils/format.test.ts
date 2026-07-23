import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fmtBytesTB, fmtDuration, fmtUptime, rel } from './format.ts'

test('rel: sub-minute vs rounds-to-a-minute', () => {
  assert.equal(rel(0), '<1m')
  assert.equal(rel(20_000), '<1m') // round(0.33) = 0
  assert.equal(rel(30_000), '1m')  // round(0.5) = 1
})

test('rel: minutes and hours', () => {
  assert.equal(rel(5 * 60_000), '5m')
  assert.equal(rel(90 * 60_000), '1h 30m')
  assert.equal(rel(2 * 60 * 60_000), '2h')
})

test('rel: rolls multi-day deltas over to days (JN-016 regression)', () => {
  // 120 hours must read "5d", never "120h".
  assert.equal(rel(120 * 60 * 60_000), '5d')
  assert.equal(rel((5 * 24 + 3) * 60 * 60_000), '5d 3h')
})

test('fmtBytesTB: TB at/above 1e12, GB below', () => {
  assert.equal(fmtBytesTB(2e12), '2.00 TB')
  assert.equal(fmtBytesTB(1e12), '1.00 TB')
  assert.equal(fmtBytesTB(5e11), '500.00 GB')
})

test('fmtDuration: keeps seconds below an hour (rel() would say "1m" for 41s)', () => {
  assert.equal(fmtDuration(2_000), '2s')
  assert.equal(fmtDuration(41_000), '41s')
  assert.equal(rel(41_000), '1m') // why fmtDuration exists
  assert.equal(fmtDuration(83_000), '1m 23s')
  assert.equal(fmtDuration(600_000), '10m 00s')
  assert.equal(fmtDuration(7_500_000), '2h 05m')
  assert.equal(fmtDuration(-1), '0s')
})

test('fmtUptime: compact d/h/m with rollovers', () => {
  assert.equal(fmtUptime(90), '1m')
  assert.equal(fmtUptime(3660), '1h 1m')
  assert.equal(fmtUptime(90_000), '1d 1h')
  assert.equal(fmtUptime(-5), '0m')
})
