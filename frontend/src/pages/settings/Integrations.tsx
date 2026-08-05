import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, api } from '../../api/client'
import { ConfirmModal, type ConfirmState } from '../../components/ConfirmModal'
import { useConfig } from '../../config/ConfigContext'
import { copyToClipboard } from '../../utils/clipboard'

type Dashboard = 'homepage' | 'glance' | 'homarr' | 'dashy'
const DASHBOARDS: Dashboard[] = ['homepage', 'glance', 'homarr', 'dashy']

const ns = 'settings.integrations'

type TFunc = (key: string, opts?: Record<string, unknown>) => string

// The endpoint URL uses the current origin so the snippet is copy-paste-ready.
function endpointUrl(): string {
  return `${window.location.origin}/api/dashboard`
}

/**
 * Widget config for each supported dashboard.
 *
 * **Rewritten for 1.0.** `GET /api/dashboard` used to answer a flat single-PBS object
 * (`pbs_state`, `next_run`, `datastore_used_pct`); M07 turned it into
 * `{state, routes[], pbss[]}` because with several routes and several backup servers there
 * is no single "next run" or "datastore" to report. Every snippet below therefore indexes
 * into a list — `routes.0` is the first configured route, not "the" route.
 *
 * **Only the lines addressed to the reader go through `t()`.** Everything else stays
 * English on purpose: the YAML/JSON keys are syntax the target tool parses, the `label:`
 * values render in the user's *own* dashboard rather than in Joulenap, and the Homarr
 * navigation ("Management -> Custom Widgets", "API Key (Header)", "Display Type") names
 * controls in Homarr's own English UI — translating those would send the user hunting for
 * a menu item that does not exist.
 */
function snippet(dash: Dashboard, url: string, key: string, t: TFunc): string {
  const iconUrl = `${window.location.origin}/assets/joulenap-icon.svg`
  const href = window.location.origin
  switch (dash) {
    case 'homepage':
      return `- Joulenap:
    icon: ${iconUrl}
    href: ${href}
    widget:
      type: customapi
      url: ${url}
      headers:
        X-API-Key: ${key}
      mappings:
        - field: state
          label: Joulenap
        - field: routes.0.name
          label: Route
        - field: routes.0.next_run
          label: Next run
          format: relativeDate
        - field: routes.0.last_run_status
          label: Last run
        - field: pbss.0.datastore_used_pct
          label: Datastore
          format: percent
${t(`${ns}.snippetIndexNote`)}`
    case 'glance':
      return `- type: custom-api
  title: Joulenap
  url: ${url}
  headers:
    X-API-Key: ${key}
  template: |
    <div>State: {{ .JSON.String "state" }}</div>
    {{ range .JSON.Array "routes" }}
      <div>{{ .String "name" }}: {{ .String "last_run_status" }}
           (next {{ .String "next_run" }})</div>
    {{ end }}
    {{ range .JSON.Array "pbss" }}
      <div>{{ .String "id" }}: {{ .String "state" }}
           — {{ .Int "datastore_used_pct" }}%</div>
    {{ end }}`
    case 'homarr':
      return `# Homarr v1.65+: Management -> Custom Widgets -> Add -> Custom API
URL: ${url}
HTTP Method: GET
Authentication: API Key (Header)
  Header Name: X-API-Key
  Value: ${key}
  ${t(`${ns}.snippetQueryAuth`)}
   ${url}?key=${key})
Display Type: Key Value

${t(`${ns}.snippetFields`)}
  state                            idle | running | paused
  routes[]  id, name, kind, enabled, next_run,
            last_run_status, last_run_time
  pbss[]    id, state, datastore_used_pct,
            datastore_used_bytes, datastore_total_bytes

${t(`${ns}.snippetIndexHint`)}`
    case 'dashy':
      return `- type: customapi
  options:
    url: ${url}
    headers:
      X-API-Key: ${key}
    mappings:
      - field: routes.0.name
        label: Route
      - field: routes.0.next_run
        label: Next run
        format: relativeDate
      - field: routes.0.last_run_status
        label: Last run
      - field: pbss.0.datastore_used_pct
        label: Datastore
        format: percent
${t(`${ns}.snippetCors`, { url: `${url}?key=${key}` })}`
  }
}

// Straight from backend/app/api/metrics.py. Labels matter as much as the names: with routes
// every series is per-route or per-PBS, so a query without them sums unrelated boxes.
const METRICS: { name: string; labels?: string }[] = [
  { name: 'joulenap_build_info', labels: 'version' },
  { name: 'joulenap_scheduler_enabled' },
  { name: 'joulenap_job_running' },
  { name: 'joulenap_queued_runs' },
  { name: 'joulenap_pbs_online', labels: 'pbs' },
  { name: 'joulenap_pbs_cpu_percent', labels: 'pbs' },
  { name: 'joulenap_pbs_memory_percent', labels: 'pbs' },
  { name: 'joulenap_pbs_uptime_seconds', labels: 'pbs' },
  { name: 'joulenap_datastore_used_bytes', labels: 'pbs, datastore' },
  { name: 'joulenap_datastore_total_bytes', labels: 'pbs, datastore' },
  { name: 'joulenap_route_next_run_timestamp_seconds', labels: 'route' },
  { name: 'joulenap_route_last_run_timestamp_seconds', labels: 'route' },
  { name: 'joulenap_route_last_run_success', labels: 'route' },
  { name: 'joulenap_route_last_run_duration_seconds', labels: 'route' },
  { name: 'joulenap_route_last_run_guests', labels: 'route' },
  { name: 'joulenap_guest_last_backup_timestamp_seconds', labels: 'pve, pbs, vmid' },
  { name: 'joulenap_runs_recent', labels: 'status' },
]

function promSnippet(key: string): string {
  return `# prometheus.yml
scrape_configs:
  - job_name: joulenap
    metrics_path: /metrics
    params: { key: ["${key}"] }
    scrape_interval: 60s
    static_configs:
      - targets: ["${window.location.host}"]`
}

/** Copy button that reports the outcome in its own label, as the mockup does. */
function CopyButton({ text, small }: { text: string; small?: boolean }) {
  const { t } = useTranslation()
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  return (
    <button
      type="button"
      className={`btn${small ? ' btn-sm' : ''}`}
      onClick={async () => {
        const ok = await copyToClipboard(text)
        setState(ok ? 'copied' : 'failed')
        setTimeout(() => setState('idle'), ok ? 1500 : 3000)
      }}
    >
      {state === 'copied' ? t(`${ns}.copied`) : state === 'failed' ? t(`${ns}.copyFailed`) : t(`${ns}.copy`)}
    </button>
  )
}

export function Integrations() {
  const { t } = useTranslation()
  const { config, reload } = useConfig()
  const [freshKey, setFreshKey] = useState<string | null>(null)
  const [dash, setDash] = useState<Dashboard>('homepage')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)

  // "A key is configured" comes from the shared config (api_key is redacted to a non-empty
  // sentinel when set) rather than a standalone fetch; reload() after mutating it so the
  // shared cache never goes stale (FE-M10).
  const enabled = Boolean(config?.app.api_key)

  async function run(fn: () => Promise<void>) {
    setBusy(true)
    setErr(null)
    try {
      await fn()
      await reload()
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
  }

  const doGenerate = () =>
    run(async () => {
      setFreshKey((await api.generateApiKey()).api_key)
    })

  const doDisable = () =>
    run(async () => {
      await api.deleteApiKey()
      setFreshKey(null)
    })

  const keyForSnippet = freshKey ?? t(`${ns}.keyPlaceholder`)
  const code = snippet(dash, endpointUrl(), keyForSnippet, t)
  const prom = promSnippet(keyForSnippet)

  return (
    <div>
      <section className="panel">
        <div className="panel-hd">
          <h2>{t(`${ns}.title`)}</h2>
          <span className="panel-hint">{t(`${ns}.subtitle`)}</span>
        </div>
        <div className="panel-bd stack tight">
          <div className="url-row">
            <input
              type="text"
              className="in-mono"
              readOnly
              value={freshKey ?? (enabled ? t(`${ns}.keyHidden`) : t(`${ns}.keyNone`))}
              aria-label={t(`${ns}.title`)}
            />
            {freshKey && <CopyButton text={freshKey} />}
            <button
              type="button"
              className="btn btn-accent"
              disabled={busy}
              onClick={() =>
                enabled
                  ? setConfirm({
                      title: t(`${ns}.regenerateConfirmTitle`),
                      message: t(`${ns}.regenerateConfirmBody`),
                      confirmLabel: t(`${ns}.regenerate`),
                      danger: true,
                      icon: '⟳',
                      onConfirm: () => void doGenerate(),
                    })
                  : void doGenerate()
              }
            >
              {enabled ? t(`${ns}.regenerate`) : t(`${ns}.generate`)}
            </button>
            {enabled && (
              <button
                type="button"
                className="btn btn-danger-ghost"
                disabled={busy}
                onClick={() =>
                  setConfirm({
                    title: t(`${ns}.disableConfirmTitle`),
                    message: t(`${ns}.disableConfirmBody`),
                    confirmLabel: t(`${ns}.disable`),
                    danger: true,
                    icon: '⨯',
                    onConfirm: () => void doDisable(),
                  })
                }
              >
                {t(`${ns}.disable`)}
              </button>
            )}
          </div>
          <span className="help">{t(`${ns}.keyShownOnce`)}</span>
          {err && <span className="err-note">{err}</span>}
        </div>
      </section>

      <section className="panel">
        <div className="panel-hd">
          <h2>{t(`${ns}.dashboardLabel`)}</h2>
          <div className="seg seg-inline">
            {DASHBOARDS.map((d) => (
              <button
                key={d}
                type="button"
                className={`seg-btn${dash === d ? ' on' : ''}`}
                style={{ textTransform: 'capitalize' }}
                onClick={() => setDash(d)}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
        <div className="panel-bd stack tight">
          <pre className="yaml">{code}</pre>
          <div className="save-row bare">
            <span className="help spacer">{t(`${ns}.snippetHint`)}</span>
            <CopyButton text={code} />
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-hd">
          <h2>{t(`${ns}.promTitle`)}</h2>
          <span className="panel-hint">{t(`${ns}.promSubtitle`)}</span>
        </div>
        <div className="panel-bd stack tight">
          <pre className="yaml">{prom}</pre>
          <span className="help">{t(`${ns}.promMetricsHint`)}</span>
          <pre className="yaml">
            {METRICS.map((m) => (m.labels ? `${m.name}{${m.labels}}` : m.name)).join('\n')}
          </pre>
          <div className="save-row bare">
            <span className="spacer" />
            <CopyButton text={prom} />
          </div>
        </div>
      </section>

      <ConfirmModal state={confirm} onCancel={() => setConfirm(null)} />
    </div>
  )
}
