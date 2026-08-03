// Thin typed wrapper over the JSON API. Same-origin session cookie auth; a non-2xx
// response throws ApiError carrying the status and the backend's `detail`.
import type {
  AuthStatus,
  Config,
  DashboardResponse,
  DeviceKind,
  DeviceLists,
  DeviceTestResult,
  GuestInfo,
  LogLine,
  MaintenanceQueued,
  NetInterface,
  NotifyTestResult,
  PbsDerive,
  PveConnectResult,
  Route,
  RunDetail,
  RunQueued,
  RunSummary,
  StatusResponse,
  TaskLogResponse,
  ToggleResponse,
  UserInfo,
} from './types'

export class ApiError extends Error {
  status: number
  // The backend's `detail` as parsed, when it wasn't a plain string — the YAML editor reads
  // {message, line} off it to mark the offending line. `message` stays a string either way.
  raw?: unknown
  // A plain field assignment, not a `public status` parameter property: the frontend test
  // harness runs `node --test` in strip-only TS mode, which rejects parameter properties.
  constructor(status: number, message: string, raw?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.raw = raw
  }
}

// A 401 on a session-protected endpoint means the cookie has expired; a central handler
// (registered by AuthProvider) flips the whole app back to the login screen, so every
// polling loop and page recovers at once instead of rendering stale data forever (FE-H3).
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn
}

// Endpoints that use 401 for their *own* logic — a wrong password on /login or the wrong
// current password on /account (BE-S9) — must NOT eject the user; only a dead session does.
const AUTH_SELF_HANDLED = new Set(['/login', '/account'])

// Client-side backstop timeout (ms). Sits *above* the backend's own probe ceilings (the
// slowest is the 30s httpx connect on wizard PVE/PBS), so it never pre-empts an informative
// backend error — it only fires when the backend itself stops responding (wedged process, a
// proxy black-holing the response), turning an indefinite fetch hang into a clean error
// (FE-M1). Every call is bounded: the long-running backup/GC jobs return a run_id immediately
// and are polled separately, so no request legitimately runs this long.
const DEFAULT_TIMEOUT_MS = 45000

// The timeout error text is localized, but this module lives outside React and can't call
// t() itself, so the app registers the translated string here (mirroring the 401 handler) and
// re-registers it on a language switch. A plain-English fallback covers the pre-registration
// window and keeps client.ts usable in tests.
let timeoutMessage = 'The request timed out.'
export function setTimeoutMessage(msg: string): void {
  timeoutMessage = msg
}

async function req<T>(method: string, path: string, body?: unknown, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let res: Response
  try {
    res = await fetch('/api' + path, {
      method,
      credentials: 'same-origin',
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
  } catch (e) {
    // Our own timeout aborts with an AbortError; surface it as an ApiError so callers show it
    // like any other failure. A caller-initiated abort would look identical, but we don't pass
    // external signals in yet, so every abort here is the timeout.
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new ApiError(408, timeoutMessage)
    }
    throw e
  } finally {
    clearTimeout(timer)
  }
  if (!res.ok) {
    if (res.status === 401 && !AUTH_SELF_HANDLED.has(path.split('?')[0])) {
      onUnauthorized?.()
    }
    let detail: string = res.statusText
    let raw: unknown
    try {
      const j = await res.json()
      if (j && typeof j.detail !== 'undefined') {
        if (typeof j.detail === 'string') {
          detail = j.detail
        } else {
          raw = j.detail
          // A structured detail: prefer its own message, else fall back to the JSON dump.
          const m = (j.detail as { message?: unknown }).message
          detail = typeof m === 'string' ? m : JSON.stringify(j.detail)
        }
      }
    } catch {
      // non-JSON error body — keep statusText
    }
    throw new ApiError(res.status, detail, raw)
  }
  if (res.status === 204) return undefined as T
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const api = {
  // meta (unauthenticated) — app version for the footer
  health: () => req<{ status: string; version: string }>('GET', '/health'),
  // meta — running version + (only when app.update_check is on) the latest release
  update: () =>
    req<{ current: string; latest: string; update_available: boolean; url: string }>(
      'GET',
      '/update',
    ),

  // auth
  authStatus: () => req<AuthStatus>('GET', '/auth/status'),
  me: () => req<UserInfo>('GET', '/auth/me'),
  setup: (username: string, password: string, timezone: string) =>
    req<UserInfo>('POST', '/auth/setup', { username, password, timezone }),
  login: (username: string, password: string) =>
    req<UserInfo>('POST', '/login', { username, password }),
  logout: () => req<void>('POST', '/logout'),
  updateAccount: (currentPassword: string, username: string, password?: string) =>
    req<UserInfo>('PUT', '/account', {
      current_password: currentPassword,
      username,
      password: password || null,
    }),

  // homepage
  status: () => req<StatusResponse>('GET', '/status'),
  // The read-only widget/monitoring view (API-key auth for external dashboards, session auth
  // works too). Not what the UI polls — that's /status.
  dashboard: () => req<DashboardResponse>('GET', '/dashboard'),
  getConfig: () => req<Config>('GET', '/config'),
  putConfig: (config: Config) => req<Config>('PUT', '/config', config),
  // Raw config.yaml (redacted) for the Advanced tab's editor; PUT goes through the same
  // validation as putConfig, so a rejected document leaves the stored config untouched.
  getConfigYaml: () => req<{ yaml: string }>('GET', '/config/yaml'),
  putConfigYaml: (text: string) => req<Config>('PUT', '/config/yaml', { yaml: text }),
  generateApiKey: () => req<{ api_key: string }>('POST', '/config/api-key'),
  deleteApiKey: () => req<void>('DELETE', '/config/api-key'),
  // vmids collide across PVEs, so the backend refuses to guess which one you meant.
  guests: (pve: string) => req<GuestInfo[]>('GET', `/guests?pve=${encodeURIComponent(pve)}`),
  toggleScheduler: (enabled: boolean) =>
    req<ToggleResponse>('POST', '/scheduler/toggle', { enabled }),
  notifyTest: () => req<NotifyTestResult>('POST', '/notify/test'),
  logs: (limit = 100) => req<LogLine[]>('GET', `/logs?limit=${limit}`),
  // `route` filters on the recorded route id, so history stays readable after a route is
  // deleted (the chip disappears, the rows don't).
  runs: (limit = 50, route?: string) =>
    req<RunSummary[]>(
      'GET',
      `/runs?limit=${limit}${route ? `&route=${encodeURIComponent(route)}` : ''}`,
    ),
  run: (id: number) => req<RunDetail>('GET', `/runs/${id}`),
  // Without `run` this follows the newest run that has lines — the live tail. With it, that
  // run's lines: a history row expands a *finished* run, which is never the newest.
  taskLog: (after = 0, run?: number) =>
    req<TaskLogResponse>('GET', `/tasklog?after=${after}${run ? `&run=${run}` : ''}`),
  // 202 = accepted, not stopped: cancellation is cooperative, poll GET /runs/{id} for the end.
  // Note the asymmetry with runRoute below — this one takes power_off *directly*.
  stopRun: (runId: number, powerOff: boolean) =>
    req<{ run_id: number }>('POST', `/runs/${runId}/stop`, { power_off: powerOff }),

  // routes
  routes: () => req<Route[]>('GET', '/routes'),
  createRoute: (route: Route) => req<Route>('POST', '/routes', route),
  // The body's id wins: renaming a route is a delete + create as far as history is concerned.
  updateRoute: (id: string, route: Route) =>
    req<Route>('PUT', `/routes/${encodeURIComponent(id)}`, route),
  deleteRoute: (id: string) => req<void>('DELETE', `/routes/${encodeURIComponent(id)}`),
  // 202: runs execute one at a time, so `queued` > 0 means it is waiting its turn. `keep_on`
  // is inverted relative to stopRun's `power_off` — the backend takes both, spelled this way.
  runRoute: (id: string, keepOn: boolean) =>
    req<RunQueued>('POST', `/routes/${encodeURIComponent(id)}/run`, { keep_on: keepOn }),

  // devices
  devices: () => req<DeviceLists>('GET', '/devices'),
  // A secret sent as REDACTED resolves against the stored one on PUT and 422s on POST, so a
  // "duplicate this device" affordance must blank the token field, not echo the mask.
  createDevice: (kind: DeviceKind, body: Record<string, unknown>) =>
    req<Record<string, unknown>>('POST', `/devices/${kind}`, body),
  updateDevice: (kind: DeviceKind, id: string, body: Record<string, unknown>) =>
    req<Record<string, unknown>>('PUT', `/devices/${kind}/${encodeURIComponent(id)}`, body),
  // 409 carries {message, routes[]} — the removal guard lists the routes still using it.
  deleteDevice: (kind: DeviceKind, id: string) =>
    req<void>('DELETE', `/devices/${kind}/${encodeURIComponent(id)}`),
  // A failure is a 502 with the reason, not a 200 with ok:false.
  testDevice: (kind: DeviceKind, id: string) =>
    req<DeviceTestResult>('POST', `/devices/${kind}/${encodeURIComponent(id)}/test`),
  pbsPower: (id: string, action: 'wake' | 'poweroff') =>
    req<{ ok: boolean }>('POST', `/devices/pbss/${encodeURIComponent(id)}/power`, { action }),
  // One-off GC / verify on a box rather than a route ("Run GC" / "Run verify" on the homepage).
  runMaintenance: (id: string, action: 'gc' | 'verify', keepOn: boolean) =>
    req<MaintenanceQueued>('POST', `/devices/pbss/${encodeURIComponent(id)}/${action}`, {
      keep_on: keepOn,
    }),

  // wizard
  wizardPveConnect: (body: Record<string, unknown>) =>
    req<PveConnectResult>('POST', '/wizard/pve/connect', body),
  wizardStorageDerive: (body: Record<string, unknown>) =>
    req<PbsDerive>('POST', '/wizard/storage/derive', body),
  wizardPbsCheck: (host: string, port: number) =>
    req<{ reachable: boolean; fingerprint: string | null }>('POST', '/wizard/pbs/check', {
      host,
      port,
    }),
  wizardPbsProvision: (body: Record<string, unknown>) =>
    req<{ id: string; secret: string }>('POST', '/wizard/pbs/provision', body),
  wizardInterfaces: () => req<NetInterface[]>('GET', '/wizard/interfaces'),
  wizardDetectMac: (host: string) =>
    req<{ mac: string | null }>('POST', '/wizard/wol/detect-mac', { host }),
  // Get-or-create: `created` is false when the key already on disk was reused, which is what
  // adding a second PBS does — regenerating would strand the first one's authorized_keys.
  wizardKeygen: () =>
    req<{
      public_key: string
      authorized_keys_line: string
      key_path: string
      created: boolean
    }>('POST', '/wizard/ssh/keygen'),
  wizardSshInstall: (body: Record<string, unknown>) =>
    req<{ installed: boolean }>('POST', '/wizard/ssh/install', body),
  wizardSshHostkey: (host: string, port = 22) =>
    req<{ key_type: string; key_base64: string; fingerprint: string }>(
      'POST', '/wizard/ssh/hostkey', { host, port },
    ),
  wizardSshTrust: (body: Record<string, unknown>) =>
    req<{ trusted: boolean }>('POST', '/wizard/ssh/trust', body),
  // Stateless: tests a MAC the wizard has just detected, before there is a device to save it
  // on. Waking a *configured* box is pbsPower(id, 'wake').
  wizardWolTest: (mac: string, host = '', iface = '') =>
    req<{ sent: boolean; mac: string; broadcast: string }>('POST', '/wizard/wol/test', {
      mac,
      host,
      iface,
    }),
}
