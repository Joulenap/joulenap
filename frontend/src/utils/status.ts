import type { RunSummary, StatusResponse } from '../api/types'

/**
 * i18n key for the header pill label while a run is in flight.
 *
 * The backend only sends `running_kind` once a RUNNING row exists, so an unknown
 * or absent kind (including the brief gap between the lock being taken and the run
 * row being created) falls back to the generic "Backup running" label.
 */
export function runningLabelKey(kind: StatusResponse['running_kind']): string {
  switch (kind) {
    case 'gc':
      return 'status.gcRunning'
    case 'verify':
      return 'status.verifyRunning'
    default:
      return 'status.running'
  }
}

/**
 * i18n key for a run's kind in the history table.
 *
 * A backup cycle is stored as `cycle` (RunKind.BACKUP is never used as a run kind), so
 * anything unrecognised — including a kind added by a newer backend — reads as a backup
 * rather than rendering a raw enum value.
 */
export function runKindLabelKey(kind: string): string {
  switch (kind) {
    case 'gc':
      return 'dashboard.kindGc'
    case 'verify':
      return 'dashboard.kindVerify'
    default:
      return 'dashboard.kindBackup'
  }
}

export interface RunStatusStyle {
  labelKey: string
  color: string
  bg: string
}

// Palette hexes are inlined rather than imported from theme.ts so this module stays free of
// React/CSS imports and can be unit-tested under `node --test`.
const RUN_STATUS: Record<string, RunStatusStyle> = {
  success: { labelKey: 'dashboard.runSuccess', color: '#3fb27f', bg: 'rgba(63,178,127,.14)' },
  failure: { labelKey: 'dashboard.runFailure', color: '#e5675b', bg: 'rgba(229,103,91,.14)' },
  aborted: { labelKey: 'dashboard.runAborted', color: '#e0a92b', bg: 'rgba(224,169,43,.14)' },
  running: { labelKey: 'dashboard.runRunning', color: '#3b82f6', bg: 'rgba(59,130,246,.14)' },
}

/** Badge styling + label key for a run or step status; unknown values read as running. */
export function runStatusStyle(status: string): RunStatusStyle {
  return RUN_STATUS[status] ?? RUN_STATUS.running
}

/**
 * How long a run took, in ms — or how long it has been going if it hasn't finished.
 *
 * `now` is injected so the caller (and the tests) control the clock. Returns null when the
 * result would be meaningless, so the caller renders a dash instead of "0m".
 */
export function runDurationMs(
  run: Pick<RunSummary, 'started_at' | 'finished_at'>,
  now: number = Date.now(),
): number | null {
  const started = Date.parse(run.started_at)
  if (Number.isNaN(started)) return null
  const end = run.finished_at ? Date.parse(run.finished_at) : now
  if (Number.isNaN(end)) return null
  return Math.max(0, end - started)
}
