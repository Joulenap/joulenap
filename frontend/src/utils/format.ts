// Small date/number formatters shared across the header and dashboard, matching the
// prototype's output. Relative strings are word-free so callers add "in"/"ago" via i18n.

export const pad = (n: number) => String(n).padStart(2, '0')

export const fmtClock = (d: Date, sec = true) =>
  pad(d.getHours()) + ':' + pad(d.getMinutes()) + (sec ? ':' + pad(d.getSeconds()) : '')

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

export const fmtDT = (d: Date) =>
  `${DAYS[d.getDay()]} ${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(
    d.getMinutes(),
  )}`

export const fmtShort = (d: Date) =>
  `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`

export function rel(ms: number): string {
  const m = Math.round(Math.abs(ms) / 60000)
  if (m < 1) return '<1m'
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  const mm = m % 60
  return `${h}h${mm ? ' ' + mm + 'm' : ''}`
}

export function fmtBytesTB(n: number): string {
  const tb = n / 1e12
  if (tb >= 1) return tb.toFixed(2) + ' TB'
  return (n / 1e9).toFixed(2) + ' GB'
}

// Uptime in seconds -> compact "Nd Nh" / "Nh Nm" / "Nm" (how long the PBS has been awake).
export function fmtUptime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  return `${m}m`
}
