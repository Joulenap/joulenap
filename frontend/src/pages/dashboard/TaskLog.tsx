import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TaskLogLine } from '../../api/types'
import { c, mono, panelStyle } from '../../theme'

// Colour per task phase so the three sources (vzdump on PVE, GC + verify on PBS) read apart.
const STEP_COLOR: Record<string, string> = {
  backup: c.blue,
  gc: c.amber,
  verify: c.green,
}

interface TaskLogProps {
  lines: TaskLogLine[]
  running: boolean
  runId: number | null
}

// Live PBS/PVE task-log panel: collapsed by default, auto-expands while a job runs, and
// stays where the user last put it for that run (a new run re-applies the auto behaviour).
export function TaskLog({ lines, running, runId }: TaskLogProps) {
  const { t } = useTranslation()

  // null => follow `running`; true/false => the user's explicit choice for this run.
  const [userOpen, setUserOpen] = useState<boolean | null>(null)
  const expanded = userOpen ?? running

  // Each new session (run_id change) drops the sticky user intent so work auto-expands again.
  useEffect(() => {
    setUserOpen(null)
  }, [runId])

  // Auto-scroll to the newest line while streaming, unless the user has scrolled up to read.
  const scroller = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  useLayoutEffect(() => {
    const el = scroller.current
    if (el && expanded && stick.current) el.scrollTop = el.scrollHeight
  }, [lines, expanded])

  const onScroll = () => {
    const el = scroller.current
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
  }

  return (
    <div style={{ ...panelStyle, padding: '8px 0 6px', alignSelf: 'stretch', marginTop: 16 }}>
      <button
        onClick={() => setUserOpen(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          width: '100%',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          padding: '6px 18px',
          color: c.text,
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 12, color: c.textFaint, width: 10 }}>{expanded ? '▾' : '▸'}</span>
          <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: '.04em' }}>
            {t('dashboard.taskLog')}
          </span>
          {running && (
            <span
              style={{
                fontFamily: mono,
                fontSize: 10,
                fontWeight: 600,
                textTransform: 'uppercase',
                letterSpacing: '.06em',
                color: c.green,
                background: 'rgba(63,178,127,.14)',
                padding: '2px 7px',
                borderRadius: 5,
              }}
            >
              {t('dashboard.taskLogLive')}
            </span>
          )}
        </span>
        <span style={{ fontFamily: mono, fontSize: 11, color: '#6f7884' }}>
          {t('dashboard.taskLines', { n: lines.length })}
        </span>
      </button>

      {expanded && (
        <div
          ref={scroller}
          onScroll={onScroll}
          style={{
            maxHeight: 400, // ~20 mono lines, then internal scroll (full session kept above)
            overflowY: 'auto',
            borderTop: `1px solid ${c.border}`,
            padding: '8px 0',
          }}
        >
          {lines.length === 0 && (
            <div style={{ padding: '14px 18px', fontSize: 13, color: c.textFaint }}>
              {t('dashboard.noTaskLog')}
            </div>
          )}
          {lines.map((l) => (
            <div
              key={l.id}
              style={{
                display: 'grid',
                gridTemplateColumns: '58px 1fr',
                gap: 10,
                padding: '1px 18px',
                fontFamily: mono,
                fontSize: 12,
                lineHeight: '18px',
              }}
            >
              <span
                style={{
                  color: STEP_COLOR[l.step] ?? c.textFaint,
                  textTransform: 'uppercase',
                  fontSize: 10,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {l.step}
              </span>
              <span style={{ color: '#c8cdd4', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {l.text}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
