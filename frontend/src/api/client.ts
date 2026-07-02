// Thin typed wrapper over the JSON API. Same-origin session cookie auth; a non-2xx
// response throws ApiError carrying the status and the backend's `detail`.
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
  TaskLogResponse,
  UserInfo,
} from './types'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch('/api' + path, {
    method,
    credentials: 'same-origin',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let detail: string = res.statusText
    try {
      const j = await res.json()
      if (j && typeof j.detail !== 'undefined') {
        detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
      }
    } catch {
      // non-JSON error body — keep statusText
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const api = {
  // meta (unauthenticated) — app version for the footer
  health: () => req<{ status: string; version: string }>('GET', '/health'),

  // auth
  authStatus: () => req<AuthStatus>('GET', '/auth/status'),
  me: () => req<UserInfo>('GET', '/auth/me'),
  setup: (username: string, password: string, timezone: string) =>
    req<UserInfo>('POST', '/auth/setup', { username, password, timezone }),
  login: (username: string, password: string) =>
    req<UserInfo>('POST', '/login', { username, password }),
  logout: () => req<void>('POST', '/logout'),
  updateAccount: (username: string, password?: string) =>
    req<UserInfo>('PUT', '/account', { username, password: password || null }),

  // dashboard
  status: () => req<StatusResponse>('GET', '/status'),
  getConfig: () => req<Config>('GET', '/config'),
  putConfig: (config: Config) => req<Config>('PUT', '/config', config),
  guests: () => req<GuestInfo[]>('GET', '/guests'),
  toggleScheduler: (enabled: boolean) =>
    req<{ enabled: boolean; next_run: string | null }>('POST', '/scheduler/toggle', { enabled }),
  runBackup: () => req<{ run_id: number }>('POST', '/backup/run'),
  runGc: () => req<{ run_id: number }>('POST', '/gc/run'),
  powerOn: () => req<{ ok: boolean }>('POST', '/power/on'),
  powerOff: () => req<{ ok: boolean }>('POST', '/power/off'),
  wolTest: () => req<{ sent: boolean; mac: string }>('POST', '/wol/test'),
  notifyTest: () => req<{ sent: boolean; channels: number }>('POST', '/notify/test'),
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
  wizardReset: () => req<{ ok: boolean }>('POST', '/wizard/reset'),
}
