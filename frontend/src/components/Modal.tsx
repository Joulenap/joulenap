import { useEffect, useId, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useDialogKeys } from './useDialogKeys'

interface ModalProps {
  title: string
  onClose: () => void
  /** The buttons; laid out right-aligned by `.modal-foot`, so anything pinned left needs
   *  `margin-right:auto` (the route editor's Delete does exactly that). */
  footer: ReactNode
  /** `lg` is the 700px route editor; the default 420px is the small action dialogs. */
  size?: 'sm' | 'lg'
  /** Where focus starts. Without it, useDialogKeys falls back to the first focusable —
   *  which is the ✕, a poor introduction for a screen reader. Point it at the first field. */
  initialFocusRef?: React.RefObject<HTMLElement | null>
  children: ReactNode
}

/**
 * The mockup's dialog shell (`design/overhaul/src/home2.html`), class-based like the rest of
 * the homepage: backdrop, scrollable layer, header with the ✕, body slot, footer.
 *
 * It is a shell and nothing else — every dialog on this page supplies its own body, which is
 * why the route editor and the five action dialogs share it instead of each carrying an
 * overlay. Escape / Tab-trap / focus restoration come from `useDialogKeys`; closing on the
 * backdrop is the same `onClose`, so a guarded surface (the route editor's unsaved-changes
 * prompt) intercepts every exit at one point.
 */
export function Modal({ title, onClose, footer, size = 'sm', initialFocusRef, children }: ModalProps) {
  const { t } = useTranslation()
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)

  useDialogKeys(true, dialogRef, onClose, initialFocusRef)

  // Stop the page behind the dialog from scrolling under it.
  useEffect(() => {
    document.body.classList.add('modal-open')
    return () => document.body.classList.remove('modal-open')
  }, [])

  return (
    <div className="modal-layer open" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="modal-backdrop" onClick={onClose} />
      <div className={`modal-scroll${size === 'lg' ? ' modal-lg-scroll' : ''}`}>
        <div ref={dialogRef} className={`modal${size === 'lg' ? ' modal-lg' : ''}`}>
          <div className="modal-hd">
            <h2 id={titleId}>{title}</h2>
            <button type="button" className="modal-x" aria-label={t('common.close')} onClick={onClose}>
              ✕
            </button>
          </div>
          {children}
          <div className="modal-foot">{footer}</div>
        </div>
      </div>
    </div>
  )
}
