import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { StatusResponse } from '../api/types'

// Polls GET /api/status. Shared by the header (status pill) and the dashboard tiles.
export function useStatus(intervalMs = 5000) {
  const [status, setStatus] = useState<StatusResponse | null>(null)

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.status())
    } catch {
      // transient errors keep the last known status; the next tick retries
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { status, refresh }
}
