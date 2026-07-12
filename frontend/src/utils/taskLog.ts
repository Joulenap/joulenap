import type { TaskLogLine } from '../api/types'

// Append only genuinely-new lines (id greater than the last one already held) so that two
// overlapping polls re-delivering the same `after` window can't duplicate lines — and thus
// duplicate React keys / inflate the line count (FE-M6). Returns the SAME array reference when
// there's nothing new, so React can bail out of the re-render.
export function appendTaskLines(prev: TaskLogLine[], incoming: TaskLogLine[]): TaskLogLine[] {
  if (!incoming.length) return prev
  const lastId = prev.length ? prev[prev.length - 1].id : -1
  const fresh = incoming.filter((l) => l.id > lastId)
  return fresh.length ? [...prev, ...fresh] : prev
}
