import type { StatusResponse } from '../api/types'

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
