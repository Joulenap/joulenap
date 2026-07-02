import { useTranslation } from 'react-i18next'
import type { StatusResponse } from '../../api/types'
import { c, mono, panelStyle } from '../../theme'
import { fmtBytesTB, fmtDT, fmtUptime, pad, rel } from '../../utils/format'

const tileLabel: React.CSSProperties = {
  display: 'block',
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: '.08em',
  textTransform: 'uppercase',
  color: c.textFaint,
  marginBottom: 8,
}

function Tile({ children }: { children: React.ReactNode }) {
  return <div style={{ ...panelStyle, padding: '15px 16px' }}>{children}</div>
}

export function StatTiles({ status }: { status: StatusResponse | null }) {
  const { t } = useTranslation()
  const now = new Date()

  // Next run
  const nr = status?.next_run ? new Date(status.next_run) : null
  let nextStr = '—'
  let nextRel = ''
  if (nr) {
    const sameDay = nr.getDate() === now.getDate() && nr.getMonth() === now.getMonth()
    nextStr = `${sameDay ? t('dashboard.today') : t('dashboard.tomorrow')} ${pad(nr.getHours())}:${pad(nr.getMinutes())}`
    nextRel = status?.scheduler_enabled
      ? t('dashboard.inTime', { rel: rel(nr.getTime() - now.getTime()) })
      : t('status.timerDisabled')
  } else {
    nextRel = t('status.timerDisabled')
  }

  // Last run
  const lr = status?.last_run?.started_at ? new Date(status.last_run.started_at) : null
  const lastStr = lr ? fmtDT(lr) : '—'
  const lastRel = lr ? t('dashboard.ago', { rel: rel(now.getTime() - lr.getTime()) }) : t('dashboard.neverRun')

  // Disk
  const ds = status?.datastore ?? null
  const load = status?.load ?? null
  const loadBars: [string, number, string][] = load
    ? [
        ['CPU', load.cpu, c.accent],
        ['MEM', load.mem, c.blue],
      ]
    : []

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
      <Tile>
        <span style={tileLabel}>{t('dashboard.nextRun')}</span>
        <div style={{ fontFamily: mono, fontSize: 18, fontWeight: 600 }}>{nextStr}</div>
        <div style={{ fontSize: 12, color: c.accent, marginTop: 3, fontWeight: 500 }}>{nextRel}</div>
      </Tile>

      <Tile>
        <span style={tileLabel}>{t('dashboard.lastRun')}</span>
        <div style={{ fontFamily: mono, fontSize: 18, fontWeight: 600 }}>{lastStr}</div>
        <div style={{ fontSize: 12, color: c.textDim, marginTop: 3 }}>{lastRel}</div>
      </Tile>

      <Tile>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 9, alignItems: 'center' }}>
          <span style={{ ...tileLabel, marginBottom: 0 }}>{t('dashboard.diskSpace')}</span>
          <span style={{ fontFamily: mono, fontSize: 12, color: '#9aa2ac' }}>
            {ds ? `${ds.used_pct}%` : '—'}
          </span>
        </div>
        <div style={{ height: 20, background: c.inputBg, borderRadius: 999, overflow: 'hidden', marginBottom: 9 }}>
          <div
            style={{
              width: ds ? `${ds.used_pct}%` : '0%',
              height: '100%',
              background: 'linear-gradient(90deg,#e8830f,#f5a83a)',
              borderRadius: 999,
              transition: 'width .5s',
            }}
          />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: mono, fontSize: 12 }}>
          <span style={{ color: c.textMid }}>
            {ds ? fmtBytesTB(ds.used) : '—'} <span style={{ color: c.textFaint }}>{t('dashboard.used')}</span>
          </span>
          <span style={{ color: c.green }}>
            {ds ? fmtBytesTB(ds.total - ds.used) : '—'} <span style={{ color: c.textFaint }}>{t('dashboard.free')}</span>
          </span>
        </div>
      </Tile>

      <Tile>
        <span style={tileLabel}>{t('dashboard.pbsLoad')}</span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {!load && <span style={{ fontFamily: mono, fontSize: 12, color: c.textFaint }}>—</span>}
          {loadBars.map(([name, pct, col]) => (
            <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontFamily: mono, fontSize: 11, color: c.textDim, width: 30 }}>{name}</span>
              <div style={{ flex: 1, height: 6, background: c.inputBg, borderRadius: 999, overflow: 'hidden' }}>
                <div
                  style={{
                    width: `${pct}%`,
                    height: '100%',
                    background: col,
                    borderRadius: 999,
                    transition: 'width .6s',
                  }}
                />
              </div>
              <span style={{ fontFamily: mono, fontSize: 11, color: c.textMid, width: 34, textAlign: 'right' }}>
                {pct}%
              </span>
            </div>
          ))}
          {load && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontFamily: mono, fontSize: 11, color: c.textDim }}>{t('dashboard.uptime')}</span>
              <span style={{ fontFamily: mono, fontSize: 11, color: c.green }}>{fmtUptime(load.uptime)}</span>
            </div>
          )}
        </div>
      </Tile>
    </div>
  )
}
