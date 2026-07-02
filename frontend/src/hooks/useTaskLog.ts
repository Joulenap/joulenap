import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { TaskLogLine } from '../api/types'

// Tails GET /api/tasklog for the live "Task log" panel. Polls fast (2s) while a job runs,
// slow (8s) when idle. Line ids increase globally, so we poll `after` the last id we hold;
// when the server's run_id changes (a new session started) we reset the buffer to it.
export function useTaskLog(running: boolean) {
  const [lines, setLines] = useState<TaskLogLine[]>([])
  const runIdRef = useRef<number | null>(null)
  const afterRef = useRef(0)

  const poll = useCallback(async () => {
    try {
      const res = await api.taskLog(afterRef.current)
      if (res.run_id === null) return
      if (res.run_id !== runIdRef.current) {
        // New session: adopt it. `after` may be stale (from a previous run), so if this
        // window came back empty, refetch the run from the start.
        runIdRef.current = res.run_id
        const fresh = res.lines.length ? res : await api.taskLog(0)
        setLines(fresh.lines)
        afterRef.current = fresh.lines.at(-1)?.id ?? 0
        return
      }
      if (res.lines.length) {
        setLines((prev) => [...prev, ...res.lines])
        afterRef.current = res.lines[res.lines.length - 1].id
      }
    } catch {
      // transient error: keep what we have, retry next tick
    }
  }, [])

  useEffect(() => {
    poll()
    const id = setInterval(poll, running ? 2000 : 8000)
    return () => clearInterval(id)
  }, [poll, running])

  return { runId: runIdRef.current, lines }
}
