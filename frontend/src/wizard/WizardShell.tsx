import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal } from '../components/Modal'
import { Spinner } from '../components/Spinner'
import { type Flow, FLOW_STEPS, nextLabelKey } from '../utils/wizardFlow'

interface WizardShellProps {
  flow: Flow
  title: string
  step: number
  /** Steps that do not apply to this run, so the indicator does not promise a step that will
   *  be hopped over (flow A's configure-PBS, flow B's wake-up/power-off). */
  skipped?: number[]
  busy?: boolean
  /** Set once the devices have been created: the last step is terminal, because Back would
   *  offer to change a decision that is already on disk. */
  done?: boolean
  /** Blocks Next without disabling it: the click is what surfaces the errors. */
  onBack: () => void
  onNext: () => void
  onClose: () => void
  children: ReactNode
}

/**
 * The multi-step chrome of both wizard flows: the numbered indicator under the header and the
 * Cancel / ← Back / Next footer, on the shared `Modal` shell (so Escape, the Tab trap and focus
 * restoration come from `useDialogKeys` like every other dialog).
 *
 * It owns no state. Which step is current, which are skipped and what Next does are all decided
 * by the flow — the shell only draws the answer, so `utils/wizardFlow.ts` stays the one place
 * the navigation rules live.
 */
export function WizardShell({
  flow,
  title,
  step,
  skipped = [],
  busy = false,
  done = false,
  onBack,
  onNext,
  onClose,
  children,
}: WizardShellProps) {
  const { t } = useTranslation()
  const steps = FLOW_STEPS[flow]

  return (
    <Modal
      size="lg"
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" style={{ marginRight: 'auto' }} onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button
            type="button"
            className="btn"
            disabled={step === 0 || busy || done}
            onClick={onBack}
          >
            {t('wizard.nav.back')}
          </button>
          <button type="button" className="btn btn-accent" disabled={busy} onClick={onNext}>
            {busy && <Spinner size={12} />}
            {t(nextLabelKey(flow, step))}
          </button>
        </>
      }
    >
      <div className="wsteps">
        {steps.map((key, i) => {
          const shown = i + 1
          return (
            <span key={key} style={{ display: 'contents' }}>
              {i > 0 && <span className="wstep-line" />}
              <span
                className={`wstep${i === step ? ' cur' : ''}${i < step && !skipped.includes(i) ? ' done' : ''}`}
                style={skipped.includes(i) ? { opacity: 0.4 } : undefined}
              >
                <span className="sn">{shown}</span>
                <span className="sl">{t(`wizard.step.${flow}.${key}`)}</span>
              </span>
            </span>
          )
        })}
      </div>
      {children}
    </Modal>
  )
}
