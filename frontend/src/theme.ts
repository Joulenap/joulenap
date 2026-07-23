// Shared design tokens, mirroring the prototype's palette (design/joulenap-remix).
// The design is inline-style heavy; we keep that idiom but centralise the colours.
// Values are CSS variables so the dark/light palettes (defined in index.css) switch by
// flipping data-theme on <html> — no re-render, and CodeMirror's generated CSS follows too.
// Consequence: a token is not a hex literal, so never string-append an alpha suffix to one;
// use `tint()` below instead.
export const c = {
  bg: 'var(--jn-bg)',
  panel: 'var(--jn-panel)',
  panelAlt: 'var(--jn-panel-alt)',
  border: 'var(--jn-border)',
  borderSoft: 'var(--jn-border-soft)',
  inputBg: 'var(--jn-input-bg)',
  inputBorder: 'var(--jn-input-border)',
  text: 'var(--jn-text)',
  textMid: 'var(--jn-text-mid)',
  textDim: 'var(--jn-text-dim)',
  textFaint: 'var(--jn-text-faint)',
  textMuted: 'var(--jn-text-muted)',
  accent: 'var(--jn-accent)',
  accentHover: 'var(--jn-accent-hover)',
  accentInk: 'var(--jn-accent-ink)',
  green: 'var(--jn-green)',
  red: 'var(--jn-red)',
  blue: 'var(--jn-blue)',
  amber: 'var(--jn-amber)',
  info: 'var(--jn-info)',
  btnBg: 'var(--jn-btn-bg)',
  btnBorder: 'var(--jn-btn-border)',
  ghostBorder: 'var(--jn-ghost-border)',
  divider: 'var(--jn-divider)',
  hover: 'var(--jn-hover)',
  menuBg: 'var(--jn-menu-bg)',
  menuBorder: 'var(--jn-menu-border)',
} as const

// Translucent wash of a token (replaces the old `${hex}33` concatenation, which cannot
// work on a var() reference).
export const tint = (color: string, pct: number) => `color-mix(in srgb, ${color} ${pct}%, transparent)`

export type ThemeMode = 'dark' | 'light'

// Chrome-tab / mobile-UI color per theme; must match --jn-bg in index.css.
const META_BG: Record<ThemeMode, string> = { dark: '#0f1216', light: '#f3f4f6' }

let fadeTimer: ReturnType<typeof setTimeout> | undefined

export function applyTheme(mode: ThemeMode) {
  const el = document.documentElement
  if (mode !== currentTheme()) {
    // Cross-fade the swap: the class arms the transition rule in index.css for its duration.
    el.classList.add('theme-fade')
    clearTimeout(fadeTimer)
    fadeTimer = setTimeout(() => el.classList.remove('theme-fade'), 300)
  }
  el.dataset.theme = mode
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', META_BG[mode])
  try {
    localStorage.setItem('jnTheme', mode)
  } catch {
    /* storage may be unavailable (private mode) — theme still applies for the session */
  }
}

export function currentTheme(): ThemeMode {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'
}

export const mono = "'IBM Plex Mono', monospace"

// Uppercase field-label style used throughout the design.
export const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: '.08em',
  textTransform: 'uppercase',
  color: c.textFaint,
  marginBottom: 6,
}

export const inputStyle: React.CSSProperties = {
  width: '100%',
  background: c.inputBg,
  border: `1px solid ${c.inputBorder}`,
  borderRadius: 7,
  color: c.text,
  padding: '10px 12px',
  fontFamily: "'IBM Plex Sans', sans-serif",
  fontSize: 14,
}

export const primaryBtn: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 8,
  background: c.accent,
  color: c.accentInk,
  border: 'none',
  borderRadius: 8,
  padding: '11px',
  fontSize: 13,
  fontWeight: 600,
  cursor: 'pointer',
}

export const ghostBtn: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 8,
  background: 'transparent',
  color: c.text,
  border: `1px solid ${c.ghostBorder}`,
  borderRadius: 8,
  padding: '11px',
  fontSize: 13,
  fontWeight: 600,
  cursor: 'pointer',
}

export const panelStyle: React.CSSProperties = {
  background: c.panel,
  border: `1px solid ${c.border}`,
  borderRadius: 12,
}
