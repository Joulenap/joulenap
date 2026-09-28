import { useEffect, useRef, type RefObject } from 'react'

// Open dialogs, most recent last. Every open dialog listens on the document, so without this
// Escape on a confirm also closed the dialog under it (the route editor, a whole wizard).
const openDialogs: symbol[] = []

export function pushDialog(): symbol {
  const id = Symbol('dialog')
  openDialogs.push(id)
  return id
}

export function popDialog(id: symbol): void {
  const i = openDialogs.indexOf(id)
  if (i >= 0) openDialogs.splice(i, 1)
}

/** Only the dialog on top answers Escape and Tab. */
export function isTopDialog(id: symbol): boolean {
  return openDialogs[openDialogs.length - 1] === id
}

/**
 * The keyboard and focus half of a modal dialog: Escape closes, Tab cycles inside it, focus
 * starts somewhere safe and returns to whatever opened it.
 *
 * Shared by `ConfirmModal` and the route/action `Modal` shell so there is one implementation
 * of the trap rather than one per dialog — the two look nothing alike but behave identically.
 *
 * `onClose` is read through a ref, so a parent that rebuilds its dialog state on every
 * keystroke (a toggle flip, a form edit) doesn't re-run the effect or capture a stale closure.
 */
export function useDialogKeys(
  open: boolean,
  dialogRef: RefObject<HTMLElement | null>,
  onClose: () => void,
  initialFocusRef?: RefObject<HTMLElement | null>,
): void {
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  const initialRef = useRef(initialFocusRef)
  initialRef.current = initialFocusRef

  useEffect(() => {
    if (!open) return
    const id = pushDialog()
    const previouslyFocused = document.activeElement as HTMLElement | null

    const focusables = () =>
      dialogRef.current
        ? Array.from(
            dialogRef.current.querySelectorAll<HTMLElement>(
              'button, [href], input, select, textarea, summary, [tabindex]:not([tabindex="-1"])',
            ),
          ).filter((el) => !el.hasAttribute('disabled'))
        : []

    // Deliberately not the confirm button: a stray Enter must never fire a destructive action
    // the user hasn't read yet.
    ;(initialRef.current?.current ?? focusables()[0])?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (!isTopDialog(id)) return
      if (e.key === 'Escape') {
        e.preventDefault()
        onCloseRef.current()
      } else if (e.key === 'Tab') {
        const f = focusables()
        if (!f.length) return
        const first = f[0]
        const last = f[f.length - 1]
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      popDialog(id)
      document.removeEventListener('keydown', onKey)
      previouslyFocused?.focus?.()
    }
  }, [open, dialogRef])
}
