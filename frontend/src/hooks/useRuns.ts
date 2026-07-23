import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { RunSummary } from '../api/types'

// Polls GET /api/runs for the Run history view. Only runs while the view is showing, so the
// default (activity log) view costs no extra requests; the same 8s cadence as the activity
// log keeps a finished run from lingering as "running" for long.
export function useRuns(active: boolean) {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [error, setError] = useState(false)
  // Same guard as useTaskLog: a slow response must not overlap the next tick.
  const inFlightRef = useRef(false)

  const poll = useCallback(async () => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    try {
      setRuns(await api.runs(50))
      setError(false)
    } catch {
      // Transient failure: keep the list we have (blanking it reads as "no runs ever", which
      // is a very different message) and flag it so the view can say so.
      setError(true)
    } finally {
      inFlightRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!active) return
    poll()
    const id = setInterval(poll, 8000)
    return () => clearInterval(id)
  }, [active, poll])

  return { runs, error, refresh: poll }
}
