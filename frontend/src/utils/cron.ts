// Bridge the dashboard's "time + weekday toggles" UI and the backend's cron string.
// Days are ordered Mon..Sun (index 0=Mon). Cron day-of-week is 0=Sun..6=Sat.

export interface Schedule {
  time: string // "HH:MM"
  days: boolean[] // length 7, Mon..Sun
}

const ALL_DAYS = [true, true, true, true, true, true, true]

function dowToIndex(token: string): number | null {
  const n = Number(token)
  if (Number.isNaN(n)) return null
  const sun0 = n % 7 // 7 -> 0 (Sunday)
  // cron 0=Sun..6=Sat  ->  our index 0=Mon..6=Sun
  return sun0 === 0 ? 6 : sun0 - 1
}

export function parseCron(schedule: string): Schedule {
  const parts = (schedule || '').trim().split(/\s+/)
  if (parts.length < 5) return { time: '02:00', days: [...ALL_DAYS] }
  const [min, hr, , , dow] = parts
  const time = `${String(hr).padStart(2, '0')}:${String(min).padStart(2, '0')}`
  if (dow === '*') return { time, days: [...ALL_DAYS] }
  const days = [false, false, false, false, false, false, false]
  for (const token of dow.split(',')) {
    const idx = dowToIndex(token)
    if (idx !== null) days[idx] = true
  }
  // No valid days parsed -> treat as every day so we never produce an unrunnable cron.
  if (!days.some(Boolean)) return { time, days: [...ALL_DAYS] }
  return { time, days }
}

export function buildCron({ time, days }: Schedule): string {
  const [hr, min] = time.split(':').map((n) => Number(n) || 0)
  const allOn = days.every(Boolean)
  if (allOn) return `${min} ${hr} * * *`
  // our index 0=Mon..6=Sun -> cron dow (Sun=0)
  const dow = days
    .map((on, i) => (on ? (i === 6 ? 0 : i + 1) : null))
    .filter((v): v is number => v !== null)
    .sort((a, b) => a - b)
    .join(',')
  return `${min} ${hr} * * ${dow || '*'}`
}
