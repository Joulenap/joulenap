// Dev/preview only. Imported from main.tsx behind `import.meta.env.VITE_STUB_API`,
// so Vite's static replacement drops it from production builds.
//
// Two jobs:
//   1. answer every `/api/*` fetch from fixtures, so the SPA renders with no backend;
//   2. pin the clock, so repeated screenshots of the same layout are byte-identical.
import type {
  AuthStatus,
  Config,
  GuestInfo,
  LogLine,
  NetInterface,
  PbsDerive,
  PveConnectResult,
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
// message, an ERROR level, a non-trivial host.

const AUTH_STATUS: AuthStatus = { setup_needed: false, authenticated: true, username: 'admin' }
const ME: UserInfo = { username: 'admin' }

const CONFIG: Config = {
  app: {
    language: 'en',
    theme: 'dark',
    port: 8080,
    timezone: 'Europe/Rome',
    secret_key: 'stub-secret-key',
    api_key: 'stub-api-key',
    auth: { username: 'admin', password_hash: 'stub' },
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
    guests: { mode: 'include', auto_include_new: false, list: [100, 102] },
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
  pbs_online: true,
  last_run: {
    id: 42,
    kind: 'backup',
    trigger: 'schedule',
    status: 'ok',
    started_at: '2026-07-08T02:30:00Z',
    finished_at: '2026-07-08T02:41:12Z',
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

const ROUTES: Record<string, unknown> = {
  'GET /health': { status: 'ok', version: '0.3.1-stub' },
  'GET /auth/status': AUTH_STATUS,
  'GET /auth/me': ME,
  'GET /status': STATUS,
  'GET /config': CONFIG,
  'PUT /config': CONFIG,
  'GET /guests': GUESTS,
  'GET /tasklog': TASKLOG,
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

  let body: unknown = ROUTES[key]
  if (body === undefined && key.startsWith('GET /logs')) body = LOGS
  if (body === undefined && bare === '/logs') body = LOGS
  if (body === undefined) body = { ok: true }

  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}) as typeof fetch

export {}
