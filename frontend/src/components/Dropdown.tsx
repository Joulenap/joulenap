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

/**
 * A styled native `<select>`. It used to be a hand-rolled button+menu, which had none of the
 * keyboard/ARIA behavior a select gets for free (Escape, arrow keys, type-ahead, aria-expanded)
 * — the menu itself now renders as the OS one, which is the trade.
 */
export function Dropdown({ value, options, onChange, width = '100%', mono: useMono }: Props) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{
        width,
        background: c.inputBg,
        border: `1px solid ${c.inputBorder}`,
        borderRadius: 7,
        color: c.text,
        padding: '10px 12px',
        fontFamily: useMono ? mono : "'IBM Plex Sans', sans-serif",
        fontSize: 13.5,
        cursor: 'pointer',
      }}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}
