import { c, tint } from '../theme'

export function Spinner({ size = 16, color = c.accent }: { size?: number; color?: string }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        border: `2px solid ${tint(color, 20)}`,
        borderTopColor: color,
        animation: 'spin .7s linear infinite',
      }}
    />
  )
}

export function FullPageSpinner() {
  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <Spinner size={28} />
    </div>
  )
}
