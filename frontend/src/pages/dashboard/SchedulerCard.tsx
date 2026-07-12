import { useTranslation } from 'react-i18next'
import { Toggle } from '../../components/Toggle'
import { c, mono, panelStyle } from '../../theme'
import { isAdvancedSchedule } from '../../utils/cron'

export interface SchedulerDraft {
  time: string
  days: boolean[]
  dom: string
  month: string
  rawSchedule: string
  gcEnabled: boolean
  keepDaily: number
  keepWeekly: number
  keepMonthly: number
  wakeTimeout: number
  wakeRetries: number
}

interface Props {
  enabled: boolean
  onToggleEnabled: () => void
  toggleError: string | null
  draft: SchedulerDraft
  patch: (p: Partial<SchedulerDraft>) => void
  dirty: boolean
  onApply: () => void
  busy: boolean
  saved: boolean
  error: string | null
}

const label: React.CSSProperties = {
  display: 'block',
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: '.08em',
  textTransform: 'uppercase',
  color: c.textFaint,
  marginBottom: 6,
}

const numInput: React.CSSProperties = {
  width: '100%',
  background: c.inputBg,
  border: `1px solid ${c.inputBorder}`,
  borderRadius: 7,
  color: c.text,
  padding: '8px 9px',
  fontFamily: mono,
  fontSize: 14,
}

const DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const

const toInt = (v: string) => (v === '' ? 0 : Math.max(0, parseInt(v, 10) || 0))

export function SchedulerCard({ enabled, onToggleEnabled, toggleError, draft, patch, dirty, onApply, busy, saved, error }: Props) {
  const { t } = useTranslation()
  const advanced = isAdvancedSchedule({ time: draft.time, days: draft.days, dom: draft.dom, month: draft.month })

  return (
    <div style={{ ...panelStyle, padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: '.04em' }}>{t('dashboard.scheduleTitle')}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: enabled ? c.accent : c.textFaint }}>
            {enabled ? t('dashboard.enabled') : t('dashboard.disabled')}
          </span>
          <Toggle on={enabled} onClick={onToggleEnabled} />
        </div>
      </div>

      {toggleError && (
        <div role="alert" style={{ marginTop: -6, marginBottom: 16, fontSize: 12, color: c.red, textAlign: 'right' }}>
          {toggleError}
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 26, alignItems: 'flex-end', marginBottom: 18 }}>
        <label style={{ display: 'block', width: 148 }}>
          <span style={label}>{t('dashboard.backupTime')}</span>
          <input
            type="time"
            value={draft.time}
            disabled={advanced}
            onChange={(e) => patch({ time: e.target.value || '00:00' })}
            style={{
              ...numInput,
              fontSize: 15,
              fontWeight: 500,
              padding: '9px 10px',
              opacity: advanced ? 0.45 : 1,
              cursor: advanced ? 'not-allowed' : 'text',
            }}
          />
        </label>

        <div>
          <span style={label}>{t('dashboard.gc')}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, height: 38 }}>
            <Toggle on={draft.gcEnabled} onClick={() => patch({ gcEnabled: !draft.gcEnabled })} />
            <span style={{ fontSize: 12, fontWeight: 600, color: draft.gcEnabled ? c.accent : c.textFaint }}>
              {draft.gcEnabled ? t('dashboard.enabled') : t('dashboard.disabled')}
            </span>
          </div>
        </div>

        <div className="jn-sched-divider" style={{ background: c.border }} />

        <div>
          <span style={{ ...label, display: 'flex', alignItems: 'center', gap: 5 }}>
            {t('dashboard.retention')}
            <span
              title={t('dashboard.retentionHelp')}
              style={{ cursor: 'help', color: c.textFaint, fontSize: 11, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}
            >
              ⓘ
            </span>
          </span>
          <div style={{ display: 'flex', gap: 10 }}>
            {(
              [
                ['daily', 'keepDaily'],
                ['weekly', 'keepWeekly'],
                ['monthly', 'keepMonthly'],
              ] as const
            ).map(([lbl, key]) => (
              <label key={key} style={{ display: 'block', width: 92 }}>
                <span style={{ display: 'block', fontSize: 11, color: '#9aa2ac', marginBottom: 5 }}>
                  {t(`dashboard.${lbl}`)}
                </span>
                <input
                  type="number"
                  min={0}
                  value={draft[key]}
                  onChange={(e) => patch({ [key]: toInt(e.target.value) } as Partial<SchedulerDraft>)}
                  style={numInput}
                />
              </label>
            ))}
          </div>
        </div>

        <div className="jn-sched-divider" style={{ background: c.border }} />

        <label style={{ display: 'block', width: 210 }}>
          <span style={label}>{t('dashboard.wakeTimeout')}</span>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              background: c.inputBg,
              border: `1px solid ${c.inputBorder}`,
              borderRadius: 7,
              padding: '0 12px 0 0',
            }}
          >
            <input
              type="number"
              min={0}
              value={draft.wakeTimeout}
              onChange={(e) => patch({ wakeTimeout: toInt(e.target.value) })}
              style={{ flex: 1, minWidth: 0, background: 'transparent', border: 'none', color: c.text, padding: '9px 11px', fontFamily: mono, fontSize: 14 }}
            />
            <span style={{ fontFamily: mono, fontSize: 13, color: '#6f7884' }}>{t('dashboard.seconds')}</span>
          </div>
        </label>

        <label style={{ display: 'block', width: 120 }}>
          <span style={{ ...label, display: 'flex', alignItems: 'center', gap: 5 }}>
            {t('dashboard.wakeRetries')}
            <span
              title={t('dashboard.wakeRetriesHelp')}
              style={{ cursor: 'help', color: c.textFaint, fontSize: 11, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}
            >
              ⓘ
            </span>
          </span>
          <input
            type="number"
            min={0}
            value={draft.wakeRetries}
            onChange={(e) => patch({ wakeRetries: toInt(e.target.value) })}
            style={numInput}
          />
        </label>
      </div>

      <span style={label}>{t('dashboard.backupDays')}</span>
      <div className="jn-days">
        {DAY_KEYS.map((key, i) => {
          const on = draft.days[i]
          return (
            <button
              key={key}
              className="jn-day-btn"
              disabled={advanced}
              onClick={() => {
                if (advanced) return
                const days = draft.days.slice()
                days[i] = !days[i]
                patch({ days })
              }}
              style={{
                textAlign: 'center',
                background: on ? c.accent : 'transparent',
                color: on ? c.accentInk : c.textFaint,
                border: `1px solid ${on ? c.accent : c.inputBorder}`,
                borderRadius: 7,
                padding: '8px 0',
                fontFamily: mono,
                fontSize: 12,
                fontWeight: 600,
                letterSpacing: '.04em',
                cursor: advanced ? 'not-allowed' : 'pointer',
                opacity: advanced ? 0.45 : 1,
              }}
            >
              {t(`dashboard.days.${key}`)}
            </button>
          )
        })}
      </div>
      {advanced && (
        <span
          style={{
            display: 'block',
            marginTop: 8,
            fontSize: 11,
            color: c.textFaint,
            lineHeight: 1.5,
          }}
        >
          {t('dashboard.scheduleCustom', { cron: draft.rawSchedule })}
        </span>
      )}

      <div style={{ height: 1, background: c.border, margin: '18px 0 14px' }} />
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 12 }}>
        <button
          onClick={onApply}
          disabled={!dirty || busy}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            background: dirty ? c.accent : '#1d232b',
            color: dirty ? c.accentInk : c.textMuted,
            border: dirty ? 'none' : '1px solid #262d35',
            borderRadius: 8,
            padding: '11px 28px',
            fontSize: 13,
            fontWeight: 600,
            cursor: !dirty || busy ? 'not-allowed' : 'pointer',
          }}
        >
          ✓ {t('dashboard.apply')}
        </button>
        {saved && !dirty && <span style={{ fontSize: 12, color: c.green }}>{t('dashboard.saved')}</span>}
        {error && <span style={{ fontSize: 12, color: c.red }}>{error}</span>}
      </div>
    </div>
  )
}
