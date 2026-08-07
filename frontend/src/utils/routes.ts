// Route model -> what the homepage draws: the topology's wires, the strip's chips and
// badges, and the schedule one-liner. Pure functions with no React and no i18n, so they
// unit-test under `node --test` (the harness has no DOM).

import type { Route, RouteKind, RouteSchedule } from '../api/types.ts'

/**
 * Key of a device card in the topology. The wire layer holds a `Map<key, HTMLElement>` of
 * card refs and looks links up by these.
 *
 * Kind-prefixed rather than the bare device id: ids are free-form slugs scoped per kind, so
 * a PVE and a PBS may both legitimately be called `alpha`. Deliberately not a DOM `id`
 * either — nothing needs to query for them globally, and a ref map cannot collide with the
 * rest of the page.
 */
export const pveNodeId = (id: string) => `pve:${id}`
export const pbsNodeId = (id: string) => `pbs:${id}`

/** Whether a kind-prefixed key names a PBS. */
export const isPbsKey = (key: string) => key.startsWith('pbs:')

/** The bare device id inside a kind-prefixed key (`pbs:alpha` -> `alpha`). */
export const deviceId = (key: string) => key.slice(key.indexOf(':') + 1)

/**
 * The wires this route draws, as `[sourceNodeId, targetNodeId]` pairs.
 *
 * A backup route draws one per source PVE — that fan-in into a shared target is exactly
 * what the diagram exists to show. A sync route draws one PBS→PBS line. External and
 * Verify routes draw none: they have no source, they only wake their target, so a wire
 * would invent a data flow that does not happen.
 */
export function routeLinks(route: Route): [string, string][] {
  const target = pbsNodeId(route.target)
  if (route.kind === 'sync') {
    return route.source_pbs ? [[pbsNodeId(route.source_pbs), target]] : []
  }
  if (route.kind === 'backup') {
    return route.sources.map((s) => [pveNodeId(s.pve), target] as [string, string])
  }
  return []
}

/** Every device node this route touches — the set to keep lit when it is highlighted. */
export function routeDevices(route: Route): string[] {
  const ids = new Set<string>([pbsNodeId(route.target)])
  for (const [from] of routeLinks(route)) ids.add(from)
  return [...ids]
}

/** Does this route read from that PVE? Drives the "Queued · <route>" label on a PVE card. */
export function routeUsesPve(route: Route, pveId: string): boolean {
  return route.kind === 'backup' && route.sources.some((s) => s.pve === pveId)
}

/** Every PBS this route wakes: its target, plus a sync route's source box. */
export function routePbss(route: Route): string[] {
  const ids = [route.target]
  if (route.kind === 'sync' && route.source_pbs) ids.push(route.source_pbs)
  return ids
}

/** The device chips the strip shows, left of the arrow. */
export function routeSourceIds(route: Route): string[] {
  if (route.kind === 'sync') return route.source_pbs ? [route.source_pbs] : []
  return route.sources.map((s) => s.pve)
}

export interface KindBadge {
  /** Extra class on `.badge`; the default (Backup) has none. */
  cls: string
  labelKey: string
}

const KIND_BADGES: Record<RouteKind, KindBadge> = {
  backup: { cls: '', labelKey: 'dashboard.kindBackup' },
  sync: { cls: 'sync', labelKey: 'dashboard.kindSync' },
  external: { cls: 'ext', labelKey: 'dashboard.kindExternal' },
  verify: { cls: 'verify', labelKey: 'dashboard.kindVerify' },
}

export function routeKindBadge(kind: RouteKind): KindBadge {
  return KIND_BADGES[kind] ?? KIND_BADGES.backup
}

export interface ScheduleSummary {
  /** `HH:MM`, or "" for a route pinned to a raw cron. */
  time: string
  /** i18n key for the days part: everyDay / weekdays / weekends / a listed set / cron. */
  daysKey: string
  /** Interpolated into `daysKey` when it is the listed-set form. */
  days: string[]
  /** The verbatim crontab string when the route is cron-pinned, else "". */
  cron: string
}

const DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

/**
 * The strip's schedule line: `02:00 · every day`, `04:00 · Sat, Sun`, or the raw cron.
 *
 * `schedule.cron` wins over time/days when set — that is the backend's own precedence
 * (config.py RouteSchedule), and time/days keep meaningless defaults in that case, so
 * rendering them would be a lie about when the route fires.
 */
export function scheduleSummary(schedule: RouteSchedule): ScheduleSummary {
  if (schedule.cron) {
    return { time: '', daysKey: 'dashboard.scheduleCron', days: [], cron: schedule.cron }
  }
  const on = schedule.days.map((d, i) => (d ? DAY_KEYS[i] : '')).filter(Boolean)
  if (on.length === 7) {
    return { time: schedule.time, daysKey: 'dashboard.everyDay', days: on, cron: '' }
  }
  return { time: schedule.time, daysKey: 'dashboard.onDays', days: on, cron: '' }
}
