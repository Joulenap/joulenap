import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { NetInterface } from '../api/types'

export interface IfaceOption {
  value: string
  label: string
}

/**
 * The host's NICs as ready-made `<option>`s for the Wake-on-LAN interface picker.
 *
 * Fetched once, not polled: the list can't change while a form is open, and the endpoint
 * reads the machine's own interfaces. A failed request yields an empty list rather than
 * throwing — the form must still open, and blank (auto) is the right default anyway.
 *
 * `current` is the value already configured. It is appended when the list doesn't contain
 * it, so a NIC that was renamed (or a list that failed to load) can't be silently rewritten
 * to whatever happens to be first the next time the form is saved.
 */
export function useWolInterfaces(current: string): IfaceOption[] {
  const [ifaces, setIfaces] = useState<NetInterface[]>([])

  useEffect(() => {
    let live = true
    api
      .wizardInterfaces()
      .then((list) => live && setIfaces(list))
      .catch(() => live && setIfaces([]))
    return () => {
      live = false
    }
  }, [])

  // A NIC with several IPv4 addresses arrives once per address; the value is the name.
  const options = Array.from(new Map(ifaces.map((i) => [i.name, i])).values()).map((i) => ({
    value: i.name,
    label: `${i.name} — ${i.address}`,
  }))
  if (current && !options.some((o) => o.value === current)) {
    options.push({ value: current, label: current })
  }
  return options
}
