import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { StatusResponse } from '../api/types'

// After this many consecutive failed polls we flag the data as stale, so a monitoring
// dashboard shows "can't reach the backend" instead of a frozen last-known pill (FE-H3).
const STALE_AFTER = 3

// Polls GET /api/status. Shared by the header (status pill) and the dashboard tiles.
export function useStatus(intervalMs = 5000) {
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [stale, setStale] = useState(false)
  const failures = useRef(0)

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.status())
      failures.current = 0
      setStale(false)
    } catch {
      // Keep the last known status (the next tick retries), but once failures pile up flag
      // the data as stale so the UI can say so. A 401 doesn't reach here as "stale": the
      // central handler resets auth and routes to Login before we'd count enough failures.
      failures.current += 1
      if (failures.current >= STALE_AFTER) setStale(true)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { status, refresh, stale }
}
