import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { RunDetail, RunSummary, TaskLogLine } from '../api/types.ts'
import { runAsText } from './runExport.ts'

const RUN: RunSummary = {
  id: 42,
  kind: 'sync',
  trigger: 'scheduled',
  status: 'failure',
  started_at: '2026-08-27T02:00:00Z',
  finished_at: '2026-08-27T02:06:22Z',
  route_id: 'b2-sync',
  route_name: 'B2-Sync',
  guests_ok: null,
  error: 'GET /nodes/localhost/tasks/UPID:pbs:0000026D/status failed: the read operation timed out',
}

const DETAIL: RunDetail = {
  ...RUN,
  steps: [
    { name: 'wait', status: 'ok', started_at: '2026-08-27T02:00:00Z', finished_at: '2026-08-27T02:00:01Z', detail: 'already awake' },
    { name: 'sync', status: 'failure', started_at: '2026-08-27T02:00:01Z', finished_at: null, detail: null },
  ],
  logs: [
    { id: 1, run_id: 42, ts: '2026-08-27T02:00:00Z', level: 'info', message: 'sync: started' },
    { id: 2, run_id: 42, ts: '2026-08-27T02:03:00Z', level: 'warn', message: 'lost contact with PBS' },
  ],
}

const LINES: TaskLogLine[] = [
  { id: 1, step: 'sync', source: 'pbs', text: 'INFO: Starting datastore sync job   ', ts: '2026-08-27T02:00:02Z' },
]

test('a run comes out as one pasteable block, every section in it', () => {
  const text = runAsText(RUN, DETAIL, LINES)

  assert.match(text, /^Joulenap run #42$/m)
  assert.match(text, /route: {4}B2-Sync/)
  assert.match(text, /status: {3}failure/)
  // The two things a maintainer actually reads: the failing step and the error behind it.
  assert.match(text, /^ {2}sync {2}failure/m)
  assert.match(text, /^error: GET \/nodes\/localhost/m)
  assert.match(text, /^ {2}2026-08-27T02:03:00Z {2}warn {2}lost contact with PBS$/m)
  assert.match(text, /^INFO: Starting datastore sync job {3}$/m) // verbatim, trailing spaces kept
})

test('a step with no detail does not leave a ragged tail of spaces', () => {
  const text = runAsText(RUN, DETAIL, [])

  assert.ok(!text.split('\n').some((l) => l !== l.trimEnd() && l.startsWith('  ')))
})

test('an expanded row that has not loaded its detail yet still copies', () => {
  // The button is on screen from the moment the row opens, so it has to survive a null detail
  // and an empty log rather than throwing where the user can see it.
  const text = runAsText({ ...RUN, error: null, route_name: null }, null, [])

  assert.match(text, /route: {4}\(none\)/)
  assert.ok(!text.includes('steps:'))
  assert.ok(!text.includes('error:'))
  assert.ok(!text.includes('task log:'))
})

test('a run still in flight says so instead of printing null', () => {
  const text = runAsText({ ...RUN, finished_at: null, status: 'running' }, null, [])

  assert.match(text, /finished: \(still running\)/)
})
