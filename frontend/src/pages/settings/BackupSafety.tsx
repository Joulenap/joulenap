import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../api/client'
import { Toggle } from '../../components/Toggle'
import { useConfig } from '../../config/ConfigContext'
import { useRegisterDirty } from '../../shell/UnsavedGuard'
import { c, inputStyle, labelStyle, panelStyle, primaryBtn } from '../../theme'

// Backup-safety guardrails. These map to existing backend fields wired into the cycle:
//   - backup.min_free_percent      -> PRECHECK step (abort before vzdump on a near-full store)
//   - pbs.poweroff_task_wait       -> pre-power-off guard (don't interrupt a busy PBS)
//   - maintenance.verify.after_backup -> quick verify of new snapshots after each backup
//   - maintenance.verify.enabled/schedule/reverify_days -> scheduled full verification cycle
// A 0 disables the free-space check / the power-off wait (matching the backend semantics).
// Wake timeout/retries live on the dashboard Scheduler card, so they're not repeated here.

interface Draft {
  min_free_percent: number
  poweroff_task_wait: number
  verify_after_backup: boolean
  verify_enabled: boolean
  verify_schedule: string
  verify_reverify_days: number
}

// Sensible values to drop in when a guard is toggled on from disabled (0).
const DEFAULT_FREE_PCT = 10
const DEFAULT_GUARD_WAIT = 600

// Preset cron schedules offered for the periodic verify (kept simple — a full cron editor
// would be overkill here). A legacy/custom value is preserved as an extra option.
const SCHEDULE_PRESETS = ['0 3 * * 0', '0 3 1 * *']

export function BackupSafety() {
  const { t } = useTranslation()
  const { config, save } = useConfig()

  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [savedNote, setSavedNote] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (config)
      setDraft({
        min_free_percent: config.backup.min_free_percent,
        poweroff_task_wait: config.pbs.poweroff_task_wait,
        verify_after_backup: config.maintenance.verify.after_backup,
        verify_enabled: config.maintenance.verify.enabled,
        verify_schedule: config.maintenance.verify.schedule,
        verify_reverify_days: config.maintenance.verify.reverify_days,
      })
  }, [config])

  const dirty = useMemo(() => {
    if (!config || !draft) return false
    const v = config.maintenance.verify
    return (
      draft.min_free_percent !== config.backup.min_free_percent ||
      draft.poweroff_task_wait !== config.pbs.poweroff_task_wait ||
      draft.verify_after_backup !== v.after_backup ||
      draft.verify_enabled !== v.enabled ||
      draft.verify_schedule !== v.schedule ||
      draft.verify_reverify_days !== v.reverify_days
    )
  }, [config, draft])
  useRegisterDirty(dirty)

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
        backup: { ...config.backup, min_free_percent: draft.min_free_percent },
        pbs: { ...config.pbs, poweroff_task_wait: draft.poweroff_task_wait },
        maintenance: {
          ...config.maintenance,
          verify: {
            ...config.maintenance.verify,
            after_backup: draft.verify_after_backup,
            enabled: draft.verify_enabled,
            schedule: draft.verify_schedule,
            reverify_days: draft.verify_reverify_days,
          },
        },
      })
      setSavedNote(true)
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
  }

  const ns = 'settings.safety'

  const numberField = (
    label: string,
    value: number,
    onChange: (n: number) => void,
    opts?: { min?: number; max?: number; hint?: string },
  ) => (
    <label style={{ display: 'block' }}>
      <span style={labelStyle}>{label}</span>
      <input
        type="number"
        value={String(value)}
        min={opts?.min}
        max={opts?.max}
        onChange={(e) => {
          let n = Math.floor(Number(e.target.value)) || 0
          if (opts?.min != null) n = Math.max(opts.min, n)
          if (opts?.max != null) n = Math.min(opts.max, n)
          onChange(n)
        }}
        style={inputStyle}
      />
      {opts?.hint && (
        <span style={{ display: 'block', fontSize: 11, color: c.textFaint, marginTop: 5 }}>{opts.hint}</span>
      )}
    </label>
  )

  // A card with a title/description and an optional enable toggle; the body shows only
  // when the guard is enabled (matching the Notifications channel cards).
  const card = (
    title: string,
    desc: string,
    body: React.ReactNode,
    toggle?: { on: boolean; onClick: () => void },
  ) => {
    const open = (toggle ? toggle.on : true) && body != null
    return (
      <div
        style={{
          background: c.panelAlt,
          border: `1px solid ${c.borderSoft}`,
          borderRadius: 10,
          padding: '16px 18px',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            gap: 16,
            marginBottom: open ? 16 : 0,
          }}
        >
          <div>
            <span style={{ display: 'block', fontSize: 14, fontWeight: 600, color: c.textMid }}>{title}</span>
            <span style={{ display: 'block', fontSize: 12, color: c.textFaint, marginTop: 3, lineHeight: 1.5 }}>
              {desc}
            </span>
          </div>
          {toggle && <Toggle on={toggle.on} onClick={toggle.onClick} />}
        </div>
        {open && <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>{body}</div>}
      </div>
    )
  }

  return (
    <div style={{ ...panelStyle, padding: '24px 26px', maxWidth: 640 }}>
      <span style={{ display: 'block', fontSize: 16, fontWeight: 700, marginBottom: 5 }}>{t(`${ns}.title`)}</span>
      <span style={{ display: 'block', fontSize: 13, color: c.textDim, lineHeight: 1.5, marginBottom: 22 }}>
        {t(`${ns}.subtitle`)}
      </span>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* Pre-flight: refuse to back up onto a near-full datastore (0 = off). */}
        {card(
          t(`${ns}.freeTitle`),
          t(`${ns}.freeDesc`),
          numberField(t(`${ns}.freeLabel`), draft.min_free_percent, (n) => patch({ min_free_percent: n }), {
            min: 1,
            max: 100,
          }),
          {
            on: draft.min_free_percent > 0,
            onClick: () => patch({ min_free_percent: draft.min_free_percent > 0 ? 0 : DEFAULT_FREE_PCT }),
          },
        )}

        {/* Safety guard: wait for a busy PBS to go idle before power-off (0 = off). */}
        {card(
          t(`${ns}.guardTitle`),
          t(`${ns}.guardDesc`),
          numberField(t(`${ns}.guardLabel`), draft.poweroff_task_wait, (n) => patch({ poweroff_task_wait: n }), {
            min: 1,
          }),
          {
            on: draft.poweroff_task_wait > 0,
            onClick: () =>
              patch({ poweroff_task_wait: draft.poweroff_task_wait > 0 ? 0 : DEFAULT_GUARD_WAIT }),
          },
        )}

        {/* --- Verification ------------------------------------------------ */}
        <span style={{ ...labelStyle, marginTop: 10 }}>{t(`${ns}.verifySection`)}</span>

        {/* Always-visible time/energy disclaimer (never a popup). */}
        <div
          style={{
            display: 'flex',
            gap: 12,
            background: 'rgba(232,131,15,.08)',
            border: '1px solid rgba(232,131,15,.4)',
            borderRadius: 10,
            padding: '14px 16px',
          }}
        >
          <span style={{ fontSize: 18, lineHeight: 1.3, flex: '0 0 auto' }}>⚠️</span>
          <span style={{ fontSize: 12.5, color: c.textMid, lineHeight: 1.55 }}>
            <strong style={{ color: c.text }}>{t(`${ns}.verifyWarnTitle`)}</strong>
            <br />
            {t(`${ns}.verifyWarnBody`)}
          </span>
        </div>

        {/* Tier 1: quick verify of new snapshots after each backup. */}
        {card(t(`${ns}.afterTitle`), t(`${ns}.afterDesc`), null, {
          on: draft.verify_after_backup,
          onClick: () => patch({ verify_after_backup: !draft.verify_after_backup }),
        })}

        {/* Tier 2: scheduled full verification on its own wake/verify/power-off cycle. */}
        {card(
          t(`${ns}.schedTitle`),
          t(`${ns}.schedDesc`),
          <>
            <label style={{ display: 'block' }}>
              <span style={labelStyle}>{t(`${ns}.frequency`)}</span>
              <select
                value={draft.verify_schedule}
                onChange={(e) => patch({ verify_schedule: e.target.value })}
                style={inputStyle}
              >
                {!SCHEDULE_PRESETS.includes(draft.verify_schedule) && (
                  <option value={draft.verify_schedule}>{draft.verify_schedule}</option>
                )}
                <option value="0 3 * * 0">{t(`${ns}.weeklyOpt`)}</option>
                <option value="0 3 1 * *">{t(`${ns}.monthlyOpt`)}</option>
              </select>
            </label>
            {numberField(
              t(`${ns}.reverifyLabel`),
              draft.verify_reverify_days,
              (n) => patch({ verify_reverify_days: n }),
              { min: 0, hint: t(`${ns}.reverifyHint`) },
            )}
          </>,
          {
            on: draft.verify_enabled,
            onClick: () => patch({ verify_enabled: !draft.verify_enabled }),
          },
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 22 }}>
        <button
          onClick={onSave}
          disabled={!dirty || busy}
          style={{
            ...primaryBtn,
            padding: '10px 24px',
            background: dirty ? c.accent : c.btnBg,
            color: dirty ? c.accentInk : c.textMuted,
            border: dirty ? 'none' : `1px solid ${c.btnBorder}`,
            cursor: dirty ? 'pointer' : 'not-allowed',
          }}
        >
          {t(`${ns}.apply`)}
        </button>
        {savedNote && !dirty && <span style={{ fontSize: 12, color: c.green }}>{t(`${ns}.saved`)}</span>}
        {err && <span style={{ fontSize: 12, color: c.red }}>{err}</span>}
      </div>
    </div>
  )
}
