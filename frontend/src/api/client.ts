// Thin typed wrapper over the JSON API. Same-origin session cookie auth; a non-2xx
// response throws ApiError carrying the status and the backend's `detail`.
import type {
  AuthStatus,
  Config,
  GuestInfo,
  LogLine,
  NetInterface,
  NotifyTestResult,
  PbsDerive,
  PveConnectResult,
  RunDetail,
  RunSummary,
  StatusResponse,
  TaskLogResponse,
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

  // dashboard
  status: () => req<StatusResponse>('GET', '/status'),
  getConfig: () => req<Config>('GET', '/config'),
  putConfig: (config: Config) => req<Config>('PUT', '/config', config),
  // Raw config.yaml (redacted) for the Advanced tab's editor; PUT goes through the same
  // validation as putConfig, so a rejected document leaves the stored config untouched.
  getConfigYaml: () => req<{ yaml: string }>('GET', '/config/yaml'),
  putConfigYaml: (text: string) => req<Config>('PUT', '/config/yaml', { yaml: text }),
  generateApiKey: () => req<{ api_key: string }>('POST', '/config/api-key'),
  deleteApiKey: () => req<void>('DELETE', '/config/api-key'),
  guests: () => req<GuestInfo[]>('GET', '/guests'),
  toggleScheduler: (enabled: boolean) =>
    req<{ enabled: boolean; next_run: string | null }>('POST', '/scheduler/toggle', { enabled }),
  runBackup: (keepOn: boolean) => req<{ run_id: number }>('POST', '/backup/run', { keep_on: keepOn }),
  runGc: (keepOn: boolean) => req<{ run_id: number }>('POST', '/gc/run', { keep_on: keepOn }),
  powerOn: () => req<{ ok: boolean }>('POST', '/power/on'),
  powerOff: () => req<{ ok: boolean }>('POST', '/power/off'),
  wolTest: () => req<{ sent: boolean; mac: string }>('POST', '/wol/test'),
  notifyTest: () => req<NotifyTestResult>('POST', '/notify/test'),
  logs: (limit = 100) => req<LogLine[]>('GET', `/logs?limit=${limit}`),
  runs: (limit = 50) => req<RunSummary[]>('GET', `/runs?limit=${limit}`),
  run: (id: number) => req<RunDetail>('GET', `/runs/${id}`),
  taskLog: (after = 0) => req<TaskLogResponse>('GET', `/tasklog?after=${after}`),

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
  wizardKeygen: () =>
    req<{ public_key: string; authorized_keys_line: string; key_path: string }>(
      'POST',
      '/wizard/ssh/keygen',
    ),
  wizardSshInstall: (body: Record<string, unknown>) =>
    req<{ installed: boolean }>('POST', '/wizard/ssh/install', body),
  wizardSshHostkey: (host: string, port = 22) =>
    req<{ key_type: string; key_base64: string; fingerprint: string }>(
      'POST', '/wizard/ssh/hostkey', { host, port },
    ),
  wizardSshTrust: (body: Record<string, unknown>) =>
    req<{ trusted: boolean }>('POST', '/wizard/ssh/trust', body),
  wizardReset: () => req<{ ok: boolean }>('POST', '/wizard/reset'),
}
