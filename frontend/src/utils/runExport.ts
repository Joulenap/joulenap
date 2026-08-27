import type { RunDetail, RunSummary, TaskLogLine } from '../api/types'

/**
 * One run as plain text, for pasting into a bug report (#58).
 *
 * The expanded history row already holds everything a maintainer asks for, but it is spread
 * over three blocks and the long lines scroll sideways, so getting it out by hand means
 * selecting across a `<pre>` that is wider than the panel.
 *
 * Deliberately not translated: what it carries (a step's detail, the run's error) is stored on
 * the row in English, and the person on the other end of the paste is reading an English
 * repository. Running the labels through i18n would translate the frame and leave the payload.
 */
export function runAsText(
  run: RunSummary,
  detail: RunDetail | null,
  lines: TaskLogLine[],
): string {
  const out = [
    `Joulenap run #${run.id}`,
    `route:    ${run.route_name ?? '(none)'}`,
    `kind:     ${run.kind}`,
    `trigger:  ${run.trigger}`,
    `status:   ${run.status}`,
    `started:  ${run.started_at}`,
    `finished: ${run.finished_at ?? '(still running)'}`,
  ]

  const steps = detail?.steps ?? []
  if (steps.length > 0) {
    const width = Math.max(...steps.map((s) => s.name.length))
    out.push('', 'steps:')
    for (const s of steps) {
      out.push(`  ${s.name.padEnd(width)}  ${s.status.padEnd(8)}  ${s.detail ?? ''}`.trimEnd())
    }
  }

  if (run.error) out.push('', `error: ${run.error}`)

  const logs = detail?.logs ?? []
  if (logs.length > 0) {
    out.push('', 'activity:')
    for (const l of logs) out.push(`  ${l.ts}  ${l.level.padEnd(5)} ${l.message}`)
  }

  // Task-log text is left exactly as the task wrote it, trailing spaces and all: it is the
  // one part of this that someone may want to diff against what PVE or PBS reported.
  if (lines.length > 0) {
    out.push('', 'task log:')
    for (const l of lines) out.push(l.text)
  }

  return out.join('\n')
}
