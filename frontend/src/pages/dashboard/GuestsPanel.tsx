import { useTranslation } from 'react-i18next'
import type { GuestInfo } from '../../api/types'
import { Toggle } from '../../components/Toggle'
import { c, mono, panelStyle } from '../../theme'
import { fmtShort } from '../../utils/format'

interface Props {
  guests: GuestInfo[]
  mode: 'general' | 'selective'
  onModeChange: (m: 'general' | 'selective') => void
  selected: Set<number>
  onToggleGuest: (vmid: number) => void
  onRefresh: () => void
  refreshing: boolean
}

function modeBtn(active: boolean): React.CSSProperties {
  return {
    flex: 1,
    textAlign: 'center',
    background: active ? c.accent : 'transparent',
    color: active ? c.accentInk : '#9aa2ac',
    border: 'none',
    borderRadius: 6,
    padding: '7px 4px',
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
  }
}

export function GuestsPanel({ guests, mode, onModeChange, selected, onToggleGuest, onRefresh, refreshing }: Props) {
  const { t } = useTranslation()
  const selective = mode === 'selective'
  const count =
    selective ? `${guests.length} · ${t('dashboard.selectedCount', { n: selected.size })}` : `${guests.length}`

  return (
    <div style={{ ...panelStyle, padding: '8px 0 8px', alignSelf: 'stretch' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 18px 12px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 9 }}>
          <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: '.04em' }}>{t('dashboard.guests')}</span>
          <span style={{ fontFamily: mono, fontSize: 11, color: '#6f7884' }}>{count}</span>
        </div>
        <button
          onClick={onRefresh}
          title="Refresh"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 28,
            height: 28,
            background: '#1d232b',
            border: `1px solid ${c.inputBorder}`,
            borderRadius: 7,
            color: c.textMid,
            cursor: 'pointer',
            fontSize: 15,
          }}
        >
          <span style={{ display: 'inline-block', animation: refreshing ? 'spin .9s linear infinite' : 'none' }}>⟳</span>
        </button>
      </div>

      <div style={{ padding: '0 18px 12px' }}>
        <div style={{ display: 'flex', background: c.inputBg, border: `1px solid ${c.inputBorder}`, borderRadius: 8, padding: 3, gap: 3 }}>
          <button style={modeBtn(!selective)} onClick={() => onModeChange('general')}>
            {t('dashboard.general')}
          </button>
          <button style={modeBtn(selective)} onClick={() => onModeChange('selective')}>
            {t('dashboard.selective')}
          </button>
        </div>
      </div>

      <div style={{ maxHeight: 250, overflowY: 'auto', overflowX: 'hidden' }}>
        {guests.map((g) => {
          const isCt = g.type === 'lxc'
          const on = selected.has(g.vmid)
          return (
            <div
              key={g.vmid}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '8px 18px',
                borderBottom: '1px solid #1b212880',
              }}
            >
              {selective && <Toggle on={on} onClick={() => onToggleGuest(g.vmid)} size="sm" />}
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flex: '0 0 auto',
                  width: 26,
                  fontFamily: mono,
                  fontSize: 10,
                  fontWeight: 600,
                  padding: '2px 0',
                  borderRadius: 5,
                  color: isCt ? c.green : '#6aa6e8',
                  background: isCt ? 'rgba(63,178,127,.14)' : 'rgba(106,166,232,.14)',
                }}
              >
                {isCt ? 'CT' : 'VM'}
              </span>
              <span style={{ fontFamily: mono, fontSize: 11, color: '#6f7884', width: 30, flex: '0 0 auto' }}>{g.vmid}</span>
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  fontSize: 13,
                  color: selective && !on ? c.textMuted : c.text,
                  fontWeight: 500,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {g.name}
              </span>
              <span
                title={t('dashboard.lastBackupTitle')}
                style={{
                  fontFamily: mono,
                  fontSize: 11,
                  color: g.last_backup ? c.textFaint : c.textMuted,
                  flex: '0 0 auto',
                }}
              >
                {g.last_backup ? fmtShort(new Date(g.last_backup)) : t('dashboard.never')}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
