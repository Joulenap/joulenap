import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { LogLine } from '../../api/types'
import { useRuns } from '../../hooks/useRuns'
import { c, mono, panelStyle } from '../../theme'
import { ActivityLog } from './ActivityLog'
import { RunHistory } from './RunHistory'

type View = 'log' | 'runs'

// The activity stream and the run history are the same data at two zoom levels (every log
// line belongs to a run), so they share one card and a two-tab switch instead of costing the
// dashboard another full-width panel.
export function HistoryCard({ logs }: { logs: LogLine[] }) {
  const { t } = useTranslation()
  const [view, setView] = useState<View>('log')
  const panelId = useId()
  // Only polls while the history tab is open, so the default view costs no extra requests.
  const { runs, error } = useRuns(view === 'runs')

  const tabStyle = (active: boolean): React.CSSProperties => ({
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: '.04em',
    textTransform: 'uppercase',
    padding: '5px 11px',
    borderRadius: 6,
    border: 'none',
    cursor: 'pointer',
    background: active ? c.inputBorder : 'transparent',
    color: active ? c.text : c.textFaint,
  })

  const tab = (id: View, labelKey: string) => (
    <button
      type="button"
      role="tab"
      aria-selected={view === id}
      aria-controls={panelId}
      onClick={() => setView(id)}
      style={tabStyle(view === id)}
    >
      {t(labelKey)}
    </button>
  )

  return (
    <div style={{ ...panelStyle, padding: '8px 0 6px', alignSelf: 'stretch' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 10,
          padding: '6px 14px 12px',
        }}
      >
        <div role="tablist" aria-label={t('dashboard.historyViews')} style={{ display: 'flex', gap: 4 }}>
          {tab('log', 'dashboard.activityLog')}
          {tab('runs', 'dashboard.runHistory')}
        </div>
        <span style={{ fontFamily: mono, fontSize: 11, color: c.textFaint, flex: '0 0 auto' }}>
          {view === 'log'
            ? t('dashboard.events', { n: logs.length })
            : t('dashboard.runsCount', { n: runs.length })}
        </span>
      </div>

      {/* Runs get more room: an expanded row carries its steps and its log lines. */}
      <div id={panelId} role="tabpanel" style={{ maxHeight: view === 'runs' ? 340 : 250, overflowY: 'auto' }}>
        {view === 'log' ? <ActivityLog logs={logs} /> : <RunHistory runs={runs} error={error} />}
      </div>
    </div>
  )
}
