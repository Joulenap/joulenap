import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal } from '../../components/Modal'
import { Toggle } from '../../components/Toggle'

/**
 * What one of the homepage's small dialogs is (the mockup's `ACTIONS` table): a title, an
 * optional explanatory note, an optional power toggle and a confirm button that is accent
 * or danger. Every dialog is pre-targeted by the control that opened it (a route card, a
 * PBS card, a history row), so there is no "which one?" select anymore.
 *
 * `onConfirm` gets the toggle's state as the user left it. The toggle always reads "power
 * off the PBS when finished" — the endpoints spell that `keep_on` (inverted) or `power_off`
 * (direct) depending on which one it is, and translating between the two is the caller's
 * job, at the call site where it is visible.
 */
export interface ActionSpec {
  title: string
  note?: string
  /** Absent = no toggle at all (the power-on/off confirms have nothing to decide). */
  toggle?: string
  confirmLabel: string
  danger?: boolean
  onConfirm: (powerOff: boolean) => void
}

export function ActionDialog({ spec, onClose }: { spec: ActionSpec; onClose: () => void }) {
  const { t } = useTranslation()
  // Checked by default: the whole point of the app is that the box goes back to sleep.
  const [powerOff, setPowerOff] = useState(true)

  return (
    <Modal
      title={spec.title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button
            type="button"
            className={`btn ${spec.danger ? 'btn-danger-solid' : 'btn-accent'}`}
            onClick={() => {
              spec.onConfirm(powerOff)
              onClose()
            }}
          >
            {spec.confirmLabel}
          </button>
        </>
      }
    >
      <div className="modal-bd">
        {spec.note && <p className="modal-note">{spec.note}</p>}
        {spec.toggle && (
          <label className="tglrow">
            <Toggle size="sm" on={powerOff} onClick={() => setPowerOff(!powerOff)} />
            <span>{spec.toggle}</span>
          </label>
        )}
      </div>
    </Modal>
  )
}
