import { useTranslation } from 'react-i18next'
import type { StatusResponse } from '../../api/types'
import { c, panelStyle } from '../../theme'

interface Props {
  status: StatusResponse | null
  error: string | null
  onBackup: () => void
  onGc: () => void
  onStop: () => void
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

export function ManualPanel({ status, error, onBackup, onGc, onStop, onPowerOn, onPowerOff }: Props) {
  const { t } = useTranslation()
  const online = !!status?.pbs_online
  const busy = !!status?.job_running
  // Backup and GC both wake the PBS themselves (wake -> ... -> power-off), so they only
  // need "not already running" — not "PBS currently on". Power on/off still gate on state.
  const canJob = !busy
  const canPower = online && !busy

  // While a run is in flight, its own button becomes Stop (11.2). A scheduled *verify* has
  // no button of its own, so it borrows the primary slot — otherwise a hung verify would be
  // unstoppable, which is exactly the deadlock this feature exists to break. Cancelling
  // needs the run id; without it (a run started before this build, say) we only disable.
  const runningKind = busy ? (status?.running_kind ?? 'cycle') : null
  const canStop = busy && typeof status?.running_run_id === 'number'
  const stopSlot = runningKind === 'gc' ? 'gc' : 'primary'
  const stopLabel = t(
    runningKind === 'gc'
      ? 'dashboard.stopGc'
      : runningKind === 'verify'
        ? 'dashboard.stopVerify'
        : 'dashboard.stopBackup',
  )

  const stopButton = (
    <button type="button" style={actionBtn('red', canStop)} disabled={!canStop} onClick={onStop}>
      ■ {stopLabel}
    </button>
  )

  return (
    <div style={{ ...panelStyle, padding: '16px 18px', height: '100%' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 22 }}>
        <div>
          <span style={colHead}>{t('dashboard.manualJobs')}</span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
            {stopSlot === 'primary' && busy ? (
              stopButton
            ) : (
              <button type="button" style={actionBtn('primary', canJob)} disabled={!canJob} onClick={onBackup}>
                ▶ {t('dashboard.runBackup')}
              </button>
            )}
            {stopSlot === 'gc' && busy ? (
              stopButton
            ) : (
              <button type="button" style={actionBtn('ghost', canJob)} disabled={!canJob} onClick={onGc}>
                ⟳ {t('dashboard.runGc')}
              </button>
            )}
          </div>
        </div>
        <div>
          <span style={colHead}>{t('dashboard.power')}</span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
            <button type="button" style={actionBtn('green', !online)} disabled={online} onClick={onPowerOn}>
              ⏻ {t('dashboard.powerOn')}
            </button>
            <button type="button" style={actionBtn('red', canPower)} disabled={!canPower} onClick={onPowerOff}>
              ⏻ {t('dashboard.powerOff')}
            </button>
          </div>
        </div>
      </div>
      {error && (
        <div
          role="alert"
          style={{
            marginTop: 14,
            background: 'rgba(229,103,91,.1)',
            border: '1px solid rgba(229,103,91,.32)',
            borderRadius: 8,
            color: c.red,
            fontSize: 12.5,
            lineHeight: 1.5,
            padding: '9px 12px',
            wordBreak: 'break-word',
          }}
        >
          {error}
        </div>
      )}
    </div>
  )
}
