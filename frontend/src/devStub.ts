// Dev/preview only — see the guard in main.tsx for who is allowed to load this.
//
// Two jobs:
//   1. answer every `/api/*` fetch from fixtures, so the SPA renders with no backend;
//   2. pin the clock, so repeated screenshots of the same layout are byte-identical.
//
// The fixtures reproduce the homepage mockup's scenario exactly — three PVEs, two PBSs, three
// routes (Nightly running, Lab, Offsite sync) — so M09+ can build the homepage against them.
//
// Under `VITE_DEMO=1` (the public demo at joulenap.com/demo, `npm run build:demo`) the same
// fixtures gain a scripted replay on top: the clock ticks, the seeded Nightly run plays out of
// `demoTimeline.ts`, the queued Lab route starts by itself when it lands, and Run now / Stop /
// Run GC drive the same machinery. Dev mode (`--mode stub`) gets none of that — every mutation
// below sits behind `DEMO` so a stub screenshot stays byte-identical run to run.
import type { DemoRun, DemoState } from './demoTimeline'
import { maintenanceRun, runFor, stateAt } from './demoTimeline'
import type {
  AuthStatus,
  Config,
  DashboardResponse,
  DeviceLists,
  GuestInfo,
  LogLine,
  NetInterface,
  PbsDerive,
  PveConnectResult,
  Route,
  RunDetail,
  RunSummary,
  StatusResponse,
  StepInfo,
  TaskLogLine,
  TaskLogResponse,
  UserInfo,
} from './api/types'

const DEMO = import.meta.env.VITE_DEMO === '1'

const FIXED_MS = Date.UTC(2026, 6, 9, 21, 30, 0)
const WEEK_MS = 7 * 24 * 60 * 60 * 1000

const RealDate = Date
const BOOT = RealDate.now()
// The demo slides the whole fixture calendar forward by a whole number of WEEKS, so a page
// hosted for a year never reads "last backed up in July 2026". Whole weeks (rather than the
// 0.9 stub's whole days) is what makes it free: every weekday and time-of-day survives the
// shift untouched, so `days[]`, the next-run list and the history stay consistent with each
// other and nothing has to re-derive a schedule.
// ponytail: a DST boundary between the fixture date and today moves the wall-clock hour by
// one. Only cosmetic here; a real fix means shifting in the display timezone.
const DELTA = DEMO ? Math.floor((BOOT - FIXED_MS) / WEEK_MS) * WEEK_MS : 0

// Dev pins the clock for byte-identical screenshots; the demo needs it to move so the scripted
// run can advance. Same helper, one term apart.
const nowMs = () => (DEMO ? FIXED_MS + DELTA + (RealDate.now() - BOOT) : FIXED_MS)

class FrozenDate extends RealDate {
  constructor(...args: ConstructorParameters<typeof Date>) {
    if ((args as unknown[]).length === 0) super(nowMs())
    else super(...args)
  }
  static now() {
    return nowMs()
  }
}
globalThis.Date = FrozenDate as unknown as DateConstructor

const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/

/** Rewrite every ISO timestamp in a fixture tree, in place, so new fixtures are covered too. */
function shiftDates(node: unknown, seen = new WeakSet<object>()): void {
  if (typeof node !== 'object' || node === null || seen.has(node)) return
  seen.add(node)
  for (const [k, v] of Object.entries(node)) {
    if (typeof v === 'string' && ISO_RE.test(v)) {
      ;(node as Record<string, unknown>)[k] = new RealDate(RealDate.parse(v) + DELTA).toISOString()
    } else shiftDates(v, seen)
  }
}

// Fixture values are chosen to exercise the layout hard: a long guest name, a long log
// message, an ERROR level, a cluster next to two standalone nodes, one sleeping PBS.

const AUTH_STATUS: AuthStatus = { setup_needed: false, authenticated: true, username: 'admin' }
const ME: UserInfo = { username: 'admin' }

// The route dot colours the mockup uses, as the literal hexes Route.color stores
// (--jn-accent / --jn-blue / --jn-amber of the dark palette in index.css).
const ACCENT = '#e8830f'
const BLUE = '#3b82f6'
const AMBER = '#e0a92b'

const RETENTION = { keep_last: 0, keep_daily: 7, keep_weekly: 4, keep_monthly: 6, keep_yearly: 0 }
const OPTIONS = {
  mode: 'snapshot' as const,
  bwlimit: 0,
  min_free_percent: 10,
  gc: true,
  verify_after: false,
  reverify_days: 30,
  transfer_last: 0,
  remove_vanished: false,
}
const EVERY_DAY = [true, true, true, true, true, true, true]
const SATURDAYS = [false, false, false, false, false, true, false]
const SUNDAYS = [false, false, false, false, false, false, true]

const DEVICES: DeviceLists = {
  pves: [
    {
      id: 'pve-alpha',
      host: '192.168.1.10',
      port: 8006,
      verify_tls: false,
      api_token_id: 'root@pam!joulenap',
      api_token_secret: '***REDACTED***',
      storages: { 'pbs-01': 'pbs-backup' },
    },
    {
      id: 'pve-beta',
      host: '192.168.1.11',
      port: 8006,
      verify_tls: false,
      api_token_id: 'root@pam!joulenap',
      api_token_secret: '***REDACTED***',
      storages: { 'pbs-01': 'pbs-backup' },
    },
    {
      id: 'pve-lab',
      host: '192.168.1.12',
      port: 8006,
      verify_tls: false,
      api_token_id: 'root@pam!joulenap',
      api_token_secret: '***REDACTED***',
      storages: { 'pbs-01': 'pbs-backup' },
    },
    {
      // Deliberately used by no route: without it every Remove button in the mockup's
      // scenario hits the guard-rail and the happy path is unreachable against the stub.
      id: 'pve-spare',
      host: '192.168.1.13',
      port: 8006,
      verify_tls: true,
      api_token_id: 'joulenap@pve!routes',
      api_token_secret: '***REDACTED***',
      storages: {},
    },
  ],
  pbss: [
    {
      id: 'pbs-01',
      host: '192.168.1.50',
      port: 8007,
      datastore: 'backup',
      fingerprint: 'aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99',
      api_token_id: 'joulenap@pbs!token',
      api_token_secret: '***REDACTED***',
      managed_power: true,
      mac: 'AA:BB:CC:DD:EE:FF',
      wol_broadcast_iface: 'eth0',
      wait_timeout: 180,
      wol_retries: 3,
      poweroff_task_wait: 600,
      ssh_user: 'root',
      ssh_key_path: '/app/data/id_ed25519',
      external: { first_task_wait: 900, idle_wait: 300 },
    },
    {
      id: 'pbs-02',
      host: '192.168.1.51',
      port: 8007,
      datastore: 'offsite',
      fingerprint: '11:22:33:44:55:66:77:88:99:aa:bb:cc:dd:ee:ff:00',
      api_token_id: 'joulenap@pbs!token',
      api_token_secret: '***REDACTED***',
      managed_power: true,
      mac: 'AA:BB:CC:DD:EE:01',
      wol_broadcast_iface: 'eth0',
      wait_timeout: 180,
      wol_retries: 2,
      poweroff_task_wait: 600,
      ssh_user: 'root',
      ssh_key_path: '/app/data/id_ed25519',
      external: { first_task_wait: 900, idle_wait: 300 },
    },
  ],
}

const ROUTE_LIST: Route[] = [
  {
    id: 'nightly',
    name: 'Nightly',
    color: ACCENT,
    enabled: true,
    notify: true,
    kind: 'backup',
    // Two sources into one target: the fan-in the topology's wires have to draw.
    sources: [
      { pve: 'pve-alpha', guests: { mode: 'all', list: [] } },
      { pve: 'pve-beta', guests: { mode: 'all', list: [] } },
    ],
    source_pbs: '',
    target: 'pbs-01',
    schedule: { time: '02:00', days: EVERY_DAY, cron: '' },
    retention: RETENTION,
    sync_direction: 'pull',
    options: OPTIONS,
  },
  {
    id: 'lab',
    name: 'Lab',
    color: BLUE,
    enabled: true,
    notify: true,
    kind: 'backup',
    sources: [{ pve: 'pve-lab', guests: { mode: 'include', list: [130, 131, 132] } }],
    source_pbs: '',
    target: 'pbs-01',
    schedule: { time: '04:00', days: SATURDAYS, cron: '' },
    retention: { ...RETENTION, keep_daily: 3, keep_weekly: 2, keep_monthly: 0 },
    sync_direction: 'pull',
    options: { ...OPTIONS, gc: false },
  },
  {
    id: 'offsite',
    name: 'Offsite sync',
    color: AMBER,
    enabled: true,
    notify: true,
    kind: 'sync',
    // A sync route wakes both boxes: source_pbs -> target, no PVE involved.
    sources: [],
    source_pbs: 'pbs-01',
    target: 'pbs-02',
    schedule: { time: '05:00', days: SUNDAYS, cron: '' },
    retention: RETENTION,
    sync_direction: 'pull',
    options: { ...OPTIONS, verify_after: true },
  },
]

const CONFIG: Config = {
  app: {
    scheduler_enabled: true,
    language: 'en',
    // The real backend persists app.theme; the stub resets on every reload, which would
    // override the toggle. Seeding from the same localStorage mirror fakes persistence.
    theme: localStorage.getItem('jnTheme') === 'light' ? 'light' : 'dark',
    port: 8080,
    timezone: 'Europe/Rome',
    secret_key: '***REDACTED***',
    api_key: '***REDACTED***',
    update_check: true,
    auth: { username: 'admin', password_hash: '***REDACTED***' },
    session: { https_only: false, max_age_days: 14 },
  },
  pves: DEVICES.pves,
  pbss: DEVICES.pbss,
  routes: ROUTE_LIST,
  maintenance: { history: { retention_days: 90 } },
  notifications: {
    on_success: true,
    on_failure: true,
    telegram: { enabled: false, bot_token: '', chat_id: '' },
    ntfy: { enabled: false, url: '', topic: '' },
    email: {
      enabled: false,
      smtp_host: '',
      smtp_port: 587,
      smtp_user: '',
      smtp_password: '',
      from_addr: '',
      to_addr: '',
    },
    discord: { enabled: false, webhook_url: '' },
    custom_urls: [],
  },
}

// The mockup's exact moment: Nightly is mid-backup on pbs-01 (which therefore has a lease
// holder, so its ⏻ button is protected), Lab is queued behind it, pbs-02 is asleep. Flip
// `state` to 'paused' or 'idle' to see the other two header-pill states.
const STATUS: StatusResponse = {
  state: 'running',
  scheduler_enabled: true,
  running: {
    run_id: 46,
    kind: 'cycle',
    started_at: '2026-07-09T21:28:40Z',
    route_id: 'nightly',
    route_name: 'Nightly',
  },
  queued: [{ key: 'lab', route_id: 'lab', pbs_id: 'pbs-01' }],
  next_runs: [
    { route_id: 'lab', route_name: 'Lab', at: '2026-07-11T04:00:00Z' },
    { route_id: 'offsite', route_name: 'Offsite sync', at: '2026-07-12T05:00:00Z' },
  ],
  pves: [
    { id: 'pve-alpha', online: true },
    { id: 'pve-beta', online: true },
    { id: 'pve-lab', online: true },
    { id: 'pve-spare', online: false },
  ],
  pbss: [
    {
      id: 'pbs-01',
      online: true,
      managed_power: true,
      holders: 1,
      datastore: { used: 1_900_000_000_000, total: 3_100_000_000_000, used_pct: 62 },
      load: { cpu: 12, mem: 41, uptime: 356_400 },
    },
    {
      // Asleep: the datastore figures survive from the cache, the live load does not.
      id: 'pbs-02',
      online: false,
      managed_power: true,
      holders: 0,
      datastore: { used: 2_200_000_000_000, total: 5_800_000_000_000, used_pct: 38 },
      load: null,
    },
  ],
  last_run: {
    id: 45,
    kind: 'sync',
    trigger: 'scheduled',
    status: 'success',
    started_at: '2026-07-05T05:00:00Z',
    finished_at: '2026-07-05T05:41:20Z',
    route_id: 'offsite',
    route_name: 'Offsite sync',
    guests_ok: null,
    error: null,
  },
  config_error: null,
}

const DASHBOARD: DashboardResponse = {
  state: 'running',
  routes: [
    {
      id: 'nightly',
      name: 'Nightly',
      kind: 'backup',
      enabled: true,
      next_run: '2026-07-10T02:00:00Z',
      last_run_status: 'success',
      last_run_time: '2026-07-09T02:00:00Z',
    },
    {
      id: 'lab',
      name: 'Lab',
      kind: 'backup',
      enabled: true,
      next_run: '2026-07-11T04:00:00Z',
      last_run_status: 'success',
      last_run_time: '2026-07-04T04:00:00Z',
    },
    {
      id: 'offsite',
      name: 'Offsite sync',
      kind: 'sync',
      enabled: true,
      next_run: '2026-07-12T05:00:00Z',
      last_run_status: 'success',
      last_run_time: '2026-07-05T05:00:00Z',
    },
  ],
  pbss: [
    {
      id: 'pbs-01',
      state: 'backing_up',
      datastore_used_pct: 62,
      datastore_used_bytes: 1_900_000_000_000,
      datastore_total_bytes: 3_100_000_000_000,
    },
    {
      id: 'pbs-02',
      state: 'sleeping',
      datastore_used_pct: 38,
      datastore_used_bytes: 2_200_000_000_000,
      datastore_total_bytes: 5_800_000_000_000,
    },
  ],
}

// One list per PVE, keyed by device id: GET /api/guests requires ?pve=, and the guest panel
// groups by PVE, so a single shared list would make every group identical. `pve-alpha` spans
// two nodes (the cluster sub-label in the topology reads the distinct node values), carries
// one guest synced onto both backup servers, one never backed up, and one deliberately long
// name for the ellipsis.
const GUESTS: Record<string, GuestInfo[]> = {
  'pve-alpha': [
    {
      vmid: 100,
      name: 'nextcloud-production-primary',
      type: 'lxc',
      status: 'running',
      node: 'alpha-1',
      last_backup: '2026-07-09T02:04:00Z',
      pbs_ids: ['pbs-01'],
    },
    {
      vmid: 101,
      name: 'homeassistant',
      type: 'qemu',
      status: 'running',
      node: 'alpha-1',
      last_backup: null,
      pbs_ids: [],
    },
    {
      vmid: 102,
      name: 'pihole',
      type: 'lxc',
      status: 'stopped',
      node: 'alpha-2',
      last_backup: '2026-07-09T02:07:00Z',
      pbs_ids: ['pbs-01', 'pbs-02'],
    },
  ],
  'pve-beta': [
    {
      vmid: 200,
      name: 'media-server',
      type: 'qemu',
      status: 'running',
      node: 'beta',
      last_backup: '2026-07-08T02:44:00Z',
      pbs_ids: ['pbs-01', 'pbs-02'],
    },
    {
      vmid: 201,
      name: 'jellyfin',
      type: 'lxc',
      status: 'running',
      node: 'beta',
      last_backup: '2026-07-08T02:46:00Z',
      pbs_ids: ['pbs-01'],
    },
  ],
  'pve-lab': [
    {
      vmid: 130,
      name: 'k3s-master',
      type: 'qemu',
      status: 'running',
      node: 'lab',
      last_backup: '2026-07-04T04:05:00Z',
      pbs_ids: ['pbs-01'],
    },
    {
      vmid: 131,
      name: 'k3s-worker',
      type: 'qemu',
      status: 'running',
      node: 'lab',
      last_backup: '2026-07-04T04:12:00Z',
      pbs_ids: ['pbs-01'],
    },
    {
      vmid: 132,
      name: 'sandbox',
      type: 'lxc',
      status: 'stopped',
      node: 'lab',
      last_backup: '2026-07-04T04:17:00Z',
      pbs_ids: ['pbs-01'],
    },
  ],
}

const LOGS: LogLine[] = [
  {
    id: 3,
    run_id: 44,
    ts: '2026-07-08T22:30:46Z',
    level: 'ERROR',
    message:
      'garbage collection failed: connection reset by peer while reading chunk index from datastore backup',
  },
  {
    id: 2,
    run_id: 45,
    ts: '2026-07-05T05:41:20Z',
    level: 'OK',
    message: 'sync finished: pbs-01 -> pbs-02, 3 groups',
  },
  {
    id: 1,
    run_id: 46,
    ts: '2026-07-09T21:28:40Z',
    level: 'INFO',
    message: 'wake-on-lan packet sent to pbs-01',
  },
]

const TASKLOG: TaskLogResponse = {
  run_id: 46,
  lines: [
    {
      id: 1,
      step: 'backup:pve-alpha',
      source: 'pve',
      text: 'INFO: Starting Backup of VM 100 (lxc)',
      ts: '2026-07-09T21:28:52Z',
    },
    {
      id: 2,
      step: 'backup:pve-alpha',
      source: 'pve',
      text: 'INFO: creating Proxmox Backup Server archive vm/100/2026-07-09T21:28:52Z',
      ts: '2026-07-09T21:28:54Z',
    },
    {
      id: 3,
      step: 'backup:pve-alpha',
      source: 'pve',
      text: 'INFO: Finished Backup of VM 100 (00:01:12)',
      ts: '2026-07-09T21:30:04Z',
    },
  ],
}

// GET /api/tasklog?run=<id>: what an expanded *finished* history row shows. Run 44 failed,
// so its tail carries the ERROR line the row's badge only hints at.
const TASKLOG_BY_RUN: Record<number, TaskLogResponse> = {
  45: {
    run_id: 45,
    lines: [
      {
        id: 11,
        step: 'sync',
        source: 'pbs',
        text: 'INFO: sync group vm/100 done, 3 snapshots, 12.4 GiB transferred',
        ts: '2026-07-05T05:12:00Z',
      },
      {
        id: 12,
        step: 'sync',
        source: 'pbs',
        text: 'INFO: sync group ct/102 done, 2 snapshots, 4.1 GiB transferred',
        ts: '2026-07-05T05:31:00Z',
      },
      {
        id: 13,
        step: 'sync',
        source: 'pbs',
        text: 'TASK OK',
        ts: '2026-07-05T05:41:00Z',
      },
    ],
  },
  44: {
    run_id: 44,
    lines: [
      {
        id: 8,
        step: 'gc',
        source: 'pbs',
        text: 'INFO: starting garbage collection on store backup',
        ts: '2026-07-08T22:28:00Z',
      },
      {
        id: 9,
        step: 'gc',
        source: 'pbs',
        text: 'ERROR: connection reset by peer while reading chunk index',
        ts: '2026-07-08T22:30:46Z',
      },
    ],
  },
}

// Run history: one of each outcome plus a sync, so the route column, the type column, the
// inline error and the still-running row (no finished_at -> elapsed duration) are all visible.
const RUNS: RunSummary[] = [
  {
    id: 46,
    kind: 'cycle',
    trigger: 'manual',
    status: 'running',
    started_at: '2026-07-09T21:28:40Z',
    finished_at: null,
    route_id: 'nightly',
    route_name: 'Nightly',
    guests_ok: null,
    error: null,
  },
  {
    id: 45,
    kind: 'sync',
    trigger: 'scheduled',
    status: 'success',
    started_at: '2026-07-05T05:00:00Z',
    finished_at: '2026-07-05T05:41:20Z',
    route_id: 'offsite',
    route_name: 'Offsite sync',
    guests_ok: null,
    error: null,
  },
  {
    id: 44,
    kind: 'cycle',
    trigger: 'scheduled',
    status: 'failure',
    started_at: '2026-07-08T22:30:00Z',
    finished_at: '2026-07-08T22:30:46Z',
    route_id: 'nightly',
    route_name: 'Nightly',
    guests_ok: null,
    error: 'vzdump exited with code 255: no space left on device',
  },
  {
    id: 43,
    kind: 'cycle',
    trigger: 'scheduled',
    status: 'success',
    started_at: '2026-07-09T02:00:00Z',
    finished_at: '2026-07-09T02:11:12Z',
    route_id: 'nightly',
    route_name: 'Nightly',
    guests_ok: 17,
    error: null,
  },
  {
    // An ad-hoc PBS maintenance run: no route at all, which the history's route column and
    // the header pill both have to survive.
    id: 42,
    kind: 'gc',
    trigger: 'manual',
    status: 'aborted',
    started_at: '2026-07-07T19:05:00Z',
    finished_at: '2026-07-07T19:08:02Z',
    route_id: null,
    route_name: null,
    guests_ok: null,
    error: 'PBS did not come up within 180s',
  },
]

const RUN_DETAIL: Record<number, RunDetail> = {
  44: {
    ...RUNS[2],
    steps: [
      { name: 'wait:pbs-01', status: 'success', started_at: '2026-07-08T22:30:00Z', finished_at: '2026-07-08T22:30:43Z', detail: 'reachable after 41s' },
      { name: 'backup:pve-alpha', status: 'failure', started_at: '2026-07-08T22:30:43Z', finished_at: '2026-07-08T22:30:46Z', detail: 'vzdump exit 255' },
      { name: 'backup:pve-beta', status: 'success', started_at: '2026-07-08T22:30:46Z', finished_at: '2026-07-08T22:30:46Z', detail: '5 guests' },
      { name: 'poweroff:pbs-01', status: 'skipped', started_at: '2026-07-08T22:30:46Z', finished_at: '2026-07-08T22:30:46Z', detail: 'left powered on' },
    ],
    logs: [
      { id: 91, run_id: 44, ts: '2026-07-08T22:30:43Z', level: 'INFO', message: 'Starting vzdump for 12 guests' },
      { id: 92, run_id: 44, ts: '2026-07-08T22:30:46Z', level: 'ERROR', message: 'vzdump failed: no space left on device' },
    ],
  },
  45: {
    ...RUNS[1],
    // A sync route takes two leases and releases them independently.
    steps: [
      { name: 'wait:pbs-02', status: 'success', started_at: '2026-07-05T05:00:00Z', finished_at: '2026-07-05T05:00:44Z', detail: 'reachable after 44s' },
      { name: 'wait:pbs-01', status: 'success', started_at: '2026-07-05T05:00:44Z', finished_at: '2026-07-05T05:00:44Z', detail: 'already awake' },
      { name: 'sync', status: 'success', started_at: '2026-07-05T05:00:44Z', finished_at: '2026-07-05T05:39:02Z', detail: '3 groups' },
      { name: 'verify', status: 'success', started_at: '2026-07-05T05:39:02Z', finished_at: '2026-07-05T05:41:00Z', detail: null },
      { name: 'poweroff:pbs-01', status: 'success', started_at: '2026-07-05T05:41:00Z', finished_at: '2026-07-05T05:41:10Z', detail: null },
      { name: 'poweroff:pbs-02', status: 'success', started_at: '2026-07-05T05:41:10Z', finished_at: '2026-07-05T05:41:20Z', detail: null },
    ],
    logs: [
      { id: 80, run_id: 45, ts: '2026-07-05T05:41:20Z', level: 'OK', message: 'sync finished: 3 groups' },
    ],
  },
}

// --- the demo replay ---------------------------------------------------------
// Only reachable under VITE_DEMO=1. Everything below mutates the fixtures above, so dev mode
// must never enter it — see the `DEMO ?` guard on the seam at the bottom of the file.

type Job = {
  id: number
  run: DemoRun
  routeId: string | null
  routeName: string | null
  trigger: string
  /** null while the job waits its turn behind the one in flight. */
  startedMs: number | null
}

let queue: Job[] = []
let nextRunId = 48
let nextLogId = 100
/** Boxes lit by the ⏻ button, or left on by a stop — awake independently of any run's lease. */
const awake = new Set<string>()

const iso = (ms: number) => new RealDate(ms).toISOString()
const live = () => (queue[0]?.startedMs != null ? queue[0] : null)
const elapsedOf = (j: Job) => (nowMs() - j.startedMs!) / 1000
const boxesOf = (j: Job) => Object.keys(j.run.online)

/** The boxes a still-queued job will need. The real lease checks exactly this before it powers
 *  a box off, and answers `left on: still needed by another run`. */
function pendingBoxes(): Set<string> {
  return new Set(queue.slice(1).flatMap(boxesOf))
}

/** Which of this run's power-offs the lease would skip, by step name. */
function skippedPoweroffs(j: Job): Set<string> {
  const held = pendingBoxes()
  return new Set(
    j.run.steps.map((s) => s.name).filter((n) => n.startsWith('poweroff:') && held.has(n.slice(9))),
  )
}

/** Every box answering right now: the live run's own window, plus the ones it is holding on
 *  for a queued run, plus anything the visitor woke by hand. */
function onlineNow(j: Job | null, st: DemoState | null): Set<string> {
  const on = new Set(awake)
  if (!j || !st) return on
  st.online.forEach((id) => on.add(id))
  const held = pendingBoxes()
  const elapsed = elapsedOf(j)
  for (const [id, [up]] of Object.entries(j.run.online)) {
    if (held.has(id) && elapsed >= up) on.add(id)
  }
  return on
}

function summaryOf(j: Job, running: boolean, status = 'success', error: string | null = null) {
  return {
    id: j.id,
    kind: j.run.kind,
    trigger: j.trigger,
    status: running ? 'running' : status,
    started_at: iso(j.startedMs!),
    finished_at: running ? null : iso(nowMs()),
    route_id: j.routeId,
    route_name: j.routeName,
    guests_ok: running || status !== 'success' ? null : j.run.guestsOk,
    error,
  } satisfies RunSummary
}

function stepsOf(j: Job, st: DemoState): StepInfo[] {
  const skipped = skippedPoweroffs(j)
  return st.steps.map((s) => ({
    name: s.name,
    status: skipped.has(s.name) ? 'skipped' : s.status,
    started_at: iso(j.startedMs! + s.from * 1000),
    finished_at: s.to === null ? null : iso(j.startedMs! + s.to * 1000),
    detail: skipped.has(s.name) ? 'left on: still needed by another run' : s.detail,
  }))
}

function linesOf(j: Job, st: DemoState): TaskLogLine[] {
  const skipped = skippedPoweroffs(j)
  return st.events
    .filter((e) => !skipped.has(e.step))
    .map((e, i) => ({
      id: j.id * 1000 + i,
      step: e.step,
      source: e.source,
      text: e.text,
      ts: iso(j.startedMs! + e.at * 1000),
    }))
}

function archive(j: Job, summary: RunSummary, steps: StepInfo[], lines: TaskLogLine[]): void {
  const log: LogLine = {
    id: nextLogId++,
    run_id: j.id,
    ts: summary.finished_at!,
    level: summary.status === 'success' ? 'OK' : summary.status === 'aborted' ? 'WARN' : 'ERROR',
    message: summary.status === 'success' ? j.run.summary : (summary.error ?? 'run stopped'),
  }
  RUNS.unshift(summary)
  RUN_DETAIL[j.id] = { ...summary, steps, logs: [log] }
  // An expanded history row refetches the task log by id once the run stops being the live one.
  TASKLOG_BY_RUN[j.id] = { run_id: j.id, lines }
  STATUS.last_run = summary
  LOGS.unshift(log)
}

/** Archive whatever finished, start whatever is next. Called before every read. */
function tick(): void {
  for (;;) {
    const head = queue[0]
    if (!head) return
    if (head.startedMs == null) {
      head.startedMs = nowMs()
      LOGS.unshift({
        id: nextLogId++,
        run_id: head.id,
        ts: iso(head.startedMs),
        level: 'INFO',
        message: head.routeName
          ? `starting ${head.run.kind} run for route '${head.routeName}'`
          : `starting ${head.run.kind} on ${boxesOf(head)[0]}`,
      })
      return
    }
    const st = stateAt(head.run, elapsedOf(head))
    if (st.running) return
    // A box held open for a queued run is not released here either — the next job's own
    // window opens on top of it, so the topology never blinks between the two.
    archive(head, summaryOf(head, false), stepsOf(head, st), linesOf(head, st))
    queue.shift()
  }
}

function enqueue(job: Omit<Job, 'startedMs'>): number {
  queue.push({ ...job, startedMs: null })
  tick()
  return Math.max(0, queue.length - 1)
}

/** The dynamic half of the API. Returns undefined to fall through to the static fixtures. */
function demoRoute(key: string, search: URLSearchParams, init?: RequestInit): unknown {
  tick()
  const j = live()
  const st = j ? stateAt(j.run, elapsedOf(j)) : null
  const body = () => JSON.parse((init?.body as string) ?? '{}')

  switch (key) {
    // The stub's "-stub" marker and its pending-update badge are dev affordances; a public
    // demo should look like a current, healthy install.
    case 'GET /health':
      return { status: 'ok', version: '1.1.0' }
    case 'GET /update':
      return {
        current: '1.1.0',
        latest: '1.1.0',
        update_available: false,
        url: 'https://github.com/Joulenap/joulenap/releases',
      }
    case 'GET /status': {
      const on = onlineNow(j, st)
      return {
        ...STATUS,
        state: !CONFIG.app.scheduler_enabled ? 'paused' : j ? 'running' : 'idle',
        running: j
          ? {
              run_id: j.id,
              kind: j.run.kind,
              started_at: iso(j.startedMs!),
              route_id: j.routeId,
              route_name: j.routeName,
            }
          : null,
        queued: queue.slice(1).map((q) => ({
          key: q.routeId ?? `pbs:${boxesOf(q)[0]}:${q.run.kind}`,
          route_id: q.routeId,
          pbs_id: boxesOf(q)[0] ?? null,
        })),
        pbss: STATUS.pbss.map((p) => ({
          ...p,
          online: on.has(p.id),
          holders: j && p.id in j.run.online ? 1 : 0,
          load: on.has(p.id) ? (p.load ?? { cpu: 12, mem: 41, uptime: 356_400 }) : null,
        })),
      } satisfies StatusResponse
    }
    case 'GET /runs':
      return j ? [summaryOf(j, true), ...RUNS] : RUNS
    case 'GET /tasklog':
      // `?run=` asks for a finished run, which TASKLOG_BY_RUN already holds.
      if (search.has('run')) return undefined
      return j ? { run_id: j.id, lines: linesOf(j, st!) } : { run_id: null, lines: [] }
  }

  if (j && key === `GET /runs/${j.id}`) {
    return { ...summaryOf(j, true), steps: stepsOf(j, st!), logs: [] }
  }

  if (j && key === `POST /runs/${j.id}/stop`) {
    // power_off is the direct sense here (runRoute's keep_on is the inverted one).
    if (body().power_off === false) boxesOf(j).forEach((p) => awake.add(p))
    // The step the stop interrupted is closed as aborted: a finished run must never archive a
    // step still reading "running".
    const steps = stepsOf(j, st!).map((s) =>
      s.status === 'running'
        ? { ...s, status: 'aborted', finished_at: iso(nowMs()), detail: 'stopped from the UI' }
        : s,
    )
    archive(j, summaryOf(j, false, 'aborted', 'stopped from the UI'), steps, linesOf(j, st!))
    queue.shift()
    return { run_id: j.id }
  }

  const runRoute = /^POST \/routes\/([^/]+)\/run$/.exec(key)
  if (runRoute) {
    const id = runRoute[1]
    const route = CONFIG.routes.find((r) => r.id === id)
    // ponytail: keep_on is accepted and ignored — every scripted arc ends in a power-off, and
    // the dialog defaults to powering down anyway. Script a second arc if that toggle ever
    // becomes the point of the demo.
    const queued = enqueue({
      id: nextRunId++,
      run: runFor(id),
      routeId: id,
      routeName: route?.name || id,
      trigger: 'manual',
    })
    return { route_id: id, queued }
  }

  const maint = /^POST \/devices\/pbss\/([^/]+)\/(gc|verify)$/.exec(key)
  if (maint) {
    const [, pbs, action] = maint
    const queued = enqueue({
      id: nextRunId++,
      run: maintenanceRun(pbs, action as 'gc' | 'verify'),
      routeId: null,
      routeName: null,
      trigger: 'manual',
    })
    return { pbs_id: pbs, action, queued }
  }

  const power = /^POST \/devices\/pbss\/([^/]+)\/power$/.exec(key)
  if (power) {
    if (body().action === 'wake') awake.add(power[1])
    else awake.delete(power[1])
    return { ok: true }
  }
  return undefined
}

function startDemo(): void {
  shiftDates([STATUS, DASHBOARD, RUNS, RUN_DETAIL, LOGS, TASKLOG, TASKLOG_BY_RUN, GUESTS])
  // The fixture's permanently-"running" row 46 is the job the replay now owns; leaving it in
  // RUNS would duplicate the live summary and collide on the React key.
  const running = RUNS.findIndex((r) => r.status === 'running')
  if (running >= 0) RUNS.splice(running, 1)
  queue = [
    {
      id: 46,
      run: runFor('nightly'),
      routeId: 'nightly',
      routeName: 'Nightly',
      trigger: 'scheduled',
      // Seeded mid-backup: the visitor lands on a run already streaming its task log rather
      // than on a still image waiting for a click.
      startedMs: nowMs() - 12_000,
    },
    {
      id: 47,
      run: runFor('lab'),
      routeId: 'lab',
      routeName: 'Lab',
      trigger: 'scheduled',
      startedMs: null,
    },
  ]
}

function mountDemoBanner(): void {
  const css = document.createElement('style')
  // In the flow rather than fixed: it then wraps to any width without a padding hack, and
  // cannot end up covering the header on a phone.
  css.textContent = `
    #jn-demo-banner {
      display: flex; align-items: center; justify-content: center; gap: 8px; flex-wrap: wrap;
      background: #e8830f; color: #12151a; font: 600 13px/1.4 system-ui, sans-serif;
      padding: 9px 12px; text-align: center;
    }
    #jn-demo-banner a { color: inherit; }
    @media (max-width: 600px) { #jn-demo-banner { font-size: 11px; } }
  `
  const bar = document.createElement('div')
  bar.id = 'jn-demo-banner'
  // English only, like the fixtures themselves: this element lives outside React and has no
  // access to t(), and adding a key for it would put demo copy in both locale packs.
  bar.innerHTML =
    'DEMO — fake data, no Proxmox attached. Runs are scripted; reload to reset. ' +
    '<a href="https://www.joulenap.com/">joulenap.com</a>'
  document.head.append(css)
  document.body.prepend(bar)
}

if (DEMO) {
  startDemo()
  mountDemoBanner()
}

// --- setup wizard fixtures ---------------------------------------------------
// Lets the wizard advance card-by-card with no backend: connecting PVE returns a node
// and a PBS-backed storage, confirming that storage seeds the PBS card, checking the
// PBS host succeeds, and the SSH card's keygen/host-key-scan/trust steps each return
// enough to unlock the next button. Same PBS host/fingerprint as the pbs-01 device
// fixture above, for consistency.
const WIZARD_PBS_FINGERPRINT = 'aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99'

// Two storages on purpose, so one run of flow A shows both halves of step 2: `pbs-backup`
// points at the registered pbs-01 (host + datastore match, so it auto-links and step 3 is
// skipped) while `pbs-archive` is unknown and offers "Configure now". Three nodes so the
// cluster line has something to say.
const WIZARD_PVE_CONNECT: PveConnectResult = {
  connected: true,
  version: '8.2.4',
  nodes: [
    { node: 'alpha-1', status: 'online' },
    { node: 'alpha-2', status: 'online' },
    { node: 'alpha-3', status: 'online' },
  ],
  storages: [
    {
      storage: 'pbs-backup',
      host: '192.168.1.50',
      port: 8007,
      datastore: 'backup',
      fingerprint: WIZARD_PBS_FINGERPRINT,
    },
    {
      storage: 'pbs-archive',
      host: '192.168.1.52',
      port: 8007,
      datastore: 'archive',
      fingerprint: '99:88:77:66:55:44:33:22:11:00:ff:ee:dd:cc:bb:aa',
    },
  ],
  token: { id: 'root@pam!joulenap', secret: 'stub-pve-token-secret' },
}

const WIZARD_STORAGE_DERIVE: PbsDerive = {
  host: '192.168.1.50',
  port: 8007,
  datastore: 'backup',
  fingerprint: WIZARD_PBS_FINGERPRINT,
}

const WIZARD_PBS_CHECK: { reachable: boolean; fingerprint: string | null } = {
  reachable: true,
  fingerprint: WIZARD_PBS_FINGERPRINT,
}

const WIZARD_PBS_PROVISION: { id: string; secret: string } = {
  id: 'joulenap@pbs!token',
  secret: 'stub-pbs-token-secret',
}

const WIZARD_PBS_GRANT_SYNC: { token_id: string; roles: string[] } = {
  token_id: 'root@pam!joulenap',
  roles: ['RemoteAdmin', 'RemoteSyncPushOperator'],
}

const WIZARD_INTERFACES: NetInterface[] = [
  { name: 'eth0', address: '192.168.1.20', netmask: '255.255.255.0', broadcast: '192.168.1.255' },
  { name: 'eth1', address: '10.0.0.5', netmask: '255.255.255.0', broadcast: '10.0.0.255' },
]

const WIZARD_DETECT_MAC: { mac: string | null } = { mac: 'AA:BB:CC:DD:EE:FF' }

// `created: false` — the stub always answers as if a key were already on disk, which is the
// second-PBS case and the one whose wording ("reusing the existing key") needs looking at.
const WIZARD_KEYGEN: {
  public_key: string
  authorized_keys_line: string
  key_path: string
  created: boolean
} = {
  public_key: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIStubKeyMaterialForDevPreviewOnly stub',
  authorized_keys_line:
    'command="systemctl poweroff",no-port-forwarding,no-x11-forwarding,no-agent-forwarding,' +
    'no-pty ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIStubKeyMaterialForDevPreviewOnly joulenap',
  key_path: '/app/data/id_ed25519',
  created: false,
}

const WIZARD_SSH_INSTALL: { installed: boolean } = { installed: true }

const WIZARD_SSH_HOSTKEY: { key_type: string; key_base64: string; fingerprint: string } = {
  key_type: 'ssh-ed25519',
  key_base64: 'AAAAC3NzaC1lZDI1NTE5AAAAIStubHostKeyMaterialForDevPreviewOnly',
  fingerprint: 'SHA256:StubHostKeyFingerprintForDevPreviewOnlyXXXXXXXXXXX',
}

const WIZARD_SSH_TRUST: { trusted: boolean } = { trusted: true }

const WIZARD_WOL_TEST: { sent: boolean; mac: string; broadcast: string } = {
  sent: true,
  mac: 'AA:BB:CC:DD:EE:FF',
  broadcast: '192.168.1.255',
}

const ROUTES: Record<string, unknown> = {
  'GET /health': { status: 'ok', version: '1.1.0-stub' },
  'GET /update': {
    current: '1.1.0-stub',
    latest: '1.1.0',
    update_available: true,
    url: 'https://github.com/Joulenap/joulenap/releases',
  },
  'GET /auth/status': AUTH_STATUS,
  'GET /auth/me': ME,
  'GET /status': STATUS,
  'GET /dashboard': DASHBOARD,
  'GET /config': CONFIG,
  'PUT /config': CONFIG,
  // ponytail: JSON is valid YAML, so the editor parses it and this can never drift from the
  // typed CONFIG above — which the hand-written YAML string it replaces silently did. Swap in
  // a real dumper only if a screenshot needs block style.
  'GET /config/yaml': { yaml: JSON.stringify(CONFIG, null, 2) },
  'PUT /config/yaml': CONFIG,
  // /guests and /tasklog are answered from their query string below, not from this table.
  'GET /runs': RUNS,
  'GET /routes': ROUTE_LIST,
  'GET /devices': DEVICES,
  'POST /wizard/pve/connect': WIZARD_PVE_CONNECT,
  'POST /wizard/storage/derive': WIZARD_STORAGE_DERIVE,
  'POST /wizard/pbs/check': WIZARD_PBS_CHECK,
  'POST /wizard/pbs/provision': WIZARD_PBS_PROVISION,
  'POST /wizard/pbs/grant-sync': WIZARD_PBS_GRANT_SYNC,
  'GET /wizard/interfaces': WIZARD_INTERFACES,
  'POST /wizard/wol/detect-mac': WIZARD_DETECT_MAC,
  'POST /wizard/ssh/keygen': WIZARD_KEYGEN,
  'POST /wizard/ssh/install': WIZARD_SSH_INSTALL,
  'POST /wizard/ssh/hostkey': WIZARD_SSH_HOSTKEY,
  'POST /wizard/ssh/trust': WIZARD_SSH_TRUST,
  'POST /wizard/wol/test': WIZARD_WOL_TEST,
  // Delivery outcomes come back as 200, one entry per enabled channel — one failing, so the
  // Notifications tab's per-channel report has both states to render.
  'POST /notify/test': {
    channels: [
      { channel: 'telegram', ok: true, error: null },
      { channel: 'ntfy', ok: false, error: 'Name or service not known: ntfy.lan' },
    ],
  },
  'POST /config/api-key': { api_key: 'jn_r0d94q7km2c8vt1zx5w3nbhy6f41c' },
}

const realFetch = globalThis.fetch.bind(globalThis)

globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const path = url.replace(/^https?:\/\/[^/]+/, '')
  if (!path.startsWith('/api/')) return realFetch(input as RequestInfo, init)

  const method = (init?.method ?? 'GET').toUpperCase()
  const [bare, search] = (() => {
    const [p, q = ''] = path.slice(4).split('?')
    return [p, new URLSearchParams(q)] as const
  })()
  const key = `${method} ${bare}`

  // There is nothing to log back into: the demo's login screen accepts any password, so
  // signing out would be a dead end. Reload instead, which doubles as "start over".
  if (DEMO && key === 'POST /logout') {
    location.reload()
    return new Promise<Response>(() => {})
  }

  // The scripted replay answers first and returns undefined to fall through, so every static
  // fixture and every mutating branch below is reached unchanged in both modes.
  let body: unknown = DEMO ? demoRoute(key, search, init) : undefined
  if (body === undefined) body = ROUTES[key]
  // The real backend persists and echoes the config it just saved; the static CONFIG would
  // silently undo edits (e.g. the theme toggle reverting on the PUT response / next GET).
  if (key === 'PUT /config' && typeof init?.body === 'string') {
    Object.assign(CONFIG, JSON.parse(init.body))
    body = CONFIG
  }
  // Read through to the live lists rather than the table's snapshot, which route and device
  // CRUD replace.
  if (key === 'GET /routes') body = CONFIG.routes
  if (key === 'GET /devices') body = { pves: CONFIG.pves, pbss: CONFIG.pbss }
  if (body === undefined && key.startsWith('GET /logs')) body = LOGS
  if (body === undefined && bare === '/logs') body = LOGS
  // Query-dependent fixtures: the guest panel asks per PVE, and an expanded history row asks
  // for one run's task log rather than the newest.
  if (body === undefined && key === 'GET /guests') {
    body = GUESTS[search.get('pve') ?? ''] ?? []
  }
  if (body === undefined && key === 'GET /tasklog') {
    const run = search.get('run')
    body = run
      ? (TASKLOG_BY_RUN[Number(run)] ?? { run_id: Number(run), lines: [] })
      : TASKLOG
  }
  // Route CRUD really mutates CONFIG.routes, unlike the rest of the writes below: the route
  // editor's whole point is that a saved route shows up in the strip and the topology on the
  // next config read, and a stub that answered {ok:true} would make that look broken.
  if (key === 'POST /routes' && typeof init?.body === 'string') {
    const created = JSON.parse(init.body) as Route
    if (CONFIG.routes.some((r) => r.id === created.id)) {
      return new Response(JSON.stringify({ detail: `Route '${created.id}' already exists` }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    CONFIG.routes = [...CONFIG.routes, created]
    body = created
  }
  const editId = /^\/routes\/([^/]+)$/.exec(bare)?.[1]
  if (editId && method === 'PUT' && typeof init?.body === 'string') {
    const updated = JSON.parse(init.body) as Route
    CONFIG.routes = CONFIG.routes.map((r) => (r.id === editId ? updated : r))
    body = updated
  }
  if (editId && method === 'DELETE') {
    CONFIG.routes = CONFIG.routes.filter((r) => r.id !== editId)
    return new Response(null, { status: 204 })
  }
  // The kill-switch mutates for the same reason: its whole point is the persistent banner the
  // shell draws from /status, and a stub that answered {ok:true} would make it look dead.
  if (key === 'POST /scheduler/toggle' && typeof init?.body === 'string') {
    const { enabled } = JSON.parse(init.body) as { enabled: boolean }
    CONFIG.app.scheduler_enabled = enabled
    STATUS.scheduler_enabled = enabled
    STATUS.state = enabled ? (STATUS.running ? 'running' : 'idle') : 'paused'
    body = { enabled, next_runs: STATUS.next_runs.map((n) => ({ route_id: n.route_id, at: n.at })) }
  }
  // Device CRUD mutates too, for the same reason as routes above: the Devices tab's whole
  // point is that an edit or a removal shows up on the next config read. The removal guard
  // needs the real 409 payload, so it is reproduced here rather than answered {ok:true}.
  // ...and creation mutates, because that is the only thing the wizard's last step produces:
  // without it a whole flow ends on a report about devices that are nowhere to be seen.
  const created = method === 'POST' ? /^\/devices\/(pves|pbss)$/.exec(bare) : null
  if (created && typeof init?.body === 'string') {
    const section = created[1] as 'pves' | 'pbss'
    const made = JSON.parse(init.body) as { id: string }
    if ((CONFIG[section] as { id: string }[]).some((d) => d.id === made.id)) {
      return new Response(JSON.stringify({ detail: `Device '${made.id}' already exists` }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    ;(CONFIG[section] as unknown[]) = [...CONFIG[section], made]
    body = made
  }
  const device = /^\/devices\/(pves|pbss)\/([^/]+)$/.exec(bare)
  if (device) {
    const [, section, id] = device as unknown as [string, 'pves' | 'pbss', string]
    const list = CONFIG[section] as { id: string }[]
    if (method === 'PUT' && typeof init?.body === 'string') {
      const updated = JSON.parse(init.body) as { id: string }
      ;(CONFIG[section] as unknown[]) = list.map((d) => (d.id === id ? updated : d))
      body = updated
    }
    if (method === 'DELETE') {
      const used = CONFIG.routes
        .filter((r) =>
          section === 'pbss'
            ? r.target === id || r.source_pbs === id
            : r.sources.some((s) => s.pve === id),
        )
        .map((r) => r.name || r.id)
      if (used.length) {
        return new Response(
          JSON.stringify({
            detail: {
              message: `'${id}' is used by ${used.length} route(s). Change or delete them first — removing the device would break them.`,
              routes: used,
            },
          }),
          { status: 409, headers: { 'Content-Type': 'application/json' } },
        )
      }
      ;(CONFIG[section] as unknown[]) = list.filter((d) => d.id !== id)
      return new Response(null, { status: 204 })
    }
  }
  // A connection test answers what the real endpoint answers: a one-liner for the card.
  const devTest = /^\/devices\/(pves|pbss)\/([^/]+)\/test$/.exec(bare)
  if (devTest && method === 'POST') {
    body =
      devTest[1] === 'pves'
        ? { ok: true, detail: '12 guest(s) visible' }
        : { ok: true, detail: 'datastore backup: 62% used' }
  }
  const devStorages = /^\/devices\/pves\/([^/]+)\/storages$/.exec(bare)
  if (devStorages && method === 'GET') {
    body = [{ storage: 'pbs', host: '192.168.1.30', port: 8007, datastore: 'backup', fingerprint: '' }]
  }
  if (devStorages && method === 'POST') {
    body = { storages: CONFIG.pves.find((p) => p.id === devStorages[1])?.storages ?? {} }
  }
  // ROUTES is keyed on exact paths, so /runs/{id} needs its own match.
  const runId = method === 'GET' ? /^\/runs\/(\d+)$/.exec(bare)?.[1] : undefined
  if (runId !== undefined && body === undefined) {
    body = RUN_DETAIL[Number(runId)] ?? {
      ...(RUNS.find((r) => r.id === Number(runId)) ?? RUNS[0]),
      steps: [],
      logs: [],
    }
  }
  // Everything unstubbed — route CRUD, device tests, manual runs, power — answers truthy, so
  // a missing fixture never blocks a click. Nothing here mutates state: the stub is a mirror
  // to look at, not a backend to drive.
  if (body === undefined) body = { ok: true }

  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}) as typeof fetch

export {}
