// The scripted runs the public demo (`npm run build:demo`) replays. Pure and side-effect free —
// devStub.ts turns the offsets below into real timestamps and API payloads, and
// demoTimeline.test.ts exercises `stateAt` directly.
//
// Offsets are seconds from the moment the run started, on the real wall clock. Under a minute
// per run: long enough to read the log scrolling, short enough that nobody walks away.
//
// The scenario is devStub's own: three routes over three PVEs and two PBSs. `nightly` fans two
// PVEs into pbs-01, `lab` backs up one, `offsite` syncs pbs-01 into pbs-02 and wakes both.
//
// Step names are the ones the backend really emits — `wait:pbs-01`, `backup:pve-alpha`, `gc`,
// `verify`, `poweroff:pbs-01`. The log text imitates PVE/PBS task output, which is English in
// production too, so nothing in this file goes through i18n.

export interface DemoEvent {
  at: number
  /** The step this line belongs to, by its full per-device name. */
  step: string
  source: 'pve' | 'pbs'
  text: string
}

export interface DemoStep {
  name: string
  from: number
  to: number
  /** Rendered beside the step in the run timeline once it finishes. */
  detail?: string
}

export interface DemoRun {
  /** cycle | sync | gc | verify — what the history's Type column shows. */
  kind: string
  steps: DemoStep[]
  events: DemoEvent[]
  /** Total length; the run is over once elapsed passes it. */
  seconds: number
  /** PBS id -> [awake, asleep) in run-seconds. Its keys are the boxes this run leases. */
  online: Record<string, [number, number]>
  guestsOk: number | null
  /** The line the activity log gets when the run lands. */
  summary: string
}

/** The handful of per-box facts the log lines quote, mirroring devStub's DEVICES fixture. */
const BOX: Record<string, { host: string; mac: string; store: string }> = {
  'pbs-01': { host: '192.168.1.50', mac: 'AA:BB:CC:DD:EE:FF', store: 'backup' },
  'pbs-02': { host: '192.168.1.51', mac: 'AA:BB:CC:DD:EE:01', store: 'offsite' },
}

// pve-alpha spans two cluster nodes (alpha-1, alpha-2), so it takes two vzdump tasks — one per
// node, which is what the real backend does and what the guest fixture is shaped to show.
const NIGHTLY: DemoRun = {
  kind: 'cycle',
  steps: [
    { name: 'wait:pbs-01', from: 0, to: 10, detail: 'woken by Wake-on-LAN' },
    { name: 'backup:pve-alpha', from: 10, to: 28, detail: '3 guests' },
    { name: 'backup:pve-beta', from: 28, to: 40, detail: '2 guests' },
    { name: 'gc', from: 40, to: 50, detail: '3.42 GiB reclaimed' },
    { name: 'poweroff:pbs-01', from: 50, to: 58 },
  ],
  events: [
    { at: 0, step: 'wait:pbs-01', source: 'pbs', text: 'Wake-on-LAN packet sent to AA:BB:CC:DD:EE:FF via eth0' },
    { at: 2, step: 'wait:pbs-01', source: 'pbs', text: 'waiting for 192.168.1.50:8007 to answer (timeout 180s)' },
    { at: 9, step: 'wait:pbs-01', source: 'pbs', text: 'pbs-01 reachable after 9s — datastore backup online' },
    { at: 11, step: 'backup:pve-alpha', source: 'pve', text: 'INFO: starting new backup job: vzdump 100 101 --node alpha-1 --storage pbs-backup --mode snapshot' },
    { at: 12, step: 'backup:pve-alpha', source: 'pve', text: 'INFO: Starting Backup of VM 100 (lxc)' },
    { at: 14, step: 'backup:pve-alpha', source: 'pve', text: 'INFO: creating Proxmox Backup Server archive for CT 100 (nextcloud-production-primary)' },
    { at: 17, step: 'backup:pve-alpha', source: 'pve', text: 'INFO: Finished Backup of VM 100 (00:00:05, 4.21 GiB)' },
    { at: 18, step: 'backup:pve-alpha', source: 'pve', text: 'INFO: Starting Backup of VM 101 (qemu)' },
    { at: 22, step: 'backup:pve-alpha', source: 'pve', text: 'INFO: Finished Backup of VM 101 (00:00:04, 8.04 GiB)' },
    { at: 23, step: 'backup:pve-alpha', source: 'pve', text: 'INFO: starting new backup job: vzdump 102 --node alpha-2 --storage pbs-backup --mode snapshot' },
    { at: 26, step: 'backup:pve-alpha', source: 'pve', text: 'INFO: Finished Backup of VM 102 (00:00:02, 1.88 GiB)' },
    { at: 27, step: 'backup:pve-alpha', source: 'pve', text: 'INFO: Backup job finished successfully' },
    { at: 29, step: 'backup:pve-beta', source: 'pve', text: 'INFO: starting new backup job: vzdump 200 201 --node beta --storage pbs-backup --mode snapshot' },
    { at: 30, step: 'backup:pve-beta', source: 'pve', text: 'INFO: Starting Backup of VM 200 (qemu)' },
    { at: 34, step: 'backup:pve-beta', source: 'pve', text: 'INFO: Finished Backup of VM 200 (00:00:04, 12.80 GiB)' },
    { at: 35, step: 'backup:pve-beta', source: 'pve', text: 'INFO: Starting Backup of VM 201 (lxc)' },
    { at: 37, step: 'backup:pve-beta', source: 'pve', text: 'INFO: Finished Backup of VM 201 (00:00:02, 2.16 GiB)' },
    { at: 38, step: 'backup:pve-beta', source: 'pbs', text: 'prune group "vm/200": keep-daily 7, keep-weekly 4, keep-monthly 6 — removed 1 snapshot' },
    { at: 39, step: 'backup:pve-beta', source: 'pve', text: 'INFO: Backup job finished successfully' },
    { at: 41, step: 'gc', source: 'pbs', text: 'starting garbage collection on store backup' },
    { at: 44, step: 'gc', source: 'pbs', text: 'processed 41% (8934 chunks)' },
    { at: 47, step: 'gc', source: 'pbs', text: 'processed 100% (21877 chunks)' },
    { at: 49, step: 'gc', source: 'pbs', text: 'removed 214 chunks, 3.42 GiB reclaimed' },
    { at: 51, step: 'poweroff:pbs-01', source: 'pbs', text: 'poweroff issued over SSH as root@192.168.1.50' },
    { at: 56, step: 'poweroff:pbs-01', source: 'pbs', text: 'pbs-01 is down — notification sent' },
  ],
  seconds: 58,
  online: { 'pbs-01': [9, 56] },
  guestsOk: 5,
  summary: 'backup finished: 5 guests, 29.1 GiB',
}

// ponytail: `lab` is scripted as the second half of the nightly -> lab handover, which is the
// only way the demo ever reaches it on its own, so its wake step says the box is already up.
// A visitor who runs it manually from idle sees that claim about a box that was asleep; give it
// its own wake arc if manual runs ever become the point of the demo.
const LAB: DemoRun = {
  kind: 'cycle',
  // No `gc` step: the fixture route sets options.gc = false.
  steps: [
    { name: 'wait:pbs-01', from: 0, to: 3, detail: 'already awake' },
    { name: 'backup:pve-lab', from: 3, to: 30, detail: '3 guests' },
    { name: 'poweroff:pbs-01', from: 30, to: 38 },
  ],
  events: [
    { at: 0, step: 'wait:pbs-01', source: 'pbs', text: 'pbs-01 is already awake — no Wake-on-LAN packet sent' },
    { at: 4, step: 'backup:pve-lab', source: 'pve', text: 'INFO: starting new backup job: vzdump 130 131 132 --node lab --storage pbs-backup --mode snapshot' },
    { at: 5, step: 'backup:pve-lab', source: 'pve', text: 'INFO: Starting Backup of VM 130 (qemu)' },
    { at: 12, step: 'backup:pve-lab', source: 'pve', text: 'INFO: Finished Backup of VM 130 (00:00:07, 18.40 GiB)' },
    { at: 13, step: 'backup:pve-lab', source: 'pve', text: 'INFO: Starting Backup of VM 131 (qemu)' },
    { at: 20, step: 'backup:pve-lab', source: 'pve', text: 'INFO: Finished Backup of VM 131 (00:00:07, 17.95 GiB)' },
    { at: 21, step: 'backup:pve-lab', source: 'pve', text: 'INFO: Starting Backup of VM 132 (lxc)' },
    { at: 25, step: 'backup:pve-lab', source: 'pve', text: 'INFO: Finished Backup of VM 132 (00:00:04, 0.94 GiB)' },
    { at: 27, step: 'backup:pve-lab', source: 'pbs', text: 'prune group "vm/130": keep-daily 3, keep-weekly 2 — removed 2 snapshots' },
    { at: 29, step: 'backup:pve-lab', source: 'pve', text: 'INFO: Backup job finished successfully' },
    { at: 31, step: 'poweroff:pbs-01', source: 'pbs', text: 'poweroff issued over SSH as root@192.168.1.50' },
    { at: 36, step: 'poweroff:pbs-01', source: 'pbs', text: 'pbs-01 is down — notification sent' },
  ],
  seconds: 38,
  online: { 'pbs-01': [0, 36] },
  guestsOk: 3,
  summary: 'backup finished: 3 guests, 37.3 GiB',
}

// A sync route leases BOTH boxes: the target wakes first because a `pull` runs on it.
const OFFSITE: DemoRun = {
  kind: 'sync',
  steps: [
    { name: 'wait:pbs-02', from: 0, to: 12, detail: 'woken by Wake-on-LAN' },
    { name: 'wait:pbs-01', from: 12, to: 20, detail: 'woken by Wake-on-LAN' },
    { name: 'sync', from: 20, to: 40, detail: '3 groups, 18.89 GiB' },
    { name: 'verify', from: 40, to: 48, detail: '12 snapshots, 0 failed' },
    { name: 'poweroff:pbs-01', from: 48, to: 54 },
    { name: 'poweroff:pbs-02', from: 54, to: 60 },
  ],
  events: [
    { at: 0, step: 'wait:pbs-02', source: 'pbs', text: 'Wake-on-LAN packet sent to AA:BB:CC:DD:EE:01 via eth0' },
    { at: 2, step: 'wait:pbs-02', source: 'pbs', text: 'waiting for 192.168.1.51:8007 to answer (timeout 180s)' },
    { at: 11, step: 'wait:pbs-02', source: 'pbs', text: 'pbs-02 reachable after 11s — datastore offsite online' },
    { at: 13, step: 'wait:pbs-01', source: 'pbs', text: 'Wake-on-LAN packet sent to AA:BB:CC:DD:EE:FF via eth0' },
    { at: 19, step: 'wait:pbs-01', source: 'pbs', text: 'pbs-01 reachable after 7s — datastore backup online' },
    { at: 21, step: 'sync', source: 'pbs', text: 'remote "joulenap-offsite" points at 192.168.1.50:8007' },
    { at: 22, step: 'sync', source: 'pbs', text: 'sync job joulenap-offsite: pulling into datastore offsite' },
    { at: 26, step: 'sync', source: 'pbs', text: 'sync group ct/100 done — 1 snapshot, 4.21 GiB' },
    { at: 31, step: 'sync', source: 'pbs', text: 'sync group vm/200 done — 1 snapshot, 12.80 GiB' },
    { at: 36, step: 'sync', source: 'pbs', text: 'sync group ct/102 done — 1 snapshot, 1.88 GiB' },
    { at: 39, step: 'sync', source: 'pbs', text: 'sync job joulenap-offsite finished: 3 groups, 18.89 GiB transferred' },
    { at: 41, step: 'verify', source: 'pbs', text: 'verify datastore offsite: 12 snapshots to check' },
    { at: 44, step: 'verify', source: 'pbs', text: 'verify group ct/100 — OK' },
    { at: 47, step: 'verify', source: 'pbs', text: 'verify finished: 12 OK, 0 failed' },
    { at: 49, step: 'poweroff:pbs-01', source: 'pbs', text: 'poweroff issued over SSH as root@192.168.1.50' },
    { at: 53, step: 'poweroff:pbs-01', source: 'pbs', text: 'pbs-01 is down' },
    { at: 55, step: 'poweroff:pbs-02', source: 'pbs', text: 'poweroff issued over SSH as root@192.168.1.51' },
    { at: 59, step: 'poweroff:pbs-02', source: 'pbs', text: 'pbs-02 is down — notification sent' },
  ],
  seconds: 60,
  online: { 'pbs-02': [11, 58], 'pbs-01': [19, 52] },
  guestsOk: null,
  summary: 'sync finished: 3 groups, 18.9 GiB',
}

/** Ad-hoc GC / verify on one box — the homepage's per-PBS buttons. Same three beats either way. */
export function maintenanceRun(pbs: string, action: 'gc' | 'verify'): DemoRun {
  const box = BOX[pbs] ?? BOX['pbs-01']
  const work: DemoEvent[] =
    action === 'gc'
      ? [
          { at: 10, step: 'gc', source: 'pbs', text: `starting garbage collection on store ${box.store}` },
          { at: 15, step: 'gc', source: 'pbs', text: 'processed 57% (12408 chunks)' },
          { at: 22, step: 'gc', source: 'pbs', text: 'removed 1174 chunks, 18.62 GiB reclaimed' },
        ]
      : [
          { at: 10, step: 'verify', source: 'pbs', text: `verify datastore ${box.store}: 26 snapshots to check` },
          { at: 16, step: 'verify', source: 'pbs', text: 'verify group vm/200 — OK' },
          { at: 22, step: 'verify', source: 'pbs', text: 'verify finished: 26 OK, 0 failed' },
        ]
  return {
    kind: action,
    steps: [
      { name: `wait:${pbs}`, from: 0, to: 9, detail: 'woken by Wake-on-LAN' },
      { name: action, from: 9, to: 24 },
      { name: `poweroff:${pbs}`, from: 24, to: 31 },
    ],
    events: [
      { at: 0, step: `wait:${pbs}`, source: 'pbs', text: `Wake-on-LAN packet sent to ${box.mac} via eth0` },
      { at: 8, step: `wait:${pbs}`, source: 'pbs', text: `${pbs} reachable after 8s — datastore ${box.store} online` },
      ...work,
      { at: 25, step: `poweroff:${pbs}`, source: 'pbs', text: `poweroff issued over SSH as root@${box.host}` },
      { at: 29, step: `poweroff:${pbs}`, source: 'pbs', text: `${pbs} is down — notification sent` },
    ],
    seconds: 31,
    online: { [pbs]: [8, 29] },
    guestsOk: null,
    summary:
      action === 'gc'
        ? `garbage collection finished on ${box.store}: 18.62 GiB reclaimed`
        : `verify finished on ${box.store}: 26 OK, 0 failed`,
  }
}

const BY_ROUTE: Record<string, DemoRun> = { nightly: NIGHTLY, lab: LAB, offsite: OFFSITE }

/** The arc for a route id. A route the visitor just created has no script, so it replays
 *  the nightly one rather than leaving the Run now button doing nothing. */
export function runFor(routeId: string): DemoRun {
  return BY_ROUTE[routeId] ?? NIGHTLY
}

export interface DemoState {
  running: boolean
  /** The boxes answering right now. */
  online: string[]
  /** Only the steps that have started — the real backend creates a step row when it begins. */
  steps: { name: string; status: string; from: number; to: number | null; detail: string | null }[]
  events: DemoEvent[]
}

/** Where the scripted run stands `elapsed` seconds in. */
export function stateAt(run: DemoRun, elapsed: number): DemoState {
  return {
    running: elapsed < run.seconds,
    online: Object.entries(run.online)
      .filter(([, [up, down]]) => elapsed >= up && elapsed < down)
      .map(([id]) => id),
    steps: run.steps
      .filter((s) => elapsed >= s.from)
      .map((s) => ({
        name: s.name,
        status: elapsed >= s.to ? 'success' : 'running',
        from: s.from,
        to: elapsed >= s.to ? s.to : null,
        detail: elapsed >= s.to ? (s.detail ?? null) : null,
      })),
    events: run.events.filter((e) => e.at <= elapsed),
  }
}
