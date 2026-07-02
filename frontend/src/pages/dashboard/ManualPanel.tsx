import { useTranslation } from 'react-i18next'
import type { StatusResponse } from '../../api/types'
import { c, panelStyle } from '../../theme'

interface Props {
  status: StatusResponse | null
  onBackup: () => void
  onGc: () => void
  onPowerOn: () => void
  onPowerOff: () => void
}

const colHead: React.CSSProperties = {
  display: 'block',
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: '.08em',
  textTransform: 'uppercase',
  color: c.textFaint,
  marginBottom: 10,
  textAlign: 'center',
}

function actionBtn(variant: 'primary' | 'ghost' | 'green' | 'red', enabled: boolean): React.CSSProperties {
  const base: React.CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    width: '100%',
    borderRadius: 8,
    padding: 11,
    fontSize: 13,
    fontWeight: 600,
    cursor: enabled ? 'pointer' : 'not-allowed',
  }
  if (!enabled) {
    return { ...base, background: '#1d232b', color: c.textMuted, border: '1px solid #262d35' }
  }
  if (variant === 'primary') return { ...base, background: c.accent, color: c.accentInk, border: 'none' }
  if (variant === 'green') return { ...base, background: 'transparent', color: c.green, border: '1px solid #2f5e4b' }
  if (variant === 'red') return { ...base, background: 'transparent', color: c.red, border: '1px solid #5e3330' }
  return { ...base, background: 'transparent', color: c.text, border: '1px solid #3a434d' }
}

export function ManualPanel({ status, onBackup, onGc, onPowerOn, onPowerOff }: Props) {
  const { t } = useTranslation()
  const online = !!status?.pbs_online
  const busy = !!status?.job_running
  const canJob = online && !busy

  return (
    <div style={{ ...panelStyle, padding: '16px 18px', height: '100%' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 22 }}>
        <div>
          <span style={colHead}>{t('dashboard.manualJobs')}</span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
            <button style={actionBtn('primary', canJob)} disabled={!canJob} onClick={onBackup}>
              ▶ {t('dashboard.runBackup')}
            </button>
            <button style={actionBtn('ghost', canJob)} disabled={!canJob} onClick={onGc}>
              ⟳ {t('dashboard.runGc')}
            </button>
          </div>
        </div>
        <div>
          <span style={colHead}>{t('dashboard.power')}</span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
            <button style={actionBtn('green', !online)} disabled={online} onClick={onPowerOn}>
              ⏻ {t('dashboard.powerOn')}
            </button>
            <button style={actionBtn('red', canJob)} disabled={!canJob} onClick={onPowerOff}>
              ⏻ {t('dashboard.powerOff')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
