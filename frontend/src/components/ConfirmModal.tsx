import { useId, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { c } from '../theme'
import { Toggle } from './Toggle'
import { useDialogKeys } from './useDialogKeys'

export interface ConfirmState {
  title: string
  message: string
  confirmLabel: string
  danger: boolean
  icon: string
  onConfirm: () => void
  // Optional switch shown above the buttons (e.g. "Keep PBS on after the job").
  toggle?: { label: string; value: boolean; onChange: (v: boolean) => void }
}

export function ConfirmModal({ state, onCancel }: { state: ConfirmState | null; onCancel: () => void }) {
  const { t } = useTranslation()
  const titleId = useId()
  const msgId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  // Focus the non-destructive Cancel button so a stray Enter/Space can't fire a danger action.
  const cancelRef = useRef<HTMLButtonElement>(null)

  useDialogKeys(state !== null, dialogRef, onCancel, cancelRef)

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
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={msgId}
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 430,
          maxWidth: '100%',
          background: c.menuBg,
          border: `1px solid ${c.menuBorder}`,
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
          <span id={titleId} style={{ fontSize: 16, fontWeight: 700 }}>{state.title}</span>
        </div>
        {/* pre-line so a message can list what it is warning about, one item per line. */}
        <p
          id={msgId}
          style={{
            margin: '0 0 20px',
            fontSize: 13.5,
            lineHeight: 1.55,
            color: c.textMid,
            whiteSpace: 'pre-line',
          }}
        >
          {state.message}
        </p>
        {state.toggle && (
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              margin: '0 0 18px',
              fontSize: 13.5,
              color: c.textMid,
              cursor: 'pointer',
            }}
          >
            <Toggle on={state.toggle.value} onClick={() => state.toggle!.onChange(!state.toggle!.value)} size="sm" />
            <span>{state.toggle.label}</span>
          </label>
        )}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button
            ref={cancelRef}
            onClick={onCancel}
            style={{
              background: 'transparent',
              color: c.textMid,
              border: `1px solid ${c.ghostBorder}`,
              borderRadius: 8,
              padding: '9px 18px',
              fontSize: 13.5,
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
              fontSize: 13.5,
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
