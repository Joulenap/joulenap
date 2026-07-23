import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { RunDetail, RunSummary } from '../../api/types'
import { useConfig } from '../../config/ConfigContext'
import { c, mono } from '../../theme'
import { fmtClock, fmtDuration, fmtShort } from '../../utils/format'
import { runDurationMs, runKindLabelKey, runStatusStyle } from '../../utils/status'
import { LEVELS, colHead } from './ActivityLog'

const badge: React.CSSProperties = {
  display: 'inline-block',
  fontFamily: mono,
  fontSize: 10,
  fontWeight: 600,
  padding: '2px 7px',
  borderRadius: 5,
  textTransform: 'uppercase',
}

const cell: React.CSSProperties = { fontFamily: mono, fontSize: 12, color: c.textDim }

function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  const s = runStatusStyle(status)
  return <span style={{ ...badge, color: s.color, background: s.bg }}>{t(s.labelKey)}</span>
}

// One run's steps + its own log lines, fetched on expand. Rendered inside the expanded row.
function RunDetailPanel({ detail }: { detail: RunDetail | null }) {
  const { t } = useTranslation()
  if (!detail) {
    return (
      <div style={{ padding: '10px 18px 12px 34px', fontSize: 12, color: c.textFaint }}>
        {t('common.loading')}
      </div>
    )
  }
  return (
    <div style={{ padding: '4px 18px 12px 34px', borderLeft: `2px solid ${c.border}`, marginLeft: 16 }}>
      <div style={{ ...colHead, margin: '8px 0 5px' }}>{t('dashboard.runSteps')}</div>
      {detail.steps.length === 0 && (
        <div style={{ fontSize: 12, color: c.textFaint }}>{t('dashboard.noSteps')}</div>
      )}
      {detail.steps.map((s) => {
        const ms = runDurationMs(s)
        return (
          <div
            key={`${s.name}-${s.started_at}`}
            style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '3px 0', flexWrap: 'wrap' }}
          >
            <span style={{ ...cell, minWidth: 74, color: c.textMid }}>{s.name}</span>
            <StatusBadge status={s.status} />
            <span style={{ ...cell, minWidth: 44 }}>{ms === null ? '—' : fmtDuration(ms)}</span>
            {s.detail && (
              <span style={{ fontSize: 12, color: c.textFaint, overflowWrap: 'anywhere' }}>{s.detail}</span>
            )}
          </div>
        )
      })}

      <div style={{ ...colHead, margin: '12px 0 5px' }}>{t('dashboard.runLog')}</div>
      {detail.logs.length === 0 && (
        <div style={{ fontSize: 12, color: c.textFaint }}>{t('dashboard.noLogs')}</div>
      )}
      {detail.logs.map((l) => {
        const lvl = LEVELS[l.level] ?? LEVELS.INFO
        return (
          <div key={l.id} style={{ display: 'flex', gap: 10, padding: '2px 0', alignItems: 'baseline' }}>
            <span style={{ ...cell, flex: '0 0 auto' }}>{fmtClock(new Date(l.ts))}</span>
            <span style={{ ...badge, color: lvl.color, background: lvl.bg, flex: '0 0 auto' }}>
              {l.level}
            </span>
            <span style={{ fontSize: 12, color: '#c8cdd4', minWidth: 0, overflowWrap: 'anywhere' }}>
              {l.message}
            </span>
          </div>
        )
      })}
    </div>
  )
}

export function RunHistory({ runs, error }: { runs: RunSummary[]; error: boolean }) {
  const { t } = useTranslation()
  const { config } = useConfig()
  const [openId, setOpenId] = useState<number | null>(null)
  const [details, setDetails] = useState<Record<number, RunDetail>>({})

  const toggle = async (run: RunSummary) => {
    if (openId === run.id) {
      setOpenId(null)
      return
    }
    setOpenId(run.id)
    // Cached detail is reused, except for a run still in flight — its steps are still being
    // written, so a cached copy would freeze mid-cycle.
    if (details[run.id] && run.status !== 'running') return
    try {
      const detail = await api.run(run.id)
      setDetails((d) => ({ ...d, [run.id]: detail }))
    } catch {
      // Leave it in the loading state; the next expand retries.
    }
  }

  const retentionDays = config?.maintenance.history.retention_days ?? 0

  return (
    <>
      <div
        className="jn-run-head"
        style={{ padding: '0 18px 7px', borderBottom: `1px solid ${c.border}` }}
      >
        <span style={colHead}>{t('dashboard.colStarted')}</span>
        <span style={colHead}>{t('dashboard.colKind')}</span>
        <span style={colHead}>{t('dashboard.colTrigger')}</span>
        <span style={colHead}>{t('dashboard.colStatus')}</span>
        <span style={colHead}>{t('dashboard.colDuration')}</span>
        <span style={colHead}>{t('dashboard.colGuests')}</span>
      </div>

      <div>
        {runs.length === 0 && (
          <div style={{ padding: '14px 18px', fontSize: 13, color: c.textFaint }}>
            {error ? t('dashboard.runsError') : t('dashboard.noRuns')}
          </div>
        )}

        {runs.map((run) => {
          const open = openId === run.id
          const ms = runDurationMs(run)
          return (
            <div key={run.id} style={{ borderBottom: '1px solid #1b212880' }}>
              <button
                type="button"
                onClick={() => void toggle(run)}
                aria-expanded={open}
                className="jn-run-row"
                style={{
                  width: '100%',
                  alignItems: 'center',
                  padding: '7px 18px',
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  textAlign: 'left',
                  color: c.text,
                }}
              >
                <span style={{ ...cell, color: c.textFaint }}>
                  <span style={{ color: c.textFaint, marginRight: 6 }}>{open ? '▾' : '▸'}</span>
                  {fmtShort(new Date(run.started_at))}
                </span>
                <span style={{ ...cell, color: c.textMid }}>{t(runKindLabelKey(run.kind))}</span>
                <span style={cell}>{t(`dashboard.trigger_${run.trigger}`)}</span>
                <span>
                  <StatusBadge status={run.status} />
                </span>
                <span style={cell}>{ms === null ? '—' : fmtDuration(ms)}</span>
                <span style={cell}>{run.guests_ok ?? '—'}</span>
              </button>

              {/* An error is worth seeing without expanding — it's why you opened this view. */}
              {!open && run.error && (
                <div
                  style={{
                    padding: '0 18px 8px 34px',
                    fontSize: 12,
                    color: c.red,
                    overflowWrap: 'anywhere',
                  }}
                >
                  {run.error}
                </div>
              )}

              {open && <RunDetailPanel detail={details[run.id] ?? null} />}
            </div>
          )
        })}
      </div>

      {retentionDays > 0 && runs.length > 0 && (
        <div style={{ padding: '9px 18px 2px', fontSize: 11, color: c.textMuted }}>
          {t('dashboard.historyRetention', { n: retentionDays })}
        </div>
      )}
    </>
  )
}
