// Copies text to the clipboard. Prefers the async Clipboard API, but that only
// exists in a secure context (HTTPS or localhost) — on a plain-HTTP LAN origin
// (e.g. http://192.168.x.x:8080, which is how this app is commonly reached)
// navigator.clipboard is undefined and writeText throws/no-ops. In that case we
// fall back to a hidden <textarea> + document.execCommand('copy'), which works
// regardless of secure-context.
//
// Globals are read via bare identifiers (typeof-guarded) rather than destructured
// at module scope, so tests can stub window/navigator/document per-case.
export async function copyToClipboard(text: string): Promise<boolean> {
  const hasSecureClipboard =
    typeof window !== 'undefined' &&
    Boolean(window.isSecureContext) &&
    typeof navigator !== 'undefined' &&
    typeof navigator.clipboard?.writeText === 'function'

  if (hasSecureClipboard) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Fall through to the textarea fallback below.
    }
  }

  if (typeof document === 'undefined' || typeof document.createElement !== 'function' || !document.body) {
    return false
  }

  try {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    textarea.style.pointerEvents = 'none'
    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(textarea)
    return Boolean(ok)
  } catch {
    return false
  }
}
