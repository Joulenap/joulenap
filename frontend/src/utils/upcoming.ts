// "Upcoming runs": which rows the elastic right-rail panel shows, and in what order.
//
// The rule from the design: every enabled route is always represented (plus the run in
// flight), and only *after* that does the list use the space it has left, whole rows at a
// time, on later occurrences. So the panel answers "when does each route next run" first
// and "what is coming up" second — never the other way round.

import type { Route, RouteSchedule } from '../api/types.ts'

export interface UpcomingRow {
  /** Stable across re-renders and unique within the list. */
  key: string
  routeId: string
  routeName: string
  color: string
  /** ISO timestamp, or null for the row of the run currently in flight. */
  at: string | null
  /** True for the in-flight run: rendered in accent, sorted to the top. */
  now: boolean
}

const DAY_MS = 86_400_000

/**
 * The next `count` firings of a time+days schedule strictly after `after`.
 *
 * Local time on purpose: `schedule.time` is wall-clock in the server's zone, and the rows
 * next to it are formatted locally too. A cron-pinned route yields nothing — `next_runs`
 * from the backend already carries its one real next firing, and re-deriving a crontab here
 * would be a second, divergent implementation of APScheduler.
 */
export function nextOccurrences(schedule: RouteSchedule, after: Date, count: number): Date[] {
  if (schedule.cron || count <= 0) return []
  const [h, m] = schedule.time.split(':').map(Number)
  if (!Number.isFinite(h) || !Number.isFinite(m)) return []
  if (!schedule.days.some(Boolean)) return []

  const out: Date[] = []
  const cursor = new Date(after.getFullYear(), after.getMonth(), after.getDate(), h, m, 0, 0)
  // Scan day by day. The sparsest legal schedule is a single weekday, so `count` firings
  // can be up to 7*count days out (+1 for a today-but-already-past start) — a `count + 7`
  // bound silently returns a short list for a weekly route.
  for (let i = 0; i <= count * 7 && out.length < count; i++) {
    const day = new Date(cursor.getTime() + i * DAY_MS)
    // JS weeks start on Sunday; schedule.days is Mon..Sun.
    const mondayIndex = (day.getDay() + 6) % 7
    if (schedule.days[mondayIndex] && day.getTime() > after.getTime()) out.push(day)
  }
  return out
}

export interface UpcomingInput {
  /** The in-flight run, if any — always the first row. */
  running: { routeId: string | null; routeName: string } | null
  /** `status.next_runs`: one entry per armed route, soonest first. */
  nextRuns: { route_id: string; route_name: string; at: string }[]
  routes: Route[]
  /** How many whole rows fit in the panel right now (from the ResizeObserver). */
  capacity: number
}

/**
 * Build the rows.
 *
 * The minimum is not negotiable: the running row plus one per `nextRuns` entry, even when
 * the panel is too short for them — a route silently missing from this list reads as "not
 * scheduled", which is exactly the wrong message. Beyond the minimum, later occurrences are
 * merged chronologically and taken only while they fit.
 */
export function upcomingRows(input: UpcomingInput): UpcomingRow[] {
  const { running, nextRuns, routes, capacity } = input
  const byId = new Map(routes.map((r) => [r.id, r]))
  const color = (id: string) => byId.get(id)?.color ?? 'var(--jn-text-muted)'

  const rows: UpcomingRow[] = []
  if (running) {
    rows.push({
      key: 'running',
      routeId: running.routeId ?? '',
      routeName: running.routeName,
      color: running.routeId ? color(running.routeId) : 'var(--jn-accent)',
      at: null,
      now: true,
    })
  }
  for (const n of nextRuns) {
    rows.push({
      key: `${n.route_id}@${n.at}`,
      routeId: n.route_id,
      routeName: n.route_name,
      color: color(n.route_id),
      at: n.at,
      now: false,
    })
  }

  const extra = Math.max(0, capacity - rows.length)
  if (extra === 0) return rows

  // Ask each route for enough occurrences to fill the panel on its own — one daily route
  // can legitimately own every remaining row — then merge and cut.
  const seen = new Set(rows.map((r) => r.key))
  const pool: UpcomingRow[] = []
  for (const n of nextRuns) {
    const route = byId.get(n.route_id)
    if (!route) continue
    for (const at of nextOccurrences(route.schedule, new Date(n.at), extra)) {
      const iso = at.toISOString()
      const key = `${route.id}@${iso}`
      if (seen.has(key)) continue
      seen.add(key)
      pool.push({
        key,
        routeId: route.id,
        routeName: n.route_name,
        color: route.color,
        at: iso,
        now: false,
      })
    }
  }
  pool.sort((a, b) => (a.at ?? '').localeCompare(b.at ?? ''))
  return rows.concat(pool.slice(0, extra))
}
