import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { ConfirmModal, type ConfirmState } from '../../components/ConfirmModal'
import { useConfig } from '../../config/ConfigContext'
import { c, ghostBtn, labelStyle, panelStyle, primaryBtn } from '../../theme'
import { copyToClipboard } from '../../utils/clipboard'

type Dashboard = 'homepage' | 'glance' | 'homarr' | 'dashy'
const DASHBOARDS: Dashboard[] = ['homepage', 'glance', 'homarr', 'dashy']

// The endpoint URL uses the current origin so the snippet is copy-paste-ready.
function endpointUrl(): string {
  return `${window.location.origin}/api/dashboard`
}

function snippet(dash: Dashboard, url: string, key: string): string {
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
        - field: pbs_state
          label: PBS
        - field: next_run
          label: Next backup
          format: relativeDate
        - field: last_run_status
          label: Last run
        - field: datastore_used_pct
          label: Datastore
          format: percent`
    case 'glance':
      return `- type: custom-api
  title: Joulenap
  url: ${url}
  headers:
    X-API-Key: ${key}
  template: |
    <div>PBS: {{ .JSON.String "pbs_state" }}</div>
    <div>Next: {{ .JSON.String "next_run" }}</div>
    <div>Last run: {{ .JSON.String "last_run_status" }}</div>
    <div>Datastore: {{ .JSON.Int "datastore_used_pct" }}%</div>`
    case 'homarr':
      return `# Homarr v1.65+: Management -> Custom Widgets -> Add -> Custom API
URL: ${url}
HTTP Method: GET
Authentication: API Key (Header)
  Header Name: X-API-Key
  Value: ${key}
  (or API Key (Query), param "key", if your version lacks header auth:
   ${url}?key=${key})
Display Type: Key Value

Fields available: pbs_state, next_run, last_run_status, last_run_time,
                  datastore_used_pct, datastore_used_bytes, datastore_total_bytes`
    case 'dashy':
      return `- type: customapi
  options:
    url: ${url}
    headers:
      X-API-Key: ${key}
    mappings:
      - field: pbs_state
        label: PBS
      - field: next_run
        label: Next backup
        format: relativeDate
      - field: last_run_status
        label: Last run
      - field: datastore_used_pct
        label: Datastore
        format: percent
# No CORS? set useProxy: true, or fall back to ${url}?key=${key}`
  }
}

export function Integrations() {
  const { t } = useTranslation()
  const { config, reload } = useConfig()
  const [freshKey, setFreshKey] = useState<string | null>(null)
  const [dash, setDash] = useState<Dashboard>('homepage')
  const [busy, setBusy] = useState(false)
  const [keyCopyState, setKeyCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const [snippetCopyState, setSnippetCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const [err, setErr] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)

  // Derive "a key is configured" from the shared config (api_key is redacted to a non-empty
  // sentinel when set) instead of a standalone fetch, and reload() after mutating it so the
  // shared cache never goes stale (FE-M10).
  const enabled = Boolean(config?.app.api_key)

  async function doGenerate() {
    setBusy(true)
    setErr(null)
    try {
      const { api_key } = await api.generateApiKey()
      setFreshKey(api_key)
      await reload()
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
  }

  async function doDisable() {
    setBusy(true)
    setErr(null)
    try {
      await api.deleteApiKey()
      setFreshKey(null)
      await reload()
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
  }

  const keyForSnippet = freshKey ?? t('settings.integrations.keyPlaceholder')
  const code = snippet(dash, endpointUrl(), keyForSnippet)

  async function copyKey() {
    if (!freshKey) return
    const ok = await copyToClipboard(freshKey)
    setKeyCopyState(ok ? 'copied' : 'failed')
    setTimeout(() => setKeyCopyState('idle'), ok ? 1500 : 3000)
  }

  async function copySnippet() {
    const ok = await copyToClipboard(code)
    setSnippetCopyState(ok ? 'copied' : 'failed')
    setTimeout(() => setSnippetCopyState('idle'), ok ? 1500 : 3000)
  }

  return (
    <div style={{ ...panelStyle, padding: '24px 26px', maxWidth: 640 }}>
      <span style={{ display: 'block', fontSize: 16, fontWeight: 700, marginBottom: 5 }}>
        {t('settings.integrations.title')}
      </span>
      <span style={{ display: 'block', fontSize: 13, color: c.textDim, lineHeight: 1.5, marginBottom: 20 }}>
        {t('settings.integrations.subtitle')}
      </span>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: enabled ? c.green : c.textDim }}>
          {enabled ? t('settings.integrations.statusEnabled') : t('settings.integrations.statusDisabled')}
        </span>
        <button
          onClick={() =>
            enabled
              ? setConfirm({
                  title: t('settings.integrations.regenerateConfirmTitle'),
                  message: t('settings.integrations.regenerateConfirmBody'),
                  confirmLabel: t('settings.integrations.regenerate'),
                  danger: true,
                  icon: '⟳',
                  onConfirm: doGenerate,
                })
              : doGenerate()
          }
          disabled={busy}
          style={{ ...primaryBtn, padding: '9px 18px' }}
        >
          {enabled ? t('settings.integrations.regenerate') : t('settings.integrations.generate')}
        </button>
        {enabled && (
          <button
            onClick={() =>
              setConfirm({
                title: t('settings.integrations.disableConfirmTitle'),
                message: t('settings.integrations.disableConfirmBody'),
                confirmLabel: t('settings.integrations.disable'),
                danger: true,
                icon: '⨯',
                onConfirm: doDisable,
              })
            }
            disabled={busy}
            style={{ ...ghostBtn, padding: '9px 18px' }}
          >
            {t('settings.integrations.disable')}
          </button>
        )}
      </div>

      {freshKey && (
        <div
          style={{
            background: c.inputBg,
            border: `1px solid ${c.border}`,
            borderRadius: 8,
            padding: '12px 14px',
            marginBottom: 18,
          }}
        >
          <div style={{ fontSize: 12, color: c.accent, marginBottom: 8 }}>
            {t('settings.integrations.keyShownOnce')}
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <code style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 13, wordBreak: 'break-all' }}>
              {freshKey}
            </code>
            <button onClick={() => void copyKey()} style={{ ...ghostBtn, padding: '6px 12px', flex: '0 0 auto' }}>
              {keyCopyState === 'copied' ? t('settings.integrations.copied') : t('settings.integrations.copy')}
            </button>
          </div>
          {keyCopyState === 'failed' && (
            <div style={{ fontSize: 12, color: c.red, marginTop: 8 }}>
              {t('settings.integrations.copyFailed')}
            </div>
          )}
        </div>
      )}

      {enabled && !freshKey && (
        <div style={{ fontSize: 12, color: c.textDim, marginBottom: 18 }}>
          {t('settings.integrations.keyHiddenNote')}
        </div>
      )}

      <div style={{ marginBottom: 10 }}>
        <span style={labelStyle}>{t('settings.integrations.dashboardLabel')}</span>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {DASHBOARDS.map((d) => (
            <button
              key={d}
              onClick={() => setDash(d)}
              style={{
                ...ghostBtn,
                padding: '6px 14px',
                textTransform: 'capitalize',
                background: dash === d ? 'rgba(232,131,15,.12)' : 'transparent',
                borderColor: dash === d ? c.accent : c.border,
              }}
            >
              {d}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <span style={{ ...labelStyle, marginBottom: 0 }}>{t('settings.integrations.snippetLabel')}</span>
        <button
          onClick={() => void copySnippet()}
          style={{ ...ghostBtn, padding: '4px 10px', fontSize: 12, flex: '0 0 auto' }}
        >
          {snippetCopyState === 'copied' ? t('settings.integrations.copied') : t('settings.integrations.copySnippet')}
        </button>
      </div>
      <pre
        style={{
          background: c.inputBg,
          border: `1px solid ${c.border}`,
          borderRadius: 8,
          padding: '12px 14px',
          overflowX: 'auto',
          fontSize: 12.5,
          fontFamily: "'IBM Plex Mono', monospace",
          color: c.textMid,
          margin: 0,
        }}
      >
        {code}
      </pre>
      <div style={{ fontSize: 12, color: c.textDim, marginTop: 8 }}>
        {t('settings.integrations.snippetHint')}
      </div>
      {snippetCopyState === 'failed' && (
        <div style={{ fontSize: 12, color: c.red, marginTop: 4 }}>
          {t('settings.integrations.copyFailed')}
        </div>
      )}

      {err && <div style={{ fontSize: 12, color: c.red, marginTop: 12 }}>{err}</div>}

      <ConfirmModal state={confirm} onCancel={() => setConfirm(null)} />
    </div>
  )
}
