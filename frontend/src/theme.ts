// Shared design tokens, mirroring the prototype's palette (design/joulenap-remix).
// The design is inline-style heavy; we keep that idiom but centralise the colours.
export const c = {
  bg: '#0f1216',
  panel: '#171b21',
  panelAlt: '#13171d',
  border: '#232a32',
  borderSoft: '#1f262e',
  inputBg: '#0f1216',
  inputBorder: '#2b333c',
  text: '#e7eaee',
  textMid: '#c4cad1',
  textDim: '#8a929c',
  textFaint: '#7f8893',
  textMuted: '#5b636d',
  accent: '#e8830f',
  accentHover: '#f5953a',
  accentInk: '#1a1206',
  green: '#3fb27f',
  red: '#e5675b',
  blue: '#3b82f6',
  amber: '#e0a92b',
} as const

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
  border: `1px solid #3a434d`,
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
