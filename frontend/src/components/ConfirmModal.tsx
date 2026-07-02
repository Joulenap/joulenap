import { useTranslation } from 'react-i18next'
import { c } from '../theme'

export interface ConfirmState {
  title: string
  message: string
  confirmLabel: string
  danger: boolean
  icon: string
  onConfirm: () => void
}

export function ConfirmModal({ state, onCancel }: { state: ConfirmState | null; onCancel: () => void }) {
  const { t } = useTranslation()
  if (!state) return null
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(8,10,13,.66)',
        backdropFilter: 'blur(3px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 50,
        padding: 20,
      }}
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 430,
          maxWidth: '100%',
          background: '#191e25',
          border: '1px solid #2c343d',
          borderRadius: 14,
          padding: 22,
          boxShadow: '0 24px 60px rgba(0,0,0,.5)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <div
            style={{
              width: 34,
              height: 34,
              flex: '0 0 auto',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: 9,
              fontSize: 16,
              background: state.danger ? 'rgba(216,70,60,.15)' : 'rgba(232,131,15,.15)',
              color: state.danger ? c.red : c.accent,
            }}
          >
            {state.icon}
          </div>
          <span style={{ fontSize: 17, fontWeight: 700 }}>{state.title}</span>
        </div>
        <p style={{ margin: '0 0 20px', fontSize: 14, lineHeight: 1.55, color: '#a8b0ba' }}>
          {state.message}
        </p>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            style={{
              background: 'transparent',
              color: c.textMid,
              border: '1px solid #3a434d',
              borderRadius: 8,
              padding: '9px 18px',
              fontSize: 13,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={() => {
              state.onConfirm()
              onCancel()
            }}
            style={{
              background: state.danger ? '#d8463c' : c.accent,
              color: state.danger ? '#fff' : c.accentInk,
              border: 'none',
              borderRadius: 8,
              padding: '9px 18px',
              fontSize: 13,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            {state.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
