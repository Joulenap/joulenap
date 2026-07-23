// Small date/number formatters shared across the header and dashboard, matching the
// prototype's output. Relative strings are word-free so callers add "in"/"ago" via i18n.

import i18n from '../i18n/index.ts'

export const pad = (n: number) => String(n).padStart(2, '0')

export const fmtClock = (d: Date, sec = true) =>
  pad(d.getHours()) + ':' + pad(d.getMinutes()) + (sec ? ':' + pad(d.getSeconds()) : '')

// Localized short weekday for the active UI language (was a hardcoded English table).
const weekday = (d: Date) => new Intl.DateTimeFormat(i18n.language, { weekday: 'short' }).format(d)

export const fmtDT = (d: Date) =>
  `${weekday(d)} ${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(
    d.getMinutes(),
  )}`

export const fmtShort = (d: Date) =>
  `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`

export function rel(ms: number): string {
  const m = Math.round(Math.abs(ms) / 60000)
  if (m < 1) return '<1m'
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) {
    const mm = m % 60
    return `${h}h${mm ? ' ' + mm + 'm' : ''}`
  }
  // Roll multi-day deltas over to days, matching fmtUptime (was rendering e.g. "120h").
  const d = Math.floor(h / 24)
  const hh = h % 24
  return `${d}d${hh ? ' ' + hh + 'h' : ''}`
}

/**
 * Elapsed time for a run or one of its steps: `43s`, `1m 23s`, `2h 5m`.
 *
 * Not `rel()` — that rounds to whole minutes for "next run in 3h", which renders a 41-second
 * wake-wait as "1m" and a 2-second step as "<1m". Step timings are the diagnostic the run
 * detail exists for, so below an hour they keep their seconds.
 */
export function fmtDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${pad(s % 60)}s`
  return `${Math.floor(m / 60)}h ${pad(m % 60)}m`
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
