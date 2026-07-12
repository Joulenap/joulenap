import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { TaskLogLine } from '../api/types.ts'
import { appendTaskLines } from './taskLog.ts'

const ln = (id: number): TaskLogLine => ({ id, ts: `t${id}`, text: `line ${id}` }) as TaskLogLine

test('appends genuinely new lines', () => {
  const prev = [ln(1), ln(2)]
  assert.deepEqual(
    appendTaskLines(prev, [ln(3), ln(4)]).map((l) => l.id),
    [1, 2, 3, 4],
  )
})

test('a re-delivered window (overlapping poll) adds no duplicates (FE-M6)', () => {
  const prev = [ln(1), ln(2), ln(3)]
  // Second overlapping poll returns the same window that was already appended.
  assert.deepEqual(
    appendTaskLines(prev, [ln(1), ln(2), ln(3)]).map((l) => l.id),
    [1, 2, 3],
  )
})

test('a partially-overlapping window keeps only the new tail', () => {
  const prev = [ln(1), ln(2), ln(3)]
  assert.deepEqual(
    appendTaskLines(prev, [ln(2), ln(3), ln(4), ln(5)]).map((l) => l.id),
    [1, 2, 3, 4, 5],
  )
})

test('returns the same array reference when there is nothing new (no re-render)', () => {
  const prev = [ln(1), ln(2)]
  assert.equal(appendTaskLines(prev, []), prev)
  assert.equal(appendTaskLines(prev, [ln(1), ln(2)]), prev)
})
