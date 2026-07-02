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
  kind: string
  trigger: string
  status: string
  started_at: string
  finished_at: string | null
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

export interface StatusResponse {
  scheduler_enabled: boolean
  schedule: string
  next_run: string | null
  job_running: boolean
  pbs_online: boolean
  last_run: RunSummary | null
  datastore: DatastoreInfo | null
  load: LoadInfo | null
}

export interface GuestInfo {
  vmid: number
  name: string
  type: string
  status: string
  last_backup: string | null
}

// --- config (matches backend/app/config.py) ---------------------------------

export interface GuestsConfig {
  mode: 'all' | 'include' | 'exclude'
  auto_include_new: boolean
  list: number[]
}

export interface RetentionConfig {
  keep_last: number
  keep_daily: number
  keep_weekly: number
  keep_monthly: number
  keep_yearly: number
}

export interface BackupConfig {
  enabled: boolean
  schedule: string
  mode: 'snapshot' | 'suspend' | 'stop'
  bwlimit: number
  min_free_percent: number
  guests: GuestsConfig
  retention: RetentionConfig
}

export interface MaintenanceConfig {
  gc: { enabled: boolean }
  verify: { enabled: boolean; schedule: string; after_backup: boolean; reverify_days: number }
  history: { retention_days: number }
}

export interface PveConfig {
  host: string
  port: number
  node: string
  verify_tls: boolean
  api_token_id: string
  api_token_secret: string
  storage_id: string
}

export interface PbsConfig {
  host: string
  port: number
  datastore: string
  fingerprint: string
  api_token_id: string
  api_token_secret: string
  mac: string
  wol_broadcast_iface: string
  wait_timeout: number
  wol_retries: number
  poweroff_task_wait: number
  ssh_user: string
  ssh_key_path: string
}

export interface AppConfig {
  language: string
  theme: 'dark' | 'light'
  port: number
  timezone: string
  secret_key: string
  auth: { username: string; password_hash: string }
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
  pve: PveConfig
  pbs: PbsConfig
  backup: BackupConfig
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
