// Dev/preview only — see the guard in main.tsx for who is allowed to load this.
//
// Three jobs:
//   1. answer every `/api/*` fetch from fixtures, so the SPA renders with no backend;
//   2. pin the clock, so repeated screenshots of the same layout are byte-identical;
//   3. under `--mode demo` only (the public joulenap.com/demo build): let the clock run, slide
//      the fixtures up to today, and replay a scripted backup cycle when the visitor presses
//      "Run now". Everything demo-specific hangs off `DEMO` and leaves dev mode untouched.
import { runFor, stateAt, type DemoState } from './demoTimeline'
import type {
  AuthStatus,
  Config,
  GuestInfo,
  LogLine,
  NetInterface,
  PbsDerive,
  PveConnectResult,
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
// The demo needs a ticking header clock and a run that visibly advances, so it keeps the real
// clock; the freeze exists only to make dev screenshots reproducible.
if (!DEMO) globalThis.Date = FrozenDate as unknown as DateConstructor

// Fixture values are chosen to exercise the layout hard: a long guest name, a long log
// message, an ERROR level, a non-trivial host.

const AUTH_STATUS: AuthStatus = { setup_needed: false, authenticated: true, username: 'admin' }
const ME: UserInfo = { username: 'admin' }

const CONFIG: Config = {
  app: {
    language: 'en',
    // The real backend persists app.theme; the stub resets on every reload, which would
    // override the toggle. Seeding from the same localStorage mirror fakes persistence.
    theme: localStorage.getItem('jnTheme') === 'light' ? 'light' : 'dark',
    port: 8080,
    timezone: 'Europe/Rome',
    secret_key: 'stub-secret-key',
    api_key: 'stub-api-key',
    update_check: true,
    auth: { username: 'admin', password_hash: 'stub' },
    session: { https_only: false, max_age_days: 14 },
  },
  pve: {
    host: '192.168.1.10',
    port: 8006,
    node: 'pve',
    verify_tls: false,
    api_token_id: 'root@pam!joulenap',
    api_token_secret: 'stub-pve-secret',
    storage_id: 'pbs-backup',
  },
  pbs: {
    host: '192.168.1.50',
    port: 8007,
    datastore: 'backup-main',
    fingerprint: 'aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99',
    api_token_id: 'joulenap@pbs!token',
    api_token_secret: 'stub-pbs-secret',
    mac: 'AA:BB:CC:DD:EE:FF',
    wol_broadcast_iface: 'eth0',
    wait_timeout: 180,
    wol_retries: 3,
    poweroff_task_wait: 60,
    ssh_user: 'root',
    ssh_key_path: '/data/ssh/id_ed25519',
  },
  backup: {
    enabled: true,
    schedule: '30 2 * * 1,3,5',
    mode: 'snapshot',
    bwlimit: 0,
    min_free_percent: 10,
    guests: { mode: 'include', list: [100, 102] },
    retention: { keep_last: 0, keep_daily: 7, keep_weekly: 4, keep_monthly: 6, keep_yearly: 0 },
  },
  maintenance: {
    gc: { enabled: true },
    verify: { enabled: true, schedule: '0 4 * * 0', after_backup: false, reverify_days: 30 },
    history: { retention_days: 90 },
  },
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

const STATUS: StatusResponse = {
  scheduler_enabled: true,
  schedule: '30 2 * * 1,3,5',
  next_run: '2026-07-10T02:30:00Z',
  job_running: false,
  running_kind: null,
  pbs_online: true,
  last_run: {
    id: 42,
    kind: 'backup',
    trigger: 'schedule',
    status: 'ok',
    started_at: '2026-07-08T02:30:00Z',
    finished_at: '2026-07-08T02:41:12Z',
    guests_ok: 4,
    error: null,
  },
  datastore: { used: 1_800_000_000_000, total: 2_800_000_000_000, used_pct: 63 },
  load: { cpu: 12, mem: 41, uptime: 356_400 },
}

const GUESTS: GuestInfo[] = [
  {
    vmid: 100,
    name: 'nextcloud-production-primary',
    type: 'lxc',
    status: 'running',
    last_backup: '2026-07-08T02:35:00Z',
  },
  { vmid: 101, name: 'homeassistant', type: 'qemu', status: 'running', last_backup: null },
  { vmid: 102, name: 'pihole', type: 'lxc', status: 'stopped', last_backup: '2026-07-08T02:38:00Z' },
]

const LOGS: LogLine[] = [
  {
    id: 3,
    run_id: 42,
    ts: '2026-07-08T02:41:12Z',
    level: 'ERROR',
    message:
      'garbage collection failed: connection reset by peer while reading chunk index from datastore backup-main',
  },
  { id: 2, run_id: 42, ts: '2026-07-08T02:38:04Z', level: 'OK', message: 'backup completed: 3 guests, 12.4 GiB' },
  { id: 1, run_id: 42, ts: '2026-07-08T02:30:00Z', level: 'INFO', message: 'wake-on-lan packet sent' },
]

const TASKLOG: TaskLogResponse = { lines: [], run_id: null }

// Run history: one of each outcome, so the table's badges, the inline error and the
// still-running row (no finished_at -> elapsed duration) can all be seen without a backend.
const RUNS: RunSummary[] = [
  {
    id: 45,
    kind: 'cycle',
    trigger: 'manual',
    status: 'running',
    started_at: '2026-07-09T21:28:40Z',
    finished_at: null,
    guests_ok: null,
    error: null,
  },
  {
    id: 44,
    kind: 'verify',
    trigger: 'scheduled',
    status: 'success',
    started_at: '2026-07-09T04:00:00Z',
    finished_at: '2026-07-09T04:06:31Z',
    guests_ok: null,
    error: null,
  },
  {
    id: 43,
    kind: 'cycle',
    trigger: 'scheduled',
    status: 'failure',
    started_at: '2026-07-08T22:30:00Z',
    finished_at: '2026-07-08T22:30:46Z',
    guests_ok: null,
    error: 'vzdump exited with code 255: no space left on device',
  },
  {
    id: 42,
    kind: 'cycle',
    trigger: 'scheduled',
    status: 'success',
    started_at: '2026-07-08T02:30:00Z',
    finished_at: '2026-07-08T02:41:12Z',
    guests_ok: 4,
    error: null,
  },
  {
    id: 41,
    kind: 'gc',
    trigger: 'manual',
    status: 'aborted',
    started_at: '2026-07-07T19:05:00Z',
    finished_at: '2026-07-07T19:08:02Z',
    guests_ok: null,
    error: 'PBS did not come up within 180s',
  },
]

const RUN_DETAIL: Record<number, RunDetail> = {
  43: {
    ...RUNS[2],
    steps: [
      { name: 'wake', status: 'success', started_at: '2026-07-08T22:30:00Z', finished_at: '2026-07-08T22:30:02Z', detail: null },
      { name: 'wait', status: 'success', started_at: '2026-07-08T22:30:02Z', finished_at: '2026-07-08T22:30:43Z', detail: 'reachable after 41s' },
      { name: 'backup', status: 'failure', started_at: '2026-07-08T22:30:43Z', finished_at: '2026-07-08T22:30:46Z', detail: 'vzdump exit 255' },
    ],
    logs: [
      { id: 91, run_id: 43, ts: '2026-07-08T22:30:43Z', level: 'INFO', message: 'Starting vzdump for 4 guests' },
      { id: 92, run_id: 43, ts: '2026-07-08T22:30:46Z', level: 'ERROR', message: 'vzdump failed: no space left on device' },
    ],
  },
  42: {
    ...RUNS[3],
    steps: [
      { name: 'wake', status: 'success', started_at: '2026-07-08T02:30:00Z', finished_at: '2026-07-08T02:30:02Z', detail: null },
      { name: 'backup', status: 'success', started_at: '2026-07-08T02:30:40Z', finished_at: '2026-07-08T02:40:05Z', detail: '4 guests' },
      { name: 'gc', status: 'success', started_at: '2026-07-08T02:40:05Z', finished_at: '2026-07-08T02:41:00Z', detail: null },
      { name: 'poweroff', status: 'success', started_at: '2026-07-08T02:41:00Z', finished_at: '2026-07-08T02:41:12Z', detail: null },
    ],
    logs: [
      { id: 80, run_id: 42, ts: '2026-07-08T02:41:12Z', level: 'OK', message: 'Backup finished, 4 guests' },
    ],
  },
}

// --- setup wizard fixtures ---------------------------------------------------
// Lets the wizard advance card-by-card with no backend: connecting PVE returns a node
// and a PBS-backed storage, confirming that storage seeds the PBS card, checking the
// PBS host succeeds, and the SSH card's keygen/host-key-scan/trust steps each return
// enough to unlock the next button. Same PBS host/fingerprint as the dashboard CONFIG
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
      datastore: 'backup-main',
      fingerprint: WIZARD_PBS_FINGERPRINT,
    },
  ],
  token: { id: 'root@pam!joulenap', secret: 'stub-pve-token-secret' },
}

const WIZARD_STORAGE_DERIVE: PbsDerive = {
  host: '192.168.1.50',
  port: 8007,
  datastore: 'backup-main',
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
  key_path: '/data/ssh/id_ed25519',
}

const WIZARD_SSH_INSTALL: { installed: boolean } = { installed: true }

const WIZARD_SSH_HOSTKEY: { key_type: string; key_base64: string; fingerprint: string } = {
  key_type: 'ssh-ed25519',
  key_base64: 'AAAAC3NzaC1lZDI1NTE5AAAAIStubHostKeyMaterialForDevPreviewOnly',
  fingerprint: 'SHA256:StubHostKeyFingerprintForDevPreviewOnlyXXXXXXXXXXX',
}

const WIZARD_SSH_TRUST: { trusted: boolean } = { trusted: true }

const WIZARD_RESET: { ok: boolean } = { ok: true }

// What GET /config/yaml returns: the redacted config re-serialised, same shape the backend
// dumps. Hand-written here (no YAML dumper in the stub) but kept in step with CONFIG above.
const CONFIG_YAML = `app:
  language: en
  theme: dark
  port: 8080
  timezone: Europe/Rome
  secret_key: '***REDACTED***'
  api_key: '***REDACTED***'
  update_check: true
  auth:
    username: admin
    password_hash: '***REDACTED***'
  session:
    https_only: false
    max_age_days: 14
pve:
  host: 192.168.1.10
  port: 8006
  node: pve
  verify_tls: false
  api_token_id: root@pam!joulenap
  api_token_secret: '***REDACTED***'
  storage_id: pbs
pbs:
  host: 192.168.1.50
  port: 8007
  datastore: backup
  mac: aa:bb:cc:dd:ee:ff
  wol_broadcast_iface: eth0
  wait_timeout: 180
  wol_retries: 2
  poweroff_task_wait: 600
  ssh_user: root
  ssh_key_path: /app/data/id_ed25519
backup:
  enabled: true
  schedule: 30 2 * * 1,3,5
  mode: snapshot
  bwlimit: 0
  min_free_percent: 10
  guests:
    mode: all
    list: []
  retention:
    keep_last: 0
    keep_daily: 7
    keep_weekly: 4
    keep_monthly: 6
    keep_yearly: 0
maintenance:
  gc:
    enabled: true
  verify:
    after_backup: false
    enabled: true
    schedule: 0 4 * * 0
    reverify_days: 30
  history:
    retention_days: 90
notifications:
  on_success: true
  on_failure: true
  custom_urls: []
`

// --- demo mode ---------------------------------------------------------------
// Only reached under `--mode demo`. Two halves: slide the fixtures onto today's calendar,
// and drive a scripted run from ./demoTimeline when the visitor presses Run now / Run GC.

const DAY_MS = 86_400_000
const RUN_DAYS = [1, 3, 5] // Mon/Wed/Fri — the fixture's `30 2 * * 1,3,5`
// Fixture times are UTC; re-reading them as local ones makes 02:30Z show up as 02:30 on the
// visitor's clock, which is what the schedule panel claims the backup time is.
const TZ_MS = new RealDate().getTimezoneOffset() * 60_000

let DELTA = 0

/**
 * How far to slide the fixtures onto today's calendar: whole days (so every clock time
 * survives), as recent as possible, but only a shift that lands the last backup on a
 * scheduled weekday and leaves no run in the future.
 */
function computeDelta(): number {
  const now = RealDate.now()
  const last = RealDate.parse(STATUS.last_run!.started_at)
  const newest = Math.max(...RUNS.map((r) => RealDate.parse(r.started_at)))
  const start = Math.floor((now - FIXED_MS) / DAY_MS) * DAY_MS + TZ_MS
  for (let d = 0; d < 10; d++) {
    const delta = start - d * DAY_MS
    if (newest + delta < now && RUN_DAYS.includes(new RealDate(last + delta).getDay())) return delta
  }
  return start
}

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

/** The next 02:30 on a scheduled weekday, on the visitor's clock. */
function nextRunIso(): string {
  const d = new RealDate(RealDate.now())
  d.setHours(2, 30, 0, 0)
  for (let i = 0; i < 8; i++) {
    if (d.getTime() > RealDate.now() && RUN_DAYS.includes(d.getDay())) break
    d.setDate(d.getDate() + 1)
  }
  return d.toISOString()
}

const iso = (ms: number) => new RealDate(ms).toISOString()

type Job = { runId: number; kind: 'cycle' | 'gc'; startedMs: number; archived: boolean }
let job: Job | null = null
let nextRunId = 46
let nextLogId = 100
// The PBS sleeps until something wakes it — that is the whole point of the product, so the
// demo starts with it off rather than with the dev fixture's always-on box.
let pbsOnline = false

if (DEMO) {
  // The dev fixture keeps a permanently "running" row (id 45) to exercise the elapsed-time
  // cell; on a demo it would be a job that never ends. Drop it before measuring the shift.
  const stuck = RUNS.findIndex((r) => r.status === 'running')
  if (stuck >= 0) RUNS.splice(stuck, 1)
  DELTA = computeDelta()
  shiftDates(STATUS)
  shiftDates(GUESTS)
  shiftDates(LOGS)
  shiftDates(RUNS)
  shiftDates(RUN_DETAIL)
  STATUS.pbs_online = false
  STATUS.load = null
  STATUS.next_run = nextRunIso()
}

const jobState = (j: Job): DemoState => stateAt(runFor(j.kind), (RealDate.now() - j.startedMs) / 1000)

const stepsOf = (j: Job, st: DemoState): StepInfo[] =>
  st.steps.map((s) => ({
    name: s.name,
    status: s.status,
    started_at: iso(j.startedMs + s.from * 1000),
    finished_at: s.to === null ? null : iso(j.startedMs + s.to * 1000),
    detail: null,
  }))

const taskLinesOf = (j: Job, st: DemoState): TaskLogLine[] =>
  st.events.map((e, i) => ({
    id: j.runId * 1000 + i,
    step: e.step === 'gc' ? 'gc' : 'backup',
    source: e.source,
    text: e.text,
    ts: iso(j.startedMs + e.at * 1000),
  }))

const summaryOf = (j: Job, running: boolean): RunSummary => {
  const run = runFor(j.kind)
  return {
    id: j.runId,
    kind: j.kind,
    trigger: 'manual',
    status: running ? 'running' : 'success',
    started_at: iso(j.startedMs),
    finished_at: running ? null : iso(j.startedMs + run.seconds * 1000),
    guests_ok: running || !run.guestsOk ? null : run.guestsOk,
    error: null,
  }
}

/** Move a finished run into the history exactly once. Called before every read. */
function tick(): void {
  if (!job || job.archived) return
  const st = jobState(job)
  if (st.running) return
  job.archived = true
  const summary = summaryOf(job, false)
  RUNS.unshift(summary)
  RUN_DETAIL[job.runId] = {
    ...summary,
    steps: stepsOf(job, st),
    logs: [
      {
        id: nextLogId++,
        run_id: job.runId,
        ts: summary.finished_at!,
        level: 'OK',
        message:
          job.kind === 'gc' ? 'garbage collection finished' : 'backup completed: 2 guests, 6.09 GiB',
      },
    ],
  }
  STATUS.last_run = summary
  LOGS.unshift(RUN_DETAIL[job.runId].logs[0])
  if (STATUS.datastore) {
    STATUS.datastore.used += job.kind === 'gc' ? -3_670_000_000 : 6_090_000_000
    STATUS.datastore.used_pct = Math.round((STATUS.datastore.used / STATUS.datastore.total) * 100)
  }
  pbsOnline = false
}

/** The stub's version with its marker suffix removed — one place to bump, not two. */
const DEMO_VERSION = () => (ROUTES['GET /health'] as { version: string }).version.replace('-stub', '')

/** Demo-only responses. Returns undefined to fall through to the static ROUTES table. */
function demoRoute(key: string, init?: RequestInit): unknown {
  tick()
  const live = job && !job.archived ? job : null
  const st = live ? jobState(live) : null

  switch (key) {
    // Dev wants the "-stub" marker and the update badge; a public demo wants neither.
    case 'GET /health':
      return { status: 'ok', version: DEMO_VERSION() }
    case 'GET /update':
      return {
        current: DEMO_VERSION(),
        latest: DEMO_VERSION(),
        update_available: false,
        url: 'https://github.com/Joulenap/joulenap/releases',
      }
    case 'PUT /account':
      return ME
    case 'GET /status':
      return {
        ...STATUS,
        job_running: !!live,
        running_kind: live ? live.kind : null,
        running_run_id: live ? live.runId : null,
        pbs_online: st ? st.pbsOnline : pbsOnline,
        load: (st ? st.pbsOnline : pbsOnline) ? { cpu: 12, mem: 41, uptime: 356_400 } : null,
      } satisfies StatusResponse
    case 'GET /runs':
      return live ? [summaryOf(live, true), ...RUNS] : RUNS
    case 'GET /tasklog': {
      // The last run's log stays on screen after it ends, like the real backend's tail.
      const j = job
      if (!j) return { run_id: null, lines: [] }
      return { run_id: j.runId, lines: taskLinesOf(j, jobState(j)) }
    }
    case 'POST /backup/run':
    case 'POST /gc/run': {
      if (live) return { run_id: live.runId }
      job = {
        runId: nextRunId++,
        kind: key === 'POST /gc/run' ? 'gc' : 'cycle',
        startedMs: RealDate.now(),
        archived: false,
      }
      LOGS.unshift({
        id: nextLogId++,
        run_id: job.runId,
        ts: iso(job.startedMs),
        level: 'INFO',
        message: 'wake-on-lan packet sent',
      })
      return { run_id: job.runId }
    }
    case 'POST /jobs/cancel': {
      if (!live) return { run_id: null }
      const summary = { ...summaryOf(live, false), status: 'aborted', error: 'cancelled from the UI' }
      live.archived = true
      RUNS.unshift(summary)
      RUN_DETAIL[live.runId] = { ...summary, steps: stepsOf(live, jobState(live)), logs: [] }
      STATUS.last_run = summary
      pbsOnline = false
      return { run_id: live.runId }
    }
    case 'POST /power/on':
      pbsOnline = true
      return { ok: true }
    case 'POST /power/off':
      pbsOnline = false
      return { ok: true }
    case 'POST /scheduler/toggle': {
      const enabled = !!JSON.parse((init?.body as string) ?? '{}').enabled
      STATUS.scheduler_enabled = enabled
      STATUS.next_run = enabled ? nextRunIso() : null
      return { enabled, next_run: STATUS.next_run }
    }
    case 'POST /wol/test':
      return { sent: true, mac: CONFIG.pbs.mac }
    case 'POST /notify/test':
      return { channels: [{ channel: 'telegram', ok: true, error: null }] }
    case 'POST /config/api-key':
      CONFIG.app.api_key = 'demo-0123456789abcdef0123456789abcdef'
      return { api_key: CONFIG.app.api_key }
  }

  // The live run has no row in RUN_DETAIL until it is archived.
  if (live && key === `GET /runs/${live.runId}`) {
    return { ...summaryOf(live, true), steps: stepsOf(live, jobState(live)), logs: [] }
  }
  return undefined
}

/** Fixed strip that makes it unmistakable this is a sandbox, not someone's real backup box. */
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
  bar.innerHTML =
    'DEMO — fake data, no Proxmox attached. Reload to reset. ' +
    '<a href="https://www.joulenap.com/">joulenap.com</a>'
  document.head.append(css)
  document.body.prepend(bar)
}

if (DEMO) mountDemoBanner()

const ROUTES: Record<string, unknown> = {
  'GET /health': { status: 'ok', version: '0.8.0-stub' },
  'GET /update': {
    current: '0.8.0-stub',
    latest: '0.8.0',
    update_available: true,
    url: 'https://github.com/Joulenap/joulenap/releases',
  },
  'GET /auth/status': AUTH_STATUS,
  'GET /auth/me': ME,
  'GET /status': STATUS,
  'GET /config': CONFIG,
  'PUT /config': CONFIG,
  'GET /config/yaml': { yaml: CONFIG_YAML },
  'PUT /config/yaml': CONFIG,
  'GET /guests': GUESTS,
  'GET /tasklog': TASKLOG,
  'GET /runs': RUNS,
  'POST /jobs/cancel': { run_id: 45 },
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
  'POST /wizard/reset': WIZARD_RESET,
}

const realFetch = globalThis.fetch.bind(globalThis)

globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const path = url.replace(/^https?:\/\/[^/]+/, '')
  if (!path.startsWith('/api/')) return realFetch(input as RequestInfo, init)

  const method = (init?.method ?? 'GET').toUpperCase()
  const bare = path.slice(4).split('?')[0]
  const key = `${method} ${bare}`

  // Logout has nowhere to go without a backend (the stub can't log back in), so on the demo it
  // doubles as "start over": reload, and never resolve the call that is about to be discarded.
  if (DEMO && key === 'POST /logout') {
    location.reload()
    return new Promise<Response>(() => {})
  }

  let body: unknown = DEMO ? demoRoute(key, init) : undefined
  if (body === undefined) body = ROUTES[key]
  // The real backend persists and echoes the config it just saved; the static CONFIG would
  // silently undo edits (e.g. the theme toggle reverting on the PUT response / next GET).
  if (key === 'PUT /config' && typeof init?.body === 'string') {
    Object.assign(CONFIG, JSON.parse(init.body))
    body = CONFIG
  }
  if (body === undefined && key.startsWith('GET /logs')) body = LOGS
  if (body === undefined && bare === '/logs') body = LOGS
  // ROUTES is keyed on exact paths, so /runs/{id} needs its own match.
  const runId = method === 'GET' ? /^\/runs\/(\d+)$/.exec(bare)?.[1] : undefined
  if (runId !== undefined && body === undefined) {
    body = RUN_DETAIL[Number(runId)] ?? {
      ...(RUNS.find((r) => r.id === Number(runId)) ?? RUNS[0]),
      steps: [],
      logs: [],
    }
  }
  if (body === undefined) body = { ok: true }

  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}) as typeof fetch

export {}
