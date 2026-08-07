import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { PveDevice } from '../api/types'
import type { PveGuests } from '../utils/guestPanel'

// Refreshed far slower than the 5s status poll on purpose: each entry is a live call into a
// PVE's API (the guest names and types exist nowhere else), and the answer changes when
// someone creates a VM or a backup finishes — minutes, not seconds.
const INTERVAL_MS = 60_000

/**
 * Every configured PVE's guest list, fanned out concurrently.
 *
 * Failures are per-PVE, not global: one unreachable node marks its own group and leaves the
 * others intact, because the panel's whole job is saying which guests are covered — going
 * blank because one box is down loses that for all of them.
 *
 * Feeds both the guest panel and the topology's PVE sub-label (node count / guest count).
 */
export function useGuestsAll(pves: PveDevice[]) {
  const [groups, setGroups] = useState<PveGuests[]>([])
  const [loaded, setLoaded] = useState(false)
  const inFlightRef = useRef(false)
  // The poll closes over the id list, not over `pves`, so a status refresh that hands back a
  // new-but-equal device array doesn't restart the interval.
  const ids = pves.map((p) => p.id).join(',')

  const poll = useCallback(async () => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    const list = ids ? ids.split(',') : []
    try {
      const next = await Promise.all(
        list.map(async (pve): Promise<PveGuests> => {
          try {
            return { pve, guests: await api.guests(pve) }
          } catch {
            return { pve, guests: [], error: true }
          }
        }),
      )
      setGroups(next)
    } finally {
      setLoaded(true)
      inFlightRef.current = false
    }
  }, [ids])

  useEffect(() => {
    poll()
    const id = setInterval(poll, INTERVAL_MS)
    return () => clearInterval(id)
  }, [poll])

  return { groups, loaded, refresh: poll }
}
