// Dev/preview only — see the guard in main.tsx for who is allowed to load this.
//
// Two jobs:
//   1. answer every `/api/*` fetch from fixtures, so the SPA renders with no backend;
//   2. pin the clock, so repeated screenshots of the same layout are byte-identical.
//
// The fixtures reproduce the homepage mockup's scenario exactly — three PVEs, two PBSs, three
// routes (Nightly running, Lab, Offsite sync) — so M09+ can build the homepage against them.
//
// TODO(M14): the scripted demo replay lived here and was deleted with the 0.9 endpoints it
// drove (`/backup/run`, `/jobs/cancel`, `/power/*`). M14 rebuilds it around these route
// fixtures; until then `npm run build:demo` serves them statically, with no "Run now" replay.
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
  TaskLogResponse,
  UserInfo,
} from './api/types'

const FIXED_MS = Date.UTC(2026, 6, 9, 21, 30, 0)

const RealDate = Date
class FrozenDate extends RealDate {
  constructor(...args: ConstructorParameters<typeof Date>) {
    if ((args as unknown[]).length === 0) super(FIXED_MS)
    else super(...args)
  }
  static now() {
    return FIXED_MS
  }
}
globalThis.Date = FrozenDate as unknown as DateConstructor

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

// --- setup wizard fixtures ---------------------------------------------------
// Lets the wizard advance card-by-card with no backend: connecting PVE returns a node
// and a PBS-backed storage, confirming that storage seeds the PBS card, checking the
// PBS host succeeds, and the SSH card's keygen/host-key-scan/trust steps each return
// enough to unlock the next button. Same PBS host/fingerprint as the pbs-01 device
// fixture above, for consistency.
const WIZARD_PBS_FINGERPRINT = 'aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99'

const WIZARD_PVE_CONNECT: PveConnectResult = {
  connected: true,
  version: '8.2.4',
  nodes: [{ node: 'pve', status: 'online' }],
  storages: [
    {
      storage: 'pbs-backup',
      host: '192.168.1.50',
      port: 8007,
      datastore: 'backup',
      fingerprint: WIZARD_PBS_FINGERPRINT,
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

const WIZARD_INTERFACES: NetInterface[] = [
  { name: 'eth0', address: '192.168.1.20', netmask: '255.255.255.0', broadcast: '192.168.1.255' },
  { name: 'eth1', address: '10.0.0.5', netmask: '255.255.255.0', broadcast: '10.0.0.255' },
]

const WIZARD_DETECT_MAC: { mac: string | null } = { mac: 'AA:BB:CC:DD:EE:FF' }

const WIZARD_KEYGEN: { public_key: string; authorized_keys_line: string; key_path: string } = {
  public_key: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIStubKeyMaterialForDevPreviewOnly stub',
  authorized_keys_line:
    'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIStubKeyMaterialForDevPreviewOnly joulenap@stub',
  key_path: '/app/data/id_ed25519',
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
  'GET /health': { status: 'ok', version: '1.0.0-stub' },
  'GET /update': {
    current: '1.0.0-stub',
    latest: '1.0.0',
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
  'GET /wizard/interfaces': WIZARD_INTERFACES,
  'POST /wizard/wol/detect-mac': WIZARD_DETECT_MAC,
  'POST /wizard/ssh/keygen': WIZARD_KEYGEN,
  'POST /wizard/ssh/install': WIZARD_SSH_INSTALL,
  'POST /wizard/ssh/hostkey': WIZARD_SSH_HOSTKEY,
  'POST /wizard/ssh/trust': WIZARD_SSH_TRUST,
  'POST /wizard/wol/test': WIZARD_WOL_TEST,
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

  let body: unknown = ROUTES[key]
  // The real backend persists and echoes the config it just saved; the static CONFIG would
  // silently undo edits (e.g. the theme toggle reverting on the PUT response / next GET).
  if (key === 'PUT /config' && typeof init?.body === 'string') {
    Object.assign(CONFIG, JSON.parse(init.body))
    body = CONFIG
  }
  // Read through to the live list rather than the table's snapshot, which route CRUD replaces.
  if (key === 'GET /routes') body = CONFIG.routes
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
