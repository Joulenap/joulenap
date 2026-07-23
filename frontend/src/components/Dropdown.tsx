import { useState } from 'react'
import { c, mono } from '../theme'

export interface Option {
  value: string
  label: string
}

interface Props {
  value: string
  options: Option[]
  onChange: (value: string) => void
  width?: number | string
  mono?: boolean
}

export function Dropdown({ value, options, onChange, width = '100%', mono: useMono }: Props) {
  const [open, setOpen] = useState(false)
  const current = options.find((o) => o.value === value)

  return (
    <div style={{ position: 'relative', width }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 10,
          width: '100%',
          background: c.inputBg,
          border: `1px solid ${c.inputBorder}`,
          borderRadius: 7,
          color: c.text,
          padding: '10px 12px',
          fontFamily: useMono ? mono : "'IBM Plex Sans', sans-serif",
          fontSize: 14,
          cursor: 'pointer',
        }}
      >
        <span>{current?.label ?? value ?? '—'}</span>
        <span style={{ color: c.textFaint, fontSize: 9 }}>▼</span>
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 30 }} />
          <div
            style={{
              position: 'absolute',
              top: 'calc(100% + 4px)',
              left: 0,
              right: 0,
              zIndex: 31,
              background: c.menuBg,
              border: `1px solid ${c.inputBorder}`,
              borderRadius: 7,
              overflowY: 'auto',
              overflowX: 'hidden',
              maxHeight: '40vh',
              boxShadow: '0 12px 30px rgba(0,0,0,.45)',
            }}
          >
            {options.map((o) => (
              <button
                key={o.value}
                type="button"
                onClick={() => {
                  onChange(o.value)
                  setOpen(false)
                }}
                style={{
                  display: 'block',
                  width: '100%',
                  textAlign: 'left',
                  background: o.value === value ? 'rgba(232,131,15,.12)' : 'transparent',
                  color: o.value === value ? c.text : c.textMid,
                  border: 'none',
                  padding: '9px 12px',
                  fontSize: 14,
                  cursor: 'pointer',
                }}
              >
                {o.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
