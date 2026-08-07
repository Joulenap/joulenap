import { Fragment, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { Route, RunDetail, RunSummary, TaskLogLine } from '../../api/types'
import { fmtDT, fmtDuration } from '../../utils/format'
import { runKindLabelKey, runStatusStyle, runDurationMs } from '../../utils/status'

interface RunHistoryProps {
  runs: RunSummary[]
  routes: Route[]
  error: boolean
  /** Live task-log lines for the run in flight, from the shared useTaskLog poll. */
  liveLines: TaskLogLine[]
  liveRunId: number | null
  onStop: (run: RunSummary) => void
}

export function RunHistory({
  runs,
  routes,
  error,
  liveLines,
  liveRunId,
  onStop,
}: RunHistoryProps) {
  const { t } = useTranslation()
  const [filter, setFilter] = useState<string>('all')
  const [open, setOpen] = useState<number | null>(null)

  // Open the run in flight on its own: someone landing on this page wants to see what is
  // happening now, and the live task log is otherwise a click away. The ref means each run is
  // auto-opened once, so collapsing it is not fought until the next run starts.
  const autoOpened = useRef<number | null>(null)
  useEffect(() => {
    const running = runs.find((r) => r.status === 'running')
    if (running && autoOpened.current !== running.id) {
      autoOpened.current = running.id
      setOpen(running.id)
    }
  }, [runs])

  // Filtered client-side over the 50 rows already polled: switching chips is instant and
  // costs no request, and the backend's ?route= filter would only matter past that window.
  const shown = filter === 'all' ? runs : runs.filter((r) => r.route_id === filter)

  return (
    <section className="panel">
      <div className="panel-hd">
        <h2>{t('dashboard.runHistory')}</h2>
        <div className="fchips">
          <button
            type="button"
            className={`fchip${filter === 'all' ? ' on' : ''}`}
            onClick={() => setFilter('all')}
          >
            {t('dashboard.filterAll')}
          </button>
          {routes.map((r) => (
            <button
              type="button"
              key={r.id}
              className={`fchip${filter === r.id ? ' on' : ''}`}
              onClick={() => setFilter(r.id)}
            >
              <span className="rdot" style={{ background: r.color }} />
              {r.name || r.id}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="panel-empty">{t('dashboard.runsError')}</div>}
      {/* The empty state lives *inside* .table-scroll so the box keeps its fixed height:
          the bottom grid takes its row height from this panel, and a collapsed one leaves
          "Last backup per guest" one row tall. */}
      <div className={`table-scroll${open !== null ? ' uncapped' : ''}`}>
        {shown.length === 0 ? (
          <div className="panel-empty">{t('dashboard.noRuns')}</div>
        ) : (
          <table className="htable">
            <thead>
              <tr>
                <th>{t('dashboard.colRoute')}</th>
                <th>{t('dashboard.colKind')}</th>
                <th>{t('dashboard.colStarted')}</th>
                <th>{t('dashboard.colDuration')}</th>
                <th>{t('dashboard.colStatus')}</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((run) => (
                <Fragment key={run.id}>
                  <HistoryRow
                    run={run}
                    routes={routes}
                    open={open === run.id}
                    onToggle={() => setOpen(open === run.id ? null : run.id)}
                  />
                  {open === run.id && (
                    <tr className="drow">
                      <td colSpan={5}>
                        <RunDetailBody
                          run={run}
                          liveLines={liveRunId === run.id ? liveLines : []}
                          onStop={onStop}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}

function HistoryRow({
  run,
  routes,
  open,
  onToggle,
}: {
  run: RunSummary
  routes: Route[]
  open: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const route = routes.find((r) => r.id === run.route_id)
  const style = runStatusStyle(run.status)
  const ms = runDurationMs(run)

  return (
    <tr
      className={`hrow x${open ? ' open' : ''}`}
      tabIndex={0}
      aria-expanded={open}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onToggle()
        }
      }}
    >
      <td>
        <span className="xbtn">▶</span>
        {/* A deleted route leaves its name denormalised on the row, but no colour — the dot
            falls back to a neutral one rather than disappearing and shifting the column. */}
        <span className="rdot" style={{ background: route?.color ?? 'var(--jn-text-muted)' }} />
        {run.route_name || t('dashboard.adhocRun')}
      </td>
      <td className="kind">{t(runKindLabelKey(run.kind))}</td>
      <td className="when">{fmtDT(new Date(run.started_at))}</td>
      <td className="dur">{run.finished_at && ms !== null ? fmtDuration(ms) : '—'}</td>
      <td>
        <span className="res" style={{ color: style.color, background: style.bg }}>
          {t(style.labelKey)}
          {run.guests_ok !== null ? ` · ${t('dashboard.guestsOk', { count: run.guests_ok })}` : ''}
        </span>
      </td>
    </tr>
  )
}

/**
 * The expanded body: the run's step timeline, then its task-log tail.
 *
 * The steps are not in the mockup, but they are the only place the UI can show *why* a
 * power-off was skipped ("left on: still needed by another run") or which source of a
 * multi-PVE route failed — and the run detail view they used to live in is gone.
 */
function RunDetailBody({
  run,
  liveLines,
  onStop,
}: {
  run: RunSummary
  liveLines: TaskLogLine[]
  onStop: (run: RunSummary) => void
}) {
  const { t } = useTranslation()
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [lines, setLines] = useState<TaskLogLine[] | null>(null)
  const live = run.status === 'running'

  useEffect(() => {
    let cancelled = false
    api
      .run(run.id)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => {})
    // A finished run's output is fetched once, on expand: it never changes again. The
    // running row reads the shared live poll instead.
    if (!live) {
      api
        .taskLog(0, run.id)
        .then((r) => !cancelled && setLines(r.lines))
        .catch(() => !cancelled && setLines([]))
    }
    return () => {
      cancelled = true
    }
    // A run in flight grows a step at a time, so the timeline has to be re-read or it freezes
    // at whatever it looked like when the row was expanded. `liveLines` is already narrowed to
    // this run by the caller, so its length only moves while this row is the live one — which
    // makes it a free tick at the task-log poll's own cadence, with no second timer.
  }, [run.id, live, live ? liveLines.length : 0])

  // A run reports its final status *before* its timeline is complete: the power-off steps are
  // recorded by JobService after the cycle returns (it owns the lease, so it owns those
  // steps), which means the fetch above — the one triggered by `live` flipping to false —
  // usually lands while `poweroff` is still `running`. Nothing re-read it after that, so the
  // row the history auto-opened sat on a blue "Running · —" power-off forever while the
  // topology showed the box already asleep. Collapsing and re-expanding was the only cure.
  const stepPending = detail?.steps.some((s) => s.status === 'running') ?? false
  useEffect(() => {
    if (live || !stepPending) return
    let cancelled = false
    const id = setInterval(() => {
      api
        .run(run.id)
        .then((d) => !cancelled && setDetail(d))
        .catch(() => {})
    }, 2000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [run.id, live, stepPending])

  const shown = live ? liveLines : (lines ?? [])

  return (
    <>
      {detail && detail.steps.length > 0 && (
        <div className="steps">
          {detail.steps.map((step, i) => {
            const style = runStatusStyle(step.status)
            const ms = runDurationMs(step)
            return (
              <div className="step" key={`${step.name}-${i}`}>
                <span className="sname">{step.name}</span>
                <span style={{ color: style.color }}>{t(style.labelKey)}</span>
                <span className="sdur">
                  {step.finished_at && ms !== null ? fmtDuration(ms) : '—'}
                </span>
                {step.detail && <span className="sdetail">{step.detail}</span>}
              </div>
            )
          })}
        </div>
      )}

      {run.error && <div className="tasklog tl-err">{run.error}</div>}

      {shown.length > 0 ? (
        <pre className="tasklog">
          {shown.map((line) => (
            <div key={line.id} className={lineClass(line.text)}>
              {line.text}
            </div>
          ))}
        </pre>
      ) : (
        !live && lines !== null && <div className="tasklog-empty">{t('dashboard.noTaskLog')}</div>
      )}

      {live && (
        <>
          <div className="runbar" aria-hidden="true" />
          <div className="drow-actions">
            <button
              type="button"
              className="btn btn-outline-danger"
              onClick={(e) => {
                e.stopPropagation()
                onStop(run)
              }}
            >
              ■ {t('dashboard.stopRun')}
            </button>
          </div>
        </>
      )}
    </>
  )
}

/** Colour a task-log line by what the task itself said, not by guessing at severity. */
function lineClass(text: string): string {
  if (/^\s*(ERROR|TASK ERROR)\b/i.test(text)) return 'tl-err'
  if (/^\s*WARN/i.test(text) || /\bWARNINGS?:\s*\d+/.test(text)) return 'tl-warn'
  return ''
}
