import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal } from '../../components/Modal'
import { Toggle } from '../../components/Toggle'

export interface ActionOption {
  value: string
  label: string
}

/**
 * What one of the homepage's small dialogs is (the mockup's `ACTIONS` table): a title, an
 * optional explanatory note, an optional "which one?" select, an optional power toggle and a
 * confirm button that is accent or danger.
 *
 * `onConfirm` gets the chosen option's value and the toggle's state as the user left them.
 * The toggle always reads "power off the PBS when finished" — the endpoints spell that
 * `keep_on` (inverted) or `power_off` (direct) depending on which one it is, and translating
 * between the two is the caller's job, at the call site where it is visible.
 */
export interface ActionSpec {
  title: string
  note?: string
  select?: { label: string; options: ActionOption[]; emptyNote: string }
  /** Absent = no toggle at all (the power-on/off confirms have nothing to decide). */
  toggle?: string
  confirmLabel: string
  danger?: boolean
  onConfirm: (choice: string, powerOff: boolean) => void
}

export function ActionDialog({ spec, onClose }: { spec: ActionSpec; onClose: () => void }) {
  const { t } = useTranslation()
  const selectId = useId()
  const [choice, setChoice] = useState(spec.select?.options[0]?.value ?? '')
  // Checked by default: the whole point of the app is that the box goes back to sleep.
  const [powerOff, setPowerOff] = useState(true)

  // A dialog whose only subject list is empty has nothing to act on; say so instead of
  // offering an empty select and a button that would 404.
  const empty = !!spec.select && spec.select.options.length === 0

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
            disabled={empty}
            onClick={() => {
              spec.onConfirm(choice, powerOff)
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
        {spec.select && (
          <div>
            <span className="lab" id={selectId}>
              {spec.select.label}
            </span>
            {empty ? (
              <p className="modal-note">{spec.select.emptyNote}</p>
            ) : (
              <select
                aria-labelledby={selectId}
                value={choice}
                onChange={(e) => setChoice(e.target.value)}
              >
                {spec.select.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}
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
