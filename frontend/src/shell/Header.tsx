import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { StatusResponse } from '../api/types'
import { useConfig } from '../config/ConfigContext'
import { useClock } from '../hooks/useClock'
import { applyTheme, c, currentTheme, mono, tint } from '../theme'
import { fmtClock, fmtDT } from '../utils/format'
import { headerPill, type PillTone } from '../utils/status'

interface HeaderProps {
  status: StatusResponse | null
  view: 'main' | 'settings'
  onToggleView: () => void
  onLogout: () => void
}

// The single-PBS host readout that used to sit beside the pill is gone for good: with N
// backup servers it described nothing, and per-device state lives in the topology now.
const TONE: Record<PillTone, string> = { blue: c.blue, amber: c.amber, neutral: c.textFaint }

export function Header({ status, view, onToggleView, onLogout }: HeaderProps) {
  const { t } = useTranslation()
  const { config, save } = useConfig()
  const now = useClock()
  const p = headerPill(status)
  const color = TONE[p.tone]
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
        {/* BASE_URL is "/" for the app the backend serves and "./" for the public demo build,
            which lives under /demo/ — a root-absolute src would 404 there. */}
        <img
          src={`${import.meta.env.BASE_URL}assets/joulenap-icon.svg`}
          alt="Joulenap"
          className="jn-header-icon"
          style={{ position: 'relative', left: 5 }}
        />
        <div style={{ width: 1, height: 28, background: c.inputBorder }} />
        <img
          src={`${import.meta.env.BASE_URL}assets/joulenap-wordmark.svg`}
          alt="Joulenap"
          className="jn-header-wordmark"
          style={{ position: 'relative', top: 4 }}
        />
      </div>

      <div className="jn-header-status">
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '6px 13px',
            borderRadius: 999,
            whiteSpace: 'nowrap',
            background: c.hover,
            border: `1px solid ${tint(color, 33)}`,
          }}
        >
          {p.busy ? (
            <div
              style={{
                width: 13,
                height: 13,
                borderRadius: '50%',
                border: `2px solid ${tint(color, 20)}`,
                borderTopColor: color,
                animation: 'spin .7s linear infinite',
              }}
            />
          ) : (
            <div
              style={{ width: 9, height: 9, borderRadius: '50%', background: color, boxShadow: `0 0 0 3px ${tint(color, 13)}` }}
            />
          )}
          <span style={{ fontSize: 13, fontWeight: 600 }}>
            {t(p.labelKey, {
              route: p.route,
              when: p.nextAt ? fmtDT(new Date(p.nextAt)) : '',
            })}
          </span>
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
