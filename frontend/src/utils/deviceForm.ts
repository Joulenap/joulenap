// Everything the Settings page decides, as pure functions.
//
// The test harness is `node --test` with no DOM, so nothing inside a component can be
// covered (same constraint as utils/routeForm.ts). Which tab is shown, which routes block a
// removal, what a device card says about itself and whether a device may be saved all live
// here; the components only render the answers.
//
// There is deliberately no draft type for the edit modal: a device draft *is* a `PveDevice`
// / `PbsDevice`, so `draftFrom`/`draftTo` would be identity functions. The modal clones the
// device, edits it in place and PUTs it back.

import type { ApiError } from '../api/client'
import type { PbsDevice, PveDevice, Route } from '../api/types'
import type { PveGuests } from './guestPanel'

// --- tabs ---------------------------------------------------------------------

export type SettingsTab = 'devices' | 'account' | 'notifications' | 'integrations' | 'advanced'

/** The five top tabs, in the order the mockup draws them. */
export const SETTINGS_TABS: readonly SettingsTab[] = [
  'devices',
  'account',
  'notifications',
  'integrations',
  'advanced',
] as const

/**
 * Coerce a stored tab id to one that still exists.
 *
 * `AppShell` keeps the last tab in state and the gear used to open `localization`, which M11
 * folded into Account — an unknown id must land on Devices rather than render nothing.
 */
export function normalizeTab(id: string | null | undefined): SettingsTab {
  return SETTINGS_TABS.includes(id as SettingsTab) ? (id as SettingsTab) : 'devices'
}

// --- removal guard -------------------------------------------------------------

export type DeviceKind = 'pves' | 'pbss'

/**
 * The names of the routes that would break if this device went away.
 *
 * Mirrors `backend/app/api/devices.py::_routes_using` exactly, so the guard can open without
 * a round trip; the backend stays the authority and its 409 is still honoured (see
 * `guardRoutes`). A route with no name falls back to its id, as the backend does.
 */
export function routesUsing(routes: Route[], kind: DeviceKind, id: string): string[] {
  return routes
    .filter((r) =>
      kind === 'pbss' ? r.target === id || r.source_pbs === id : r.sources.some((s) => s.pve === id),
    )
    .map((r) => r.name || r.id)
}

/**
 * The route names out of a refused DELETE.
 *
 * `detail` is `{message, routes[]}`, which the client parks on `ApiError.raw`; a plain-string
 * detail (any other 4xx) yields nothing and the caller falls back to `err.message`.
 */
export function guardRoutes(err: ApiError): string[] {
  const raw = err.raw as { routes?: unknown } | undefined
  const routes = raw?.routes
  return Array.isArray(routes) ? routes.map(String) : []
}

// --- card meta ------------------------------------------------------------------

export type DeviceState = 'connected' | 'sleeping' | 'offline'

/**
 * What the card's status row means.
 *
 * An unmanaged PBS that is unreachable reads `offline`, not "always on": saying a box is
 * always on while it is down would hide a fault (the same call M09 made in the topology).
 */
export function deviceState(online: boolean | undefined, managedPower = false): DeviceState {
  if (online) return 'connected'
  return managedPower ? 'sleeping' : 'offline'
}

export interface PveCardMeta {
  /** i18n key suffix for the badge: `cluster` or `standalone`. */
  badge: 'cluster' | 'standalone'
  host: string
  nodes: number
  guests: number
  /** The guest listing failed, so node/guest counts are unknown and must not be shown. */
  unknown: boolean
}

/**
 * A PVE card's derived facts.
 *
 * `PveDevice` carries no node count and no cluster flag on purpose (config.py: "nodes are
 * discovered at runtime via the API, which is also what tells us whether this entry is a
 * cluster"), so both come from the guest listing `useGuestsAll` already fetches for the
 * topology. No listing yet, or a failed one, means unknown — never "standalone, 0 guests".
 */
export function pveCardMeta(pve: PveDevice, group?: PveGuests): PveCardMeta {
  const host = `${pve.host}:${pve.port}`
  if (!group || group.error) {
    return { badge: 'standalone', host, nodes: 0, guests: 0, unknown: true }
  }
  const nodes = new Set(group.guests.map((g) => g.node)).size
  return {
    badge: nodes > 1 ? 'cluster' : 'standalone',
    host,
    nodes,
    guests: group.guests.length,
    unknown: false,
  }
}

export interface PbsCardMeta {
  /** `wol` when Joulenap manages the box's power, `alwaysOn` when it does not. */
  badge: 'wol' | 'alwaysOn'
  /** `host:port`, plus the MAC when there is one to show. */
  host: string
  datastore: string
  routes: number
}

export function pbsCardMeta(pbs: PbsDevice, routes: Route[]): PbsCardMeta {
  const base = `${pbs.host}:${pbs.port}`
  return {
    badge: pbs.managed_power ? 'wol' : 'alwaysOn',
    host: pbs.managed_power && pbs.mac ? `${base} · MAC ${pbs.mac}` : base,
    datastore: pbs.datastore,
    routes: routesUsing(routes, 'pbss', pbs.id).length,
  }
}

// --- validation ------------------------------------------------------------------

export interface DeviceError {
  /** The field this belongs to, when it maps onto one; absent = a banner above the form. */
  field?: string
  /** An i18n key for a rule this form checks itself. */
  key?: string
  params?: Record<string, unknown>
  /** Verbatim backend prose. */
  message?: string
}

const PORT_RANGE = { min: 1, max: 65535 }

/**
 * What the backend would reject, said before the round trip.
 *
 * Mirrors `backend/app/config.py` — the id pattern, the port bounds and
 * `PbsDevice._check_power_fields`, whose "only once a host is set" escape hatch matters here
 * too: a half-filled device must stay saveable.
 */
export function validateDevice(device: PveDevice | PbsDevice): DeviceError[] {
  const errors: DeviceError[] = []
  if (!device.host.trim()) errors.push({ field: 'host', key: 'settings.devices.errHost' })
  if (device.port < PORT_RANGE.min || device.port > PORT_RANGE.max) {
    errors.push({ field: 'port', key: 'settings.devices.errPort' })
  }

  if (!isPbs(device)) return errors

  if (!device.datastore.trim()) {
    errors.push({ field: 'datastore', key: 'settings.devices.errDatastore' })
  }
  if (device.managed_power && device.host) {
    // Same three fields, same order, same reason as the pydantic validator: without them
    // Joulenap has no way to wake the box or put it back to sleep.
    const missing = (['mac', 'ssh_user', 'ssh_key_path'] as const).filter((f) => !device[f])
    for (const field of missing) {
      errors.push({ field, key: 'settings.devices.errPowerField' })
    }
  }
  return errors
}

function isPbs(device: PveDevice | PbsDevice): device is PbsDevice {
  return 'managed_power' in device
}

/**
 * Map a rejected save onto the form.
 *
 * A device write goes through `save_section`, which re-validates the whole `Config`: the 422
 * `detail` is pydantic's *list*, so `ApiError.message` is a JSON dump and `raw` holds the
 * useful part. A `model_validator(mode="after")` reports no field at all, and those become a
 * banner rather than being pinned to an arbitrary input. A 422 from the redaction guard has a
 * plain-string detail and falls through to the message.
 */
export function deviceSaveErrors(err: ApiError): DeviceError[] {
  const raw = err.raw
  if (Array.isArray(raw)) {
    const out: DeviceError[] = []
    for (const entry of raw as { loc?: unknown[]; msg?: string }[]) {
      const msg = String(entry.msg ?? '').replace(/^Value error, /, '')
      if (!msg) continue
      // The last path segment is the field when there is one; `['pbss', 2]` has none.
      const last = (entry.loc ?? []).map(String).at(-1)
      const field = last && !/^\d+$/.test(last) && last !== 'pbss' && last !== 'pves' ? last : undefined
      out.push({ field, message: msg })
    }
    if (out.length) return out
  }
  return [{ message: err.message }]
}
