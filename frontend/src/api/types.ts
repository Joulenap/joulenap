// Shapes mirroring the backend API (backend/app/api, backend/app/config.py).

export interface AuthStatus {
  setup_needed: boolean
  authenticated: boolean
  username: string | null
}

export interface UserInfo {
  username: string
}

export interface RunSummary {
  id: number
  kind: string // cycle | sync | gc | verify | monitor
  trigger: string
  status: string
  started_at: string
  finished_at: string | null
  // The route this run came from. Both null for an ad-hoc PBS maintenance run; the name is
  // denormalised on the row, so a deleted route's history still reads correctly.
  route_id: string | null
  route_name: string | null
  guests_ok: number | null
  error: string | null
}

export interface StepInfo {
  name: string
  status: string
  started_at: string
  finished_at: string | null
  detail: string | null
}

export interface LogLine {
  id: number
  run_id: number | null
  ts: string
  level: string
  message: string
}

export interface RunDetail extends RunSummary {
  steps: StepInfo[]
  logs: LogLine[]
}

export interface TaskLogLine {
  id: number
  step: string // backup | gc | verify
  source: string // pve | pbs
  text: string
  ts: string
}

export interface TaskLogResponse {
  run_id: number | null
  lines: TaskLogLine[]
}

export interface DatastoreInfo {
  used: number
  total: number
  used_pct: number
}

export interface LoadInfo {
  cpu: number
  mem: number
  uptime: number // seconds since the PBS booted
}

export interface PveState {
  id: string
  online: boolean
}

export interface PbsState {
  id: string
  online: boolean
  managed_power: boolean
  // How many runs hold this box's power lease. > 0 means the ⏻ button must be disabled.
  holders: number
  datastore: DatastoreInfo | null
  load: LoadInfo | null
}

export interface RunningRun {
  run_id: number
  kind: string
  started_at: string
  route_id: string | null
  route_name: string | null
}

export interface QueuedRunInfo {
  // The queue key: a route id, or `pbs:<id>:gc` for an ad-hoc maintenance run.
  key: string
  route_id: string | null
  pbs_id: string | null
}

// GET /api/status carries the route name; POST /api/scheduler/toggle does not — two shapes.
export interface NextRunInfo {
  route_id: string
  route_name: string
  at: string
}

/** Describes *Joulenap's* state, not a PBS's: with several backup servers "on/off" is no
 *  longer a single fact, so the pill says what the app is doing and the per-device rows
 *  carry the rest. */
export interface StatusResponse {
  state: 'running' | 'idle' | 'paused'
  scheduler_enabled: boolean
  running: RunningRun | null
  queued: QueuedRunInfo[]
  // Every armed route's next fire, soonest first.
  next_runs: NextRunInfo[]
  pves: PveState[]
  pbss: PbsState[]
  last_run: RunSummary | null
  // Set when the 0.9 -> 1.0 config migration was refused at startup: the config in use has no
  // devices and no routes, so the UI must say why instead of looking like a fresh install.
  config_error: string | null
}

export interface GuestInfo {
  vmid: number
  name: string
  type: string // qemu | lxc
  status: string
  node: string // always set — a standalone PVE is a one-node cluster to the API
  last_backup: string | null
  // Which backup servers hold a copy, sorted. More than one when a sync route copied it on.
  pbs_ids: string[]
}

// --- dashboard (frozen public contract, also read by Homepage/Homarr/Dashy/Glance) ---------

export interface DashboardRoute {
  id: string
  name: string
  kind: RouteKind
  enabled: boolean
  next_run: string | null
  last_run_status: 'success' | 'failed' | 'never'
  last_run_time: string | null
}

export interface DashboardPbs {
  id: string
  state: 'sleeping' | 'online' | 'backing_up'
  datastore_used_pct: number | null
  datastore_used_bytes: number | null
  datastore_total_bytes: number | null
}

export interface DashboardResponse {
  state: 'idle' | 'running' | 'paused'
  routes: DashboardRoute[]
  pbss: DashboardPbs[]
}

// --- action results ----------------------------------------------------------

export interface RunQueued {
  route_id: string
  // How many runs are ahead of this one; 0 means it starts immediately.
  queued: number
}

export interface MaintenanceQueued {
  pbs_id: string
  action: string
  queued: number
}

export interface ToggleResponse {
  enabled: boolean
  next_runs: { route_id: string; at: string }[]
}

export interface DeviceTestResult {
  ok: boolean
  // Free-form one-liner for the card's status row (a version string, a datastore usage).
  detail: string
}

// --- config (matches backend/app/config.py) ---------------------------------

// What GET /config and GET /devices send back in place of a stored secret. Never a value:
// echo it on a PUT to keep the secret, send "" to clear it, anything else to set it. A
// create (POST) rejects it with 422 — there is nothing to resolve it against.
export const REDACTED = '***REDACTED***'

export interface RetentionConfig {
  keep_last: number
  keep_daily: number
  keep_weekly: number
  keep_monthly: number
  keep_yearly: number
}

export interface MaintenanceConfig {
  history: { retention_days: number }
}

// --- devices -----------------------------------------------------------------

export interface PveDevice {
  id: string // slug: ^[a-z0-9][a-z0-9._-]*$
  host: string
  port: number
  verify_tls: boolean
  api_token_id: string
  api_token_secret: string
  // pbs device id -> the PVE storage that points at it. A backup route's target must appear
  // here for every one of its source PVEs.
  storages: Record<string, string>
}

export interface PbsExternalConfig {
  // "External schedules" mode: PVE/PBS run their own jobs; Joulenap wakes, watches, powers off.
  first_task_wait: number
  idle_wait: number
}

export interface PbsDevice {
  id: string
  host: string
  port: number
  datastore: string
  fingerprint: string
  api_token_id: string
  api_token_secret: string
  // false = an always-on box: no WoL, no SSH power-off, and no ⏻ button.
  managed_power: boolean
  mac: string
  wol_broadcast_iface: string
  wait_timeout: number
  wol_retries: number
  poweroff_task_wait: number
  ssh_user: string
  ssh_key_path: string
  external: PbsExternalConfig
}

export type DeviceKind = 'pves' | 'pbss'

export interface DeviceLists {
  pves: PveDevice[]
  pbss: PbsDevice[]
}

// --- routes ------------------------------------------------------------------

export type RouteKind = 'backup' | 'sync' | 'external' | 'verify'

export interface RouteGuests {
  mode: 'all' | 'include'
  list: number[]
}

export interface RouteSource {
  pve: string
  guests: RouteGuests
}

export interface RouteSchedule {
  time: string // HH:MM
  days: boolean[] // exactly 7, Mon..Sun, at least one true
  // A 5-field crontab preserved verbatim from a 0.9 migration. When non-empty it WINS over
  // time/days and the UI shows it read-only.
  cron: string
}

export interface RouteOptions {
  // Inert on non-backup kinds, but still accepted, so one payload shape covers every route.
  mode: 'snapshot' | 'suspend' | 'stop'
  bwlimit: number // KiB/s, 0 = unlimited
  min_free_percent: number
  gc: boolean
  verify_after: boolean
  reverify_days: number
}

export interface Route {
  id: string
  name: string
  color: string // #rrggbb
  enabled: boolean
  notify: boolean
  kind: RouteKind
  sources: RouteSource[] // backup only
  source_pbs: string // sync only
  target: string // pbs id
  schedule: RouteSchedule
  retention: RetentionConfig
  sync_direction: 'pull' | 'push'
  options: RouteOptions
}

export interface AppConfig {
  scheduler_enabled: boolean
  language: string
  theme: 'dark' | 'light'
  port: number
  timezone: string
  secret_key: string
  api_key: string
  update_check: boolean
  auth: { username: string; password_hash: string }
  session: { https_only: boolean; max_age_days: number }
}

export interface TelegramConfig {
  enabled: boolean
  bot_token: string
  chat_id: string
}

export interface NtfyConfig {
  enabled: boolean
  url: string
  topic: string
}

export interface EmailConfig {
  enabled: boolean
  smtp_host: string
  smtp_port: number
  smtp_user: string
  smtp_password: string
  from_addr: string
  to_addr: string
}

export interface DiscordConfig {
  enabled: boolean
  webhook_url: string
}

export interface NotificationsConfig {
  on_success: boolean
  on_failure: boolean
  telegram: TelegramConfig
  ntfy: NtfyConfig
  email: EmailConfig
  discord: DiscordConfig
  custom_urls: string[]
}

export interface Config {
  app: AppConfig
  pves: PveDevice[]
  pbss: PbsDevice[]
  routes: Route[]
  maintenance: MaintenanceConfig
  notifications: NotificationsConfig
}

// --- wizard ------------------------------------------------------------------

export interface WizardNode {
  node: string
  status: string | null
}

export interface WizardStorage {
  storage: string
  host: string
  port: number
  datastore: string
  fingerprint: string
}

export interface PveConnectResult {
  connected: boolean
  version: string | null
  nodes: WizardNode[]
  storages: WizardStorage[]
  token: { id: string; secret: string } | null
}

export interface PbsDerive {
  host: string
  port: number
  datastore: string
  fingerprint: string
}

export interface NetInterface {
  name: string
  address: string
  netmask: string
  broadcast: string
}

// --- notifications ------------------------------------------------------------

export type ChannelOutcome = {
  channel: string
  ok: boolean
  error: string | null
}

export type NotifyTestResult = {
  channels: ChannelOutcome[]
}
