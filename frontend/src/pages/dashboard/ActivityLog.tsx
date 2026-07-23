import { useTranslation } from 'react-i18next'
import type { LogLine } from '../../api/types'
import { c, mono } from '../../theme'
import { fmtClock } from '../../utils/format'

// Shared with RunHistory, which renders the same log lines nested inside an expanded run.
export const LEVELS: Record<string, { color: string; bg: string }> = {
  INFO: { color: '#8aa6c0', bg: 'rgba(138,166,192,.13)' },
  OK: { color: c.green, bg: 'rgba(63,178,127,.14)' },
  WARN: { color: c.amber, bg: 'rgba(224,169,43,.14)' },
  ERROR: { color: c.red, bg: 'rgba(229,103,91,.14)' },
}

export const colHead: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: '.06em',
  textTransform: 'uppercase',
  color: '#6f7884',
}

// The flat activity stream. The surrounding panel, the view switch and the scroll container
// live in HistoryCard, which renders either this or RunHistory.
export function ActivityLog({ logs }: { logs: LogLine[] }) {
  const { t } = useTranslation()
  return (
    <>
      <div
        className="jn-log-head"
        style={{ padding: '0 18px 7px', borderBottom: `1px solid ${c.border}` }}
      >
        <span style={colHead}>{t('dashboard.colTime')}</span>
        <span style={colHead}>{t('dashboard.colLevel')}</span>
        <span style={colHead}>{t('dashboard.colMessage')}</span>
      </div>
      <div>
        {logs.length === 0 && (
          <div style={{ padding: '14px 18px', fontSize: 13, color: c.textFaint }}>{t('dashboard.noLogs')}</div>
        )}
        {logs.map((l) => {
          const lvl = LEVELS[l.level] ?? LEVELS.INFO
          return (
            <div
              key={l.id}
              className="jn-log-row"
              style={{
                alignItems: 'center',
                padding: '7px 18px',
                borderBottom: '1px solid #1b212880',
              }}
            >
              <span style={{ fontFamily: mono, fontSize: 12, color: c.textFaint }}>{fmtClock(new Date(l.ts))}</span>
              <span>
                <span
                  style={{
                    display: 'inline-block',
                    fontFamily: mono,
                    fontSize: 10,
                    fontWeight: 600,
                    padding: '2px 7px',
                    borderRadius: 5,
                    color: lvl.color,
                    background: lvl.bg,
                  }}
                >
                  {l.level}
                </span>
              </span>
              <span style={{ fontSize: 13, color: '#c8cdd4', minWidth: 0, overflowWrap: 'anywhere' }}>
                {l.message}
              </span>
            </div>
          )
        })}
      </div>
    </>
  )
}
