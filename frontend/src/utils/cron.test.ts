import assert from 'node:assert/strict'
import { test } from 'node:test'
import { buildCron, isAdvancedSchedule, parseCron } from './cron.ts'

test('round-trips a daily schedule', () => {
  const s = parseCron('30 4 * * *')
  assert.equal(s.time, '04:30')
  assert.deepEqual(s.days, [true, true, true, true, true, true, true])
  assert.equal(buildCron(s), '30 4 * * *')
})

test('round-trips a weekday subset (Mon,Wed,Fri)', () => {
  // cron dow: Mon=1, Wed=3, Fri=5
  const s = parseCron('0 2 * * 1,3,5')
  assert.deepEqual(s.days, [true, false, true, false, true, false, false])
  assert.equal(buildCron(s), '0 2 * * 1,3,5')
})

test('0 and 7 both mean Sunday (our index 6)', () => {
  // The whole array, not just index 6: checking one slot cannot tell "Sunday" from
  // "Sunday and whatever the array happened to start with".
  const sunday = [false, false, false, false, false, false, true]
  assert.deepEqual(parseCron('0 2 * * 0').days, sunday)
  assert.deepEqual(parseCron('0 2 * * 7').days, sunday)
})

test('a single mid-week day selects that day and nothing else', () => {
  // cron dow 2 = Tuesday = our index 1.
  assert.deepEqual(parseCron('0 2 * * 2').days, [
    false, true, false, false, false, false, false,
  ])
  assert.equal(buildCron(parseCron('0 2 * * 2')), '0 2 * * 2')
})

test('an unparseable day list falls back to every day rather than to none', () => {
  // A cron nobody can read must not produce a schedule that never fires; daily is the
  // safe reading, and it is what the UI then shows the user.
  assert.deepEqual(parseCron('0 2 * * xyz').days, Array(7).fill(true))
})

test('preserves day-of-month/month through the round-trip (JN-006)', () => {
  // Monthly: 1st of the month. Must NOT collapse to daily.
  const s = parseCron('0 4 1 * *')
  assert.equal(s.dom, '1')
  assert.equal(s.month, '*')
  assert.equal(buildCron(s), '0 4 1 * *')
  assert.equal(isAdvancedSchedule(s), true)
})

test('a normal weekly schedule is not advanced', () => {
  assert.equal(isAdvancedSchedule(parseCron('0 2 * * 1,3,5')), false)
})

test('short/garbage input falls back to daily 02:00 with * dom/month', () => {
  const s = parseCron('')
  assert.equal(s.time, '02:00')
  assert.deepEqual(s.days, [true, true, true, true, true, true, true])
  assert.equal(s.dom, '*')
  assert.equal(s.month, '*')
})

test('all-days build emits * for dom/month/dow', () => {
  assert.equal(buildCron({ time: '05:00', days: Array(7).fill(true), dom: '*', month: '*' }), '0 5 * * *')
})
