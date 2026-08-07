// Grouping and search for the "Last backup per guest" panel.
//
// The panel is grouped by PVE with sticky dividers, so filtering is not a plain row filter:
// a divider whose group has no match must disappear with it, or the list reads as an empty
// section rather than as no result.

import type { GuestInfo } from '../api/types.ts'

export interface PveGuests {
  pve: string
  guests: GuestInfo[]
  /** Set when this PVE could not be listed; the group renders one error row instead. */
  error?: boolean
}

/**
 * Order each group by vmid and drop the groups the query empties.
 *
 * vmid order, not backup time: the panel is a per-PVE inventory you scan by number, and a
 * list that reshuffles itself after every nightly run is unreadable.
 *
 * A group that failed to load survives any query — hiding it would silently turn "this PVE
 * is unreachable" into "this PVE has no guests".
 */
export function groupGuests(groups: PveGuests[], query: string): PveGuests[] {
  const q = query.trim().toLowerCase()
  const out: PveGuests[] = []
  for (const group of groups) {
    if (group.error) {
      out.push({ ...group, guests: [] })
      continue
    }
    const guests = group.guests
      .filter((g) => !q || matches(g, q))
      .slice()
      .sort((a, b) => a.vmid - b.vmid)
    if (guests.length) out.push({ ...group, guests })
  }
  return out
}

function matches(g: GuestInfo, q: string): boolean {
  return (
    g.name.toLowerCase().includes(q) ||
    String(g.vmid).includes(q) ||
    guestTypeLabel(g.type).toLowerCase().includes(q)
  )
}

/** `qemu`/`lxc` as the badge reads them. Anything else is shown verbatim, uppercased. */
export function guestTypeLabel(type: string): string {
  if (type === 'qemu') return 'VM'
  if (type === 'lxc') return 'CT'
  return type.toUpperCase()
}

export interface PbsChip {
  label: string
  /** More than one copy: the chip is highlighted, as in the mockup. */
  many: boolean
  /** Full list for the tooltip, when the label had to elide. */
  title: string
}

/**
 * The PBS chip for one guest: `pbs-01`, or `pbs-01 +1` when a sync route copied it on.
 *
 * The mockup writes the second box's own suffix (`pbs-01 +02`); device ids are free-form
 * slugs, so there is no suffix to lift out of an arbitrary one. An overflow count says the
 * same thing for any naming scheme, and the full list is in the tooltip.
 */
export function pbsChip(pbsIds: string[]): PbsChip | null {
  if (!pbsIds.length) return null
  const [first, ...rest] = pbsIds
  return {
    label: rest.length ? `${first} +${rest.length}` : first,
    many: rest.length > 0,
    title: pbsIds.join(', '),
  }
}
