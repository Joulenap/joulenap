import { Suspense, lazy, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, api } from '../../api/client'
import type { Config } from '../../api/types'
import { Spinner } from '../../components/Spinner'
import { Toggle } from '../../components/Toggle'
import { useConfig } from '../../config/ConfigContext'
import { useRegisterDirty } from '../../shell/UnsavedGuard'

// CodeMirror is code-split so it only downloads when this tab is opened.
const YamlEditor = lazy(() => import('./YamlEditor'))

const ns = 'settings.advanced'

// One card, one Save. The backup mode, bandwidth limit and retention slots that used to live
// here belong to a route now (route modal -> Advanced, M10); what is left really is
// application-wide, and the update check joined it from the Integrations tab (NOTES).
interface Draft {
  port: number
  session_days: number
  https_only: boolean
  history_days: number
  update_check: boolean
}

function draftOf(config: Config): Draft {
  return {
    port: config.app.port,
    session_days: config.app.session.max_age_days,
    https_only: config.app.session.https_only,
    history_days: config.maintenance.history.retention_days,
    update_check: config.app.update_check,
  }
}

export function Advanced({ refreshStatus }: { refreshStatus: () => void }) {
  return (
    <div>
      <SchedulerSwitch refreshStatus={refreshStatus} />
      <Application />
      <Suspense fallback={<Spinner />}>
        <YamlEditor />
      </Suspense>
    </div>
  )
}

/**
 * The global kill-switch, and the only place it lives (NOTES: no mirror on the homepage).
 *
 * It writes through `POST /api/scheduler/toggle`, not the config PUT: that endpoint is what
 * actually arms and disarms the APScheduler jobs. `refreshStatus` re-reads /status so the
 * shell's persistent "scheduler paused" banner appears with the click rather than up to five
 * seconds later.
 */
function SchedulerSwitch({ refreshStatus }: { refreshStatus: () => void }) {
  const { t } = useTranslation()
  const { config, reload } = useConfig()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const on = Boolean(config?.app.scheduler_enabled)

  async function toggle() {
    setBusy(true)
    setErr(null)
    try {
      await api.toggleScheduler(!on)
      await reload()
      refreshStatus()
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <div className="panel-hd">
        <h2>{t(`${ns}.schedulerTitle`)}</h2>
      </div>
      <div className="panel-bd stack tight">
        <label className="tglrow">
          <Toggle on={on} onClick={() => void (busy ? null : toggle())} size="sm" />
          <span>{t(`${ns}.schedulerLabel`)}</span>
        </label>
        <span className="help">{t(`${ns}.schedulerHint`)}</span>
        {err && <span className="err-note">{err}</span>}
      </div>
    </section>
  )
}

function Application() {
  const { t } = useTranslation()
  const { config, save } = useConfig()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
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

  if (!config || !draft) return null

  function patch(next: Partial<Draft>) {
    setDraft((d) => (d ? { ...d, ...next } : d))
    setSaved(false)
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
          update_check: draft.update_check,
          session: { max_age_days: draft.session_days, https_only: draft.https_only },
        },
        maintenance: { ...config.maintenance, history: { retention_days: draft.history_days } },
      })
      setSaved(true)
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
  }

  const num = (
    key: keyof Draft,
    label: string,
    hint?: string,
    opts: { min?: number; max?: number } = {},
  ) => {
    const min = opts.min ?? 0
    return (
      <div className="field" key={key}>
        <label htmlFor={`app-${key}`}>{label}</label>
        <input
          id={`app-${key}`}
          type="number"
          className="in-mono"
          min={min}
          max={opts.max}
          value={String(draft[key])}
          onChange={(e) => {
            let n = Math.floor(Number(e.target.value)) || 0
            n = Math.max(min, n)
            if (opts.max != null) n = Math.min(opts.max, n)
            patch({ [key]: n } as Partial<Draft>)
          }}
        />
        {hint && <span className="help">{hint}</span>}
      </div>
    )
  }

  return (
    <section className="panel">
      <div className="panel-hd">
        <h2>{t(`${ns}.appTitle`)}</h2>
      </div>
      <div className="panel-bd stack">
        <div className="frow3">
          {num('port', t(`${ns}.port`), t(`${ns}.portHint`), { min: 1, max: 65535 })}
          {num('session_days', t(`${ns}.sessionDays`), undefined, { min: 1 })}
          <div className="field">
            <span className="lab">{t(`${ns}.cookie`)}</span>
            <label className="tglrow" style={{ height: 35 }}>
              <Toggle on={draft.https_only} onClick={() => patch({ https_only: !draft.https_only })} size="sm" />
              <span>{t(`${ns}.httpsOnly`)}</span>
            </label>
            <span className="help">{t(`${ns}.httpsOnlyHint`)}</span>
          </div>
          {num('history_days', t(`${ns}.historyDays`), t(`${ns}.historyDaysHint`))}
          <div className="field span2">
            <span className="lab">{t(`${ns}.updates`)}</span>
            <label className="tglrow">
              <Toggle on={draft.update_check} onClick={() => patch({ update_check: !draft.update_check })} size="sm" />
              <span>{t(`${ns}.updateCheck`)}</span>
            </label>
            <span className="help">{t(`${ns}.updateCheckHint`)}</span>
          </div>
        </div>
        <div className="save-row">
          <span className="help spacer">{t(`${ns}.restartHint`)}</span>
          {err && <span className="err-note">{err}</span>}
          {saved && !dirty && <span className="ok-note">{t(`${ns}.saved`)}</span>}
          {dirty && !err && <span className="help">{t('common.unsavedChanges')}</span>}
          <button
            type="button"
            className="btn btn-accent"
            disabled={!dirty || busy}
            onClick={() => void onSave()}
          >
            {t('common.save')}
          </button>
        </div>
      </div>
    </section>
  )
}
