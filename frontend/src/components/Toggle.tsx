import { c } from '../theme'

interface ToggleProps {
  on: boolean
  onClick: () => void
  size?: 'lg' | 'sm'
}

export function Toggle({ on, onClick, size = 'lg' }: ToggleProps) {
  const w = size === 'lg' ? 40 : 32
  const h = size === 'lg' ? 22 : 18
  const knob = size === 'lg' ? 16 : 12
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={onClick}
      style={{
        width: w,
        height: h,
        borderRadius: 999,
        background: on ? c.accent : c.inputBorder,
        position: 'relative',
        border: 'none',
        padding: 0,
        cursor: 'pointer',
        flex: '0 0 auto',
        transition: 'background .2s',
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: 3,
          left: on ? w - knob - 3 : 3,
          width: knob,
          height: knob,
          borderRadius: '50%',
          background: '#fff',
          transition: 'left .2s',
          display: 'block',
        }}
      />
    </button>
  )
}
