import { useEffect } from 'react'

/**
 * Scrolls an error banner into view when it appears (or changes). The banner mounts at the
 * top of its scroll container while the user is at the bottom where the button they pressed
 * is — without this, a failure looks like a dead click.
 *
 * `active` is the error value itself, not just a boolean: a *different* failure while the
 * banner is already showing must re-reveal it, and only the value can tell that apart.
 */
export function useRevealBanner(active: unknown, selector = '.modal-scroll .form-banner') {
  useEffect(() => {
    if (!active) return
    document.querySelector(selector)?.scrollIntoView({ block: 'nearest' })
  }, [active, selector])
}
