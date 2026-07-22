import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../api/client'
import type { Config } from '../../api/types'
import { Dropdown } from '../../components/Dropdown'
import { Spinner } from '../../components/Spinner'
import { Toggle } from '../../components/Toggle'
import { useConfig } from '../../config/ConfigContext'
import { useRegisterDirty } from '../../shell/UnsavedGuard'
import { c, inputStyle, labelStyle, panelStyle, primaryBtn } from '../../theme'

// Config knobs the backend honours but no other screen exposes (re-review 11.8). Everything
// else remains reachable through the YAML editor below, which is code-split so CodeMirror
// only downloads when this tab is opened.
const YamlEditor = lazy(() => import('./YamlEditor'))

interface Draft {
  mode: 'snapshot' | 'suspend' | 'stop'
  bwlimit: number
  keep_last: number
  keep_yearly: number
  history_days: number
  session_days: number
  https_only: boolean
  port: number
}

function draftOf(config: Config): Draft {
  return {
    mode: config.backup.mode,
    bwlimit: config.backup.bwlimit,
    keep_last: config.backup.retention.keep_last,
    keep_yearly: config.backup.retention.keep_yearly,
    history_days: config.maintenance.history.retention_days,
    session_days: config.app.session.max_age_days,
    https_only: config.app.session.https_only,
    port: config.app.port,
  }
}

export function Advanced() {
  const { t } = useTranslation()
  const { config, save } = useConfig()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [savedNote, setSavedNote] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (config) setDraft(draftOf(config))
  }, [config])

  const dirty = useMemo(() => {
    if (!config || !draft) return false
    const stored = draftOf(config)
    return (Object.keys(stored) as (keyof Draft)[]).some((k) => stored[k] !== draft[k])
  }, [config, draft])
  useRegisterDirty(dirty)

  const ns = 'settings.advanced'

  if (!config || !draft) return null

  function patch(next: Partial<Draft>) {
    setDraft((d) => (d ? { ...d, ...next } : d))
    setSavedNote(false)
    setErr(null)
  }

  async function onSave() {
    if (!config || !draft) return
    setBusy(true)
    setErr(null)
    try {
      await save({
        ...config,
        app: {
          ...config.app,
          port: draft.port,
          session: { max_age_days: draft.session_days, https_only: draft.https_only },
        },
        backup: {
          ...config.backup,
          mode: draft.mode,
          bwlimit: draft.bwlimit,
          retention: {
            ...config.backup.retention,
            keep_last: draft.keep_last,
            keep_yearly: draft.keep_yearly,
          },
        },
        maintenance: {
          ...config.maintenance,
          history: { retention_days: draft.history_days },
        },
      })
      setSavedNote(true)
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
  }

  const numberField = (
    key: keyof Draft,
    label: string,
    hint: string,
    opts?: { min?: number; max?: number },
  ) => (
    <label style={{ display: 'block' }}>
      <span style={labelStyle}>{label}</span>
      <input
        type="number"
        value={String(draft[key])}
        min={opts?.min ?? 0}
        max={opts?.max}
        onChange={(e) => {
          let n = Math.floor(Number(e.target.value)) || 0
          n = Math.max(opts?.min ?? 0, n)
          if (opts?.max != null) n = Math.min(opts.max, n)
          patch({ [key]: n } as Partial<Draft>)
        }}
        style={{ ...inputStyle, maxWidth: 200 }}
      />
      <span style={{ display: 'block', fontSize: 11, color: c.textFaint, marginTop: 5, lineHeight: 1.5 }}>
        {hint}
      </span>
    </label>
  )

  const section = (title: string, body: React.ReactNode) => (
    <div
      style={{
        background: c.panelAlt,
        border: `1px solid ${c.borderSoft}`,
        borderRadius: 10,
        padding: '16px 18px',
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}
    >
      <span style={{ fontSize: 14, fontWeight: 600, color: c.textMid }}>{title}</span>
      {body}
    </div>
  )

  return (
    <div>
      <div style={{ ...panelStyle, padding: '24px 26px', maxWidth: 640 }}>
        <span style={{ display: 'block', fontSize: 16, fontWeight: 700, marginBottom: 5 }}>
          {t(`${ns}.title`)}
        </span>
        <span style={{ display: 'block', fontSize: 13, color: c.textDim, lineHeight: 1.5, marginBottom: 22 }}>
          {t(`${ns}.subtitle`)}
        </span>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {section(
            t(`${ns}.backupSection`),
            <>
              <label style={{ display: 'block' }}>
                <span style={labelStyle}>{t(`${ns}.mode`)}</span>
                <div style={{ maxWidth: 200 }}>
                  <Dropdown
                    value={draft.mode}
                    options={[
                      { value: 'snapshot', label: t(`${ns}.modeSnapshot`) },
                      { value: 'suspend', label: t(`${ns}.modeSuspend`) },
                      { value: 'stop', label: t(`${ns}.modeStop`) },
                    ]}
                    onChange={(v) => patch({ mode: v as Draft['mode'] })}
                  />
                </div>
                <span style={{ display: 'block', fontSize: 11, color: c.textFaint, marginTop: 5, lineHeight: 1.5 }}>
                  {t(`${ns}.modeHint`)}
                </span>
              </label>
              {numberField('bwlimit', t(`${ns}.bwlimit`), t(`${ns}.bwlimitHint`))}
            </>,
          )}

          {section(
            t(`${ns}.retentionSection`),
            <>
              <span style={{ fontSize: 12, color: c.textFaint, lineHeight: 1.5, marginTop: -6 }}>
                {t(`${ns}.retentionHint`)}
              </span>
              {numberField('keep_last', t(`${ns}.keepLast`), t(`${ns}.keepLastHint`))}
              {numberField('keep_yearly', t(`${ns}.keepYearly`), t(`${ns}.keepYearlyHint`))}
            </>,
          )}

          {section(
            t(`${ns}.historySection`),
            numberField('history_days', t(`${ns}.historyDays`), t(`${ns}.historyDaysHint`)),
          )}

          {section(
            t(`${ns}.serverSection`),
            <>
              <span style={{ fontSize: 12, color: c.amber, lineHeight: 1.5, marginTop: -6 }}>
                {t(`${ns}.restartHint`)}
              </span>
              {numberField('port', t(`${ns}.port`), t(`${ns}.portHint`), { min: 1, max: 65535 })}
              {numberField('session_days', t(`${ns}.sessionDays`), t(`${ns}.sessionDaysHint`), {
                min: 1,
              })}
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <Toggle on={draft.https_only} onClick={() => patch({ https_only: !draft.https_only })} />
                <span>
                  <span style={{ display: 'block', fontSize: 13, fontWeight: 600 }}>
                    {t(`${ns}.httpsOnly`)}
                  </span>
                  <span style={{ display: 'block', fontSize: 11, color: c.textFaint, marginTop: 3, lineHeight: 1.5 }}>
                    {t(`${ns}.httpsOnlyHint`)}
                  </span>
                </span>
              </div>
            </>,
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 22 }}>
          <button
            onClick={() => void onSave()}
            disabled={!dirty || busy}
            style={{
              ...primaryBtn,
              padding: '10px 24px',
              background: dirty ? c.accent : '#1d232b',
              color: dirty ? c.accentInk : c.textMuted,
              border: dirty ? 'none' : '1px solid #262d35',
              cursor: dirty && !busy ? 'pointer' : 'not-allowed',
            }}
          >
            {t('common.save')}
          </button>
          {savedNote && !dirty && <span style={{ fontSize: 12, color: c.green }}>{t(`${ns}.saved`)}</span>}
          {err && <span style={{ fontSize: 12, color: c.red }}>{err}</span>}
        </div>
      </div>

      <Suspense fallback={<Spinner />}>
        <YamlEditor />
      </Suspense>
    </div>
  )
}
