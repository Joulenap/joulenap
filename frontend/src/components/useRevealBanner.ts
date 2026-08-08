import { useEffect } from 'react'

/**
 * Scrolls the dialog's error banner into view when it appears. The banner mounts at the top
 * of the modal's scroll container while the user is at the bottom where the Next/Save button
 * is — without this, a failed validation looks like a dead click.
 */
export function useRevealBanner(active: boolean) {
  useEffect(() => {
    if (!active) return
    document.querySelector('.modal-scroll .form-banner')?.scrollIntoView({ block: 'nearest' })
  }, [active])
}
