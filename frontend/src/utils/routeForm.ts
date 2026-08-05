// The route editor's whole rule set, as pure functions.
//
// The test harness is `node --test` with no DOM, so nothing inside a component can be
// covered. Everything that decides something — which kind a device selection implies, which
// sections that kind shows, what the guest counter says, whether the form may be saved, which
// other routes it will fight with over retention — lives here, and RouteModal only renders it.

import type { ApiError } from '../api/client.ts'
import type {
  PbsDevice,
  PveDevice,
  RetentionConfig,
  Route,
  RouteKind,
  RouteOptions,
  RouteSource,
} from '../api/types.ts'
import type { PveGuests } from './guestPanel.ts'
import { deviceId, isPbsKey, pbsNodeId, pveNodeId } from './routes.ts'

/** The six preset swatches (NOTES decision 2), as the hex `Route.color` actually stores.
 *  The mockup keeps `var(--jn-…)` strings, which the backend's `^#[0-9a-fA-F]{6}$` rejects;
 *  these are the dark palette's values, which is what the existing routes use. */
export const ROUTE_COLORS = [
  '#e8830f', // accent
  '#3b82f6', // blue
  '#e0a92b', // amber
  '#9d7bd8', // violet
  '#2fa7a0', // teal
  '#d06ca8', // pink
]

export const DEFAULT_RETENTION: RetentionConfig = {
  keep_last: 0,
  keep_daily: 7,
  keep_weekly: 4,
  keep_monthly: 6,
  keep_yearly: 0,
}

export const DEFAULT_OPTIONS: RouteOptions = {
  mode: 'snapshot',
  bwlimit: 0,
  min_free_percent: 0,
  gc: true,
  verify_after: false,
  reverify_days: 30,
}

/**
 * The form's state. Flatter than `Route` on purpose: the modal edits one list of source
 * device ids (PVE and PBS in the same chip row) and derives the kind from it, so `sources` /
 * `source_pbs` / `kind` only exist again at save time.
 */
export interface RouteDraft {
  /** "" for a route that does not exist yet; an edit keeps the stored id so history stays attached. */
  id: string
  name: string
  color: string
  enabled: boolean
  notify: boolean
  /**
   * Kind-prefixed device keys — `pve:alpha`, `pbs:alpha` — not bare ids.
   *
   * Device ids are unique only *within* `pves` and *within* `pbss` (the backend checks each
   * list separately), so a PVE and a PBS may both be called `alpha`. A flat list of bare ids
   * made the two indistinguishable: selecting the PVE also lit the PBS chip, flipped the kind
   * to Sync, dropped the PVE source and saved a route that synced a box to itself. The
   * topology has always keyed its cards this way; the editor now agrees with it.
   */
  sourceIds: string[]
  target: string
  /** Only consulted when there are no sources at all. */
  onWake: 'external' | 'verify'
  guestMode: 'all' | 'include'
  /**
   * pve id -> the vmids that source covers. A missing entry means "not narrowed", i.e. every
   * guest — which is exactly `guests.mode: 'all'`, so it needs no guest list to resolve and a
   * PVE whose listing hasn't arrived yet can never be saved as "back up nothing".
   */
  selection: Record<string, number[]>
  time: string
  days: boolean[]
  /** A cron pinned by a 0.9 migration. When set it wins and the schedule renders read-only. */
  cron: string
  retention: RetentionConfig
  syncDirection: 'pull' | 'push'
  options: RouteOptions
}

/**
 * Which kind a device selection implies (NOTES decision 12).
 *
 * No sources at all -> the route only wakes its target, and the "On wake" segmented picks
 * between watching someone else's schedules and verifying the datastore. Any PBS among the
 * sources -> a PBS-to-PBS sync. Otherwise a backup from one or more PVEs.
 */
export function inferKind(sourceIds: string[], onWake: 'external' | 'verify'): RouteKind {
  if (sourceIds.length === 0) return onWake
  return sourceIds.some(isPbsKey) ? 'sync' : 'backup'
}

export interface SectionVisibility {
  onWake: boolean
  guests: boolean
  sync: boolean
  retention: boolean
}

/** Which sections of the form a kind shows — the mockup's `renderPreview()` visibility table. */
export function sectionsFor(kind: RouteKind): SectionVisibility {
  return {
    onWake: kind === 'external' || kind === 'verify',
    guests: kind === 'backup',
    sync: kind === 'sync',
    // External and Verify write no snapshots, so there is nothing for a retention to prune.
    retention: kind === 'backup' || kind === 'sync',
  }
}

// --- guest selection ---------------------------------------------------------

/** Included unless the source has been narrowed and this vmid was left out. */
export function isPicked(selection: Record<string, number[]>, pve: string, vmid: number): boolean {
  const list = selection[pve]
  return list === undefined || list.includes(vmid)
}

/**
 * Flip one guest. `allVmids` is only consulted the first time a source is narrowed — until
 * then the source has no list, and unchecking one guest has to mean "all the others".
 */
export function toggleGuest(
  selection: Record<string, number[]>,
  pve: string,
  vmid: number,
  allVmids: number[],
): Record<string, number[]> {
  const list = selection[pve] ?? allVmids
  const next = list.includes(vmid) ? list.filter((v) => v !== vmid) : [...list, vmid].sort((a, b) => a - b)
  return { ...selection, [pve]: next }
}

/** The PVE sources of this draft as bare device ids, in chip order, ignoring PBS chips. */
export function pveSources(sourceIds: string[]): string[] {
  return sourceIds.filter((key) => !isPbsKey(key)).map(deviceId)
}

/**
 * The `x/y selected` counter. `total` counts every guest of every PVE source; a source whose
 * listing failed or hasn't arrived contributes nothing rather than a wrong zero-of-zero.
 */
export function guestTally(
  sourceIds: string[],
  groups: PveGuests[],
  selection: Record<string, number[]>,
): { total: number; chosen: number } {
  let total = 0
  let chosen = 0
  for (const pve of pveSources(sourceIds)) {
    const group = groups.find((g) => g.pve === pve)
    for (const guest of group?.guests ?? []) {
      total++
      if (isPicked(selection, pve, guest.vmid)) chosen++
    }
  }
  return { total, chosen }
}

/** Per-source group headers only appear once a route has more than one PVE source (NOTES 6). */
export function showsGuestGroups(sourceIds: string[]): boolean {
  return pveSources(sourceIds).length > 1
}

// --- draft <-> Route ---------------------------------------------------------

export function draftFromRoute(route: Route | null, pbss: PbsDevice[]): RouteDraft {
  if (!route) {
    return {
      id: '',
      name: '',
      color: ROUTE_COLORS[0],
      enabled: true,
      notify: true,
      sourceIds: [],
      target: pbss[0]?.id ?? '',
      onWake: 'external',
      guestMode: 'all',
      selection: {},
      time: '04:00',
      days: Array(7).fill(true),
      cron: '',
      retention: { ...DEFAULT_RETENTION },
      syncDirection: 'pull',
      options: { ...DEFAULT_OPTIONS },
    }
  }
  const selection: Record<string, number[]> = {}
  for (const source of route.sources) {
    // A source left on "all" gets no entry, which is how the draft spells "everything".
    if (source.guests.mode === 'include') selection[source.pve] = [...source.guests.list]
  }
  return {
    id: route.id,
    name: route.name,
    color: route.color,
    enabled: route.enabled,
    notify: route.notify,
    sourceIds:
      route.kind === 'sync'
        ? route.source_pbs
          ? [pbsNodeId(route.source_pbs)]
          : []
        : route.sources.map((s) => pveNodeId(s.pve)),
    target: route.target,
    onWake: route.kind === 'verify' ? 'verify' : 'external',
    // A route mixing per-source modes can only come from a hand-edited config.yaml; the form
    // has one switch, so anything narrowed anywhere reads as Selection and the untouched
    // sources stay "all" until you narrow them too.
    guestMode: Object.keys(selection).length ? 'include' : 'all',
    selection,
    time: route.schedule.time,
    days: [...route.schedule.days],
    cron: route.schedule.cron,
    retention: { ...route.retention },
    syncDirection: route.sync_direction,
    options: { ...route.options },
  }
}

/** Turn a name into a route id the backend's `^[a-z0-9][a-z0-9._-]*$` accepts, unique among
 *  `taken`. Only used for a brand-new route — an edit never re-derives its id. */
export function slugifyRouteId(name: string, taken: readonly string[]): string {
  const base =
    name
      .toLowerCase()
      .replace(/[^a-z0-9._-]+/g, '-')
      .replace(/^[^a-z0-9]+/, '')
      .replace(/-+$/, '')
      .slice(0, 48) || 'route'
  if (!taken.includes(base)) return base
  for (let n = 2; ; n++) {
    const candidate = `${base}-${n}`
    if (!taken.includes(candidate)) return candidate
  }
}

export function draftToRoute(draft: RouteDraft, takenIds: readonly string[]): Route {
  const kind = inferKind(draft.sourceIds, draft.onWake)
  const pves = pveSources(draft.sourceIds)
  const sources: RouteSource[] =
    kind === 'backup'
      ? pves.map((pve) => {
          const list = draft.selection[pve]
          return draft.guestMode === 'include' && list !== undefined
            ? { pve, guests: { mode: 'include' as const, list } }
            : { pve, guests: { mode: 'all' as const, list: [] } }
        })
      : []
  return {
    id: draft.id || slugifyRouteId(draft.name, takenIds),
    name: draft.name.trim(),
    color: draft.color,
    enabled: draft.enabled,
    notify: draft.notify,
    kind,
    sources,
    source_pbs: kind === 'sync' ? deviceId(draft.sourceIds.find(isPbsKey) ?? '') : '',
    target: draft.target,
    schedule: { time: draft.time, days: [...draft.days], cron: draft.cron },
    retention: { ...draft.retention },
    sync_direction: draft.syncDirection,
    options: { ...draft.options },
  }
}

// --- validation --------------------------------------------------------------

export type RouteField = 'name' | 'sources' | 'target' | 'days' | 'guests'

export interface FieldError {
  /** Absent when the error belongs to the form as a whole (a banner above the fields). */
  field?: RouteField
  /** An i18n key for a rule this form checks itself. */
  key?: string
  params?: Record<string, unknown>
  /** Verbatim text from the backend, which is already localized-enough English prose. */
  message?: string
}

/**
 * Everything that would be rejected, checked here so the user sees it on the field instead of
 * as a 422 after the round trip. These mirror `backend/app/config.py` — `Route._check_kind`
 * and `Config._check_references` — deliberately: the backend stays the authority, this is the
 * same rule said earlier.
 */
export function validateDraft(
  draft: RouteDraft,
  pves: PveDevice[],
  pbss: PbsDevice[],
): FieldError[] {
  const kind = inferKind(draft.sourceIds, draft.onWake)
  const errors: FieldError[] = []

  if (!draft.name.trim()) errors.push({ field: 'name', key: 'dashboard.routeModal.errName' })
  if (!draft.target) errors.push({ field: 'target', key: 'dashboard.routeModal.errTarget' })

  if (kind === 'sync' && draft.sourceIds.length > 1) {
    errors.push({ field: 'sources', key: 'dashboard.routeModal.errSyncSingle' })
  }

  if (kind === 'backup' && draft.target) {
    for (const id of pveSources(draft.sourceIds)) {
      const pve = pves.find((p) => p.id === id)
      if (pve && !pve.storages[draft.target]) {
        errors.push({
          field: 'sources',
          key: 'dashboard.routeModal.errNoStorage',
          params: { pve: id, pbs: draft.target },
        })
      }
    }
  }

  if (kind === 'external') {
    const target = pbss.find((p) => p.id === draft.target)
    if (target && !target.managed_power) {
      errors.push({ field: 'target', key: 'dashboard.routeModal.errExternalUnmanaged' })
    }
  }

  if (!draft.cron && !draft.days.some(Boolean)) {
    errors.push({ field: 'days', key: 'dashboard.routeModal.errNoDay' })
  }

  if (kind === 'backup' && draft.guestMode === 'include') {
    const sources = pveSources(draft.sourceIds)
    // A source with no entry still means "all", so only a selection narrowed down to nothing
    // everywhere is empty — which would back up nothing at all.
    const covered = sources.some((pve) => {
      const list = draft.selection[pve]
      return list === undefined || list.length > 0
    })
    if (sources.length && !covered) {
      errors.push({ field: 'guests', key: 'dashboard.routeModal.errNoGuests' })
    }
  }

  return errors
}

// --- retention overlap -------------------------------------------------------

function retentionDiffers(a: RetentionConfig, b: RetentionConfig): boolean {
  return (
    a.keep_last !== b.keep_last ||
    a.keep_daily !== b.keep_daily ||
    a.keep_weekly !== b.keep_weekly ||
    a.keep_monthly !== b.keep_monthly ||
    a.keep_yearly !== b.keep_yearly
  )
}

/** `null` = every guest of that PVE, which overlaps any other set. */
function guestSet(source: RouteSource): number[] | null {
  return source.guests.mode === 'include' ? source.guests.list : null
}

function setsOverlap(a: number[] | null, b: number[] | null): boolean {
  if (a === null || b === null) return true
  return a.some((v) => b.includes(v))
}

/**
 * The names of the routes this draft will fight with over retention (NOTES, "Niente Basic
 * Mode").
 *
 * PBS/vzdump retention is applied per backup group on the datastore, so two backup routes
 * that reach the same target with overlapping guests but different keep-counts prune each
 * other and the last run to finish wins. Not forbidden — warned about, because the honest
 * split (different guests in different routes) is legitimate and common.
 *
 * Only backup routes: sync, external and verify never run vzdump's prune-backups.
 */
export function retentionOverlaps(draft: RouteDraft, routes: Route[]): string[] {
  if (inferKind(draft.sourceIds, draft.onWake) !== 'backup') return []
  const mine = new Map<string, number[] | null>()
  for (const pve of pveSources(draft.sourceIds)) {
    mine.set(pve, draft.guestMode === 'include' ? (draft.selection[pve] ?? null) : null)
  }
  const names: string[] = []
  for (const other of routes) {
    if (other.id === draft.id) continue
    if (other.kind !== 'backup' || other.target !== draft.target) continue
    if (!retentionDiffers(other.retention, draft.retention)) continue
    const shares = other.sources.some(
      (s) => mine.has(s.pve) && setsOverlap(mine.get(s.pve) ?? null, guestSet(s)),
    )
    if (shares) names.push(other.name || other.id)
  }
  return names
}

// --- server errors -----------------------------------------------------------

const FIELD_BY_LOC: Record<string, RouteField> = {
  name: 'name',
  target: 'target',
  sources: 'sources',
  source_pbs: 'sources',
  schedule: 'days',
  days: 'days',
  time: 'days',
  cron: 'days',
}

/**
 * Map a rejected save onto the form.
 *
 * Every route write goes through `save_section`, which re-validates the whole `Config` and
 * answers a 422 whose `detail` is pydantic's *list* of errors — so `ApiError.message` is a
 * JSON dump and the useful part is `raw`. A `model_validator(mode="after")` on `Config` or
 * `Route` reports `loc: []` / `loc: ['routes', 3]`, i.e. no field at all, so those become a
 * banner above the form rather than being pinned to an arbitrary input.
 */
export function saveErrors(err: ApiError): FieldError[] {
  if (err.status === 409) {
    return [{ field: 'name', key: 'dashboard.routeModal.errDuplicate' }]
  }
  const raw = err.raw
  if (Array.isArray(raw)) {
    const out: FieldError[] = []
    for (const entry of raw as { loc?: unknown[]; msg?: string }[]) {
      const msg = String(entry.msg ?? '').replace(/^Value error, /, '')
      if (!msg) continue
      const loc = (entry.loc ?? []).map(String)
      const field = loc.map((part) => FIELD_BY_LOC[part]).find(Boolean)
      out.push({ field, message: msg })
    }
    if (out.length) return out
  }
  return [{ message: err.message }]
}
