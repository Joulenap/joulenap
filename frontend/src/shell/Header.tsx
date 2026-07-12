import { useTranslation } from 'react-i18next'
import type { StatusResponse } from '../api/types'
import { useClock } from '../hooks/useClock'
import { c, mono } from '../theme'
import { fmtClock } from '../utils/format'
import { runningLabelKey } from '../utils/status'

interface HeaderProps {
  host: string
  status: StatusResponse | null
  view: 'main' | 'settings'
  onToggleView: () => void
  onLogout: () => void
}

function pill(status: StatusResponse | null, t: (k: string) => string) {
  if (status?.job_running)
    return { label: t(runningLabelKey(status.running_kind)), color: c.blue, busy: true, sub: '' }
  if (status?.pbs_online) {
    return {
      label: t('status.on'),
      color: c.green,
      busy: false,
      sub: status.scheduler_enabled ? '' : t('status.timerDisabled'),
    }
  }
  return { label: t('status.off'), color: '#6b7480', busy: false, sub: '' }
}

export function Header({ host, status, view, onToggleView, onLogout }: HeaderProps) {
  const { t } = useTranslation()
  const now = useClock()
  const p = pill(status, t)

  return (
    <header className="jn-header">
      <div className="jn-header-brand">
        <img
          src="/assets/joulenap-icon.svg"
          alt="Joulenap"
          className="jn-header-icon"
          style={{ position: 'relative', left: 5 }}
        />
        <div style={{ width: 1, height: 28, background: c.inputBorder }} />
        <img
          src="/assets/joulenap-wordmark.svg"
          alt="Joulenap"
          className="jn-header-wordmark"
          style={{ filter: 'brightness(0) invert(1)', position: 'relative', top: 4 }}
        />
      </div>

      <div className="jn-header-status">
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontFamily: mono, fontSize: 16, color: '#6f7884' }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: p.color }} />
          {host || '—'}
        </div>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '6px 13px',
            borderRadius: 999,
            whiteSpace: 'nowrap',
            background: 'rgba(255,255,255,.035)',
            border: `1px solid ${p.color}55`,
          }}
        >
          {p.busy ? (
            <div
              style={{
                width: 13,
                height: 13,
                borderRadius: '50%',
                border: `2px solid ${p.color}33`,
                borderTopColor: p.color,
                animation: 'spin .7s linear infinite',
              }}
            />
          ) : (
            <div
              style={{ width: 9, height: 9, borderRadius: '50%', background: p.color, boxShadow: `0 0 0 3px ${p.color}22` }}
            />
          )}
          <span style={{ fontSize: 13, fontWeight: 600 }}>{p.label}</span>
          {p.sub && <span style={{ fontSize: 12, color: c.textDim, fontWeight: 500 }}>· {p.sub}</span>}
        </div>

        <div style={{ fontFamily: mono, fontSize: 15, fontWeight: 500, color: c.textMid, minWidth: 78, textAlign: 'right' }}>
          {fmtClock(now)}
        </div>
      </div>

      <div className="jn-header-actions">
        <button
          onClick={onToggleView}
          title={t('header.settings')}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 7,
            background: '#1d232b',
            border: `1px solid ${c.inputBorder}`,
            borderRadius: 8,
            padding: '8px 12px',
            color: c.textMid,
            fontSize: 15,
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          {view === 'settings' ? '←' : '⚙'}
        </button>

        <button
          onClick={onLogout}
          title={t('header.signOut')}
          style={{
            background: '#1d232b',
            border: `1px solid ${c.inputBorder}`,
            borderRadius: 8,
            color: c.textMid,
            cursor: 'pointer',
            fontSize: 13,
            padding: '8px 12px',
            fontWeight: 600,
          }}
        >
          {t('header.logout')}
        </button>
      </div>
    </header>
  )
}
