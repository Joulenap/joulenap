import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { StatusResponse } from '../api/types'
import { useConfig } from '../config/ConfigContext'
import { useClock } from '../hooks/useClock'
import { applyTheme, c, currentTheme, mono, tint } from '../theme'
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
  return { label: t('status.off'), color: c.textFaint, busy: false, sub: '' }
}

export function Header({ host, status, view, onToggleView, onLogout }: HeaderProps) {
  const { t } = useTranslation()
  const { config, save } = useConfig()
  const now = useClock()
  const p = pill(status, t)
  const [theme, setTheme] = useState(currentTheme())

  const onToggleTheme = () => {
    const next = theme === 'light' ? 'dark' : 'light'
    applyTheme(next)
    setTheme(next)
    // Persist as app.theme; if config never loaded the toggle still works for this session.
    if (config) save({ ...config, app: { ...config.app, theme: next } }).catch(() => {})
  }

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
          style={{ position: 'relative', top: 4 }}
        />
      </div>

      <div className="jn-header-status">
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontFamily: mono, fontSize: 16, color: c.textFaint }}>
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
            background: c.hover,
            border: `1px solid ${tint(p.color, 33)}`,
          }}
        >
          {p.busy ? (
            <div
              style={{
                width: 13,
                height: 13,
                borderRadius: '50%',
                border: `2px solid ${tint(p.color, 20)}`,
                borderTopColor: p.color,
                animation: 'spin .7s linear infinite',
              }}
            />
          ) : (
            <div
              style={{ width: 9, height: 9, borderRadius: '50%', background: p.color, boxShadow: `0 0 0 3px ${tint(p.color, 13)}` }}
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
            background: c.btnBg,
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
          onClick={onToggleTheme}
          title={t('header.theme')}
          aria-label={t('header.theme')}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 5,
            background: c.btnBg,
            border: `1px solid ${c.inputBorder}`,
            borderRadius: 8,
            padding: '8px 12px',
            fontSize: 15,
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          <span style={{ color: theme === 'dark' ? c.accent : c.textMuted }}>☾</span>
          <span style={{ color: c.textMuted, fontWeight: 400 }}>/</span>
          <span style={{ color: theme === 'light' ? c.accent : c.textMuted }}>☀</span>
        </button>

        <button
          onClick={onLogout}
          title={t('header.signOut')}
          style={{
            background: c.btnBg,
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
