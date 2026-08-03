import type { RunSummary, StatusResponse } from '../api/types'

/**
 * i18n key for a run kind used as a label ("GC running").
 *
 * `running.kind` only exists once a RUNNING row does, so an unknown or absent kind —
 * including the brief gap between the queue taking the lock and the run row being created —
 * falls back to the generic "Backup running" label.
 */
export function runningLabelKey(kind: string | null | undefined): string {
  switch (kind) {
    case 'gc':
      return 'status.gcRunning'
    case 'verify':
      return 'status.verifyRunning'
    case 'monitor':
      return 'status.monitorRunning'
    case 'sync':
      return 'status.syncRunning'
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
    case 'monitor':
      return 'dashboard.kindMonitor'
    case 'sync':
      return 'dashboard.kindSync'
    default:
      return 'dashboard.kindBackup'
  }
}

export type PillTone = 'blue' | 'amber' | 'neutral'

export interface HeaderPill {
  labelKey: string
  tone: PillTone
  busy: boolean
  /** Interpolated as `{{route}}` when the running run belongs to one. */
  route?: string
  /** Raw ISO of the next fire, interpolated as `{{when}}`. Formatting is the caller's job:
   *  this module stays free of the locale-dependent formatters so it can be unit-tested. */
  nextAt?: string
}

/**
 * The header pill: what *Joulenap* is doing, not what a PBS is.
 *
 * With N backup servers "on/off" is no longer a single fact, so the three states are
 * "running a route", "the kill-switch is off" and "idle, next run at <when>". Per-device
 * state lives in the topology instead.
 *
 * `null` (first poll, or the backend gone) reads as a bare "Idle": the stale banner is what
 * tells the user something is wrong, and claiming "paused" would be a lie about the config.
 * A run with no route is an ad-hoc PBS GC/verify, which has a name only as its kind.
 */
export function headerPill(status: StatusResponse | null): HeaderPill {
  const running = status?.running
  if (status?.state === 'running') {
    const route = running?.route_name || running?.route_id
    return route
      ? { labelKey: 'status.runningRoute', tone: 'blue', busy: true, route }
      : { labelKey: runningLabelKey(running?.kind), tone: 'blue', busy: true }
  }
  if (status?.state === 'paused') return { labelKey: 'status.paused', tone: 'amber', busy: false }
  // next_runs arrives soonest-first, so [0] is the next thing that will happen.
  const next = status?.next_runs?.[0]
  if (next) return { labelKey: 'status.idleNext', tone: 'neutral', busy: false, nextAt: next.at }
  return { labelKey: 'status.idle', tone: 'neutral', busy: false }
}

export interface RunStatusStyle {
  labelKey: string
  color: string
  bg: string
}

// Palette vars are inlined rather than imported from theme.ts so this module stays free of
// React/CSS imports and can be unit-tested under `node --test`. Must track theme.ts tokens.
const RUN_STATUS: Record<string, RunStatusStyle> = {
  success: { labelKey: 'dashboard.runSuccess', color: 'var(--jn-green)', bg: 'rgba(63,178,127,.14)' },
  failure: { labelKey: 'dashboard.runFailure', color: 'var(--jn-red)', bg: 'rgba(229,103,91,.14)' },
  aborted: { labelKey: 'dashboard.runAborted', color: 'var(--jn-amber)', bg: 'rgba(224,169,43,.14)' },
  running: { labelKey: 'dashboard.runRunning', color: 'var(--jn-blue)', bg: 'rgba(59,130,246,.14)' },
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
