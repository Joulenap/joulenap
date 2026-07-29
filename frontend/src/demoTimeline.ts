// The scripted backup cycle the public demo (`npm run build:demo`) replays when the visitor
// presses "Run now". Pure and side-effect free — devStub.ts turns the offsets below into
// real timestamps and API payloads, and demoTimeline.test.ts exercises `stateAt` directly.
//
// Offsets are seconds from the moment the run started, on the real wall clock. ~55s for a
// full cycle: long enough to read the log scrolling, short enough that nobody walks away.

export interface DemoEvent {
  at: number
  step: string
  source: 'pve' | 'pbs'
  text: string
}

export interface DemoStep {
  name: string
  from: number
  to: number
}

export interface DemoRun {
  steps: DemoStep[]
  events: DemoEvent[]
  /** Total length; the run is over once elapsed passes it. */
  seconds: number
  /** Window in which the PBS answers — it is asleep before, and powered off after. */
  online: [number, number]
  guestsOk: number
}

// The two guests config.backup.guests.list selects in the demo fixture (100, 102).
const CYCLE: DemoRun = {
  steps: [
    { name: 'wake', from: 0, to: 2 },
    { name: 'wait', from: 2, to: 11 },
    { name: 'backup', from: 11, to: 36 },
    { name: 'gc', from: 36, to: 45 },
    { name: 'poweroff', from: 45, to: 54 },
  ],
  events: [
    { at: 0, step: 'wake', source: 'pbs', text: 'Wake-on-LAN packet sent to AA:BB:CC:DD:EE:FF via eth0' },
    { at: 3, step: 'wait', source: 'pbs', text: 'Waiting for 192.168.1.50:8007 to answer (timeout 180s)' },
    { at: 10, step: 'wait', source: 'pbs', text: 'PBS reachable after 9s — datastore backup-main online' },
    { at: 12, step: 'backup', source: 'pve', text: 'INFO: starting new backup job: vzdump 100 102 --storage pbs-backup --mode snapshot' },
    { at: 14, step: 'backup', source: 'pve', text: 'INFO: Starting Backup of VM 100 (lxc)' },
    { at: 16, step: 'backup', source: 'pve', text: 'INFO: creating vzdump archive (nextcloud-production-primary)' },
    { at: 23, step: 'backup', source: 'pve', text: 'INFO: Finished Backup of VM 100 (00:00:09, 4.21 GiB)' },
    { at: 25, step: 'backup', source: 'pve', text: 'INFO: Starting Backup of VM 102 (lxc)' },
    { at: 31, step: 'backup', source: 'pve', text: 'INFO: Finished Backup of VM 102 (00:00:06, 1.88 GiB)' },
    { at: 33, step: 'backup', source: 'pbs', text: 'prune group "ct/102": keep-daily 7, keep-weekly 4, keep-monthly 6 — removed 1 snapshot' },
    { at: 35, step: 'backup', source: 'pve', text: 'INFO: Backup job finished successfully' },
    { at: 37, step: 'gc', source: 'pbs', text: 'starting garbage collection on store backup-main' },
    { at: 41, step: 'gc', source: 'pbs', text: 'processed 68% (14203 chunks)' },
    { at: 44, step: 'gc', source: 'pbs', text: 'removed 214 chunks, 3.42 GiB reclaimed' },
    { at: 46, step: 'poweroff', source: 'pbs', text: 'poweroff issued over SSH as root@192.168.1.50' },
    { at: 52, step: 'poweroff', source: 'pbs', text: 'PBS is down — cycle complete, notification sent' },
  ],
  seconds: 54,
  online: [10, 52],
  guestsOk: 2,
}

const GC: DemoRun = {
  steps: [
    { name: 'wake', from: 0, to: 2 },
    { name: 'wait', from: 2, to: 11 },
    { name: 'gc', from: 11, to: 22 },
    { name: 'poweroff', from: 22, to: 31 },
  ],
  events: [
    { at: 0, step: 'wake', source: 'pbs', text: 'Wake-on-LAN packet sent to AA:BB:CC:DD:EE:FF via eth0' },
    { at: 3, step: 'wait', source: 'pbs', text: 'Waiting for 192.168.1.50:8007 to answer (timeout 180s)' },
    { at: 10, step: 'wait', source: 'pbs', text: 'PBS reachable after 9s — datastore backup-main online' },
    { at: 12, step: 'gc', source: 'pbs', text: 'starting garbage collection on store backup-main' },
    { at: 16, step: 'gc', source: 'pbs', text: 'processed 71% (14891 chunks)' },
    { at: 21, step: 'gc', source: 'pbs', text: 'removed 214 chunks, 3.42 GiB reclaimed' },
    { at: 23, step: 'poweroff', source: 'pbs', text: 'poweroff issued over SSH as root@192.168.1.50' },
    { at: 29, step: 'poweroff', source: 'pbs', text: 'PBS is down — notification sent' },
  ],
  seconds: 31,
  online: [10, 29],
  guestsOk: 0,
}

export function runFor(kind: string): DemoRun {
  return kind === 'gc' ? GC : CYCLE
}

export interface DemoState {
  running: boolean
  pbsOnline: boolean
  /** Only the steps that have started — the real backend creates a step row when it begins. */
  steps: { name: string; status: string; from: number; to: number | null }[]
  events: DemoEvent[]
}

/** Where the scripted run stands `elapsed` seconds in. */
export function stateAt(run: DemoRun, elapsed: number): DemoState {
  const running = elapsed < run.seconds
  return {
    running,
    pbsOnline: elapsed >= run.online[0] && elapsed < run.online[1],
    steps: run.steps
      .filter((s) => elapsed >= s.from)
      .map((s) => ({
        name: s.name,
        status: elapsed >= s.to ? 'success' : 'running',
        from: s.from,
        to: elapsed >= s.to ? s.to : null,
      })),
    events: run.events.filter((e) => e.at <= elapsed),
  }
}
