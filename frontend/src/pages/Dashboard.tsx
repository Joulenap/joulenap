import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import type { LogLine, PbsDevice, Route, RunSummary, StatusResponse } from '../api/types'
import { useConfig } from '../config/ConfigContext'
import { useGuestsAll } from '../hooks/useGuestsAll'
import { useRuns } from '../hooks/useRuns'
import { useTaskLog } from '../hooks/useTaskLog'
import { ActivityLog } from './dashboard/ActivityLog'
import { GuestLastBackup } from './dashboard/GuestLastBackup'
import { ManualRunPanel, type ManualAction } from './dashboard/ManualRunPanel'
import { RouteStrip } from './dashboard/RouteStrip'
import { RunHistory } from './dashboard/RunHistory'
import { Topology } from './dashboard/Topology'
import { UpcomingRuns } from './dashboard/UpcomingRuns'

interface DashboardProps {
  status: StatusResponse | null
  refreshStatus: () => Promise<void>
}

/**
 * The route-centric homepage: the operational centre of the app.
 *
 * Layout is two stretched grid pairs (see dashboard.css). Top: the topology hero beside a
 * rail of manual actions + upcoming runs. Then the full-width route strip. Bottom: run
 * history beside the read-only guest panel, with the activity log below.
 *
 * Every panel reads the same `status` poll the header already runs, plus the config for the
 * static device/route model — the only extra requests on this page are the run history, the
 * live task log and the per-PVE guest lists.
 */
export function Dashboard({ status, refreshStatus }: DashboardProps) {
  const { t } = useTranslation()
  const { config, reload } = useConfig()
  const routes: Route[] = config?.routes ?? []
  const running = status?.state === 'running'

  const { runs, error: runsError } = useRuns(true)
  const { runId: liveRunId, lines: liveLines } = useTaskLog(running)
  const { groups, loaded: guestsLoaded } = useGuestsAll(config?.pves ?? [])
  const [logs, setLogs] = useState<LogLine[]>([])
  // Highlighted route: hovering a legend pill or a route card dims everything else in the
  // topology. Lifted here because the two hover surfaces live in different components.
  const [focus, setFocus] = useState<string | null>(null)

  // Same cadence as the run history — the activity log narrates the same events.
  useEffect(() => {
    const poll = () => api.logs(60).then(setLogs).catch(() => {})
    poll()
    const id = setInterval(poll, 8000)
    return () => clearInterval(id)
  }, [])

  const afterRouteChange = () => {
    reload().catch(() => {})
    refreshStatus().catch(() => {})
  }

  // TODO(M10): every dialog on this page. The buttons are wired to their subjects already,
  // so M10 only has to replace these with the modal that collects the power-off toggle.
  const openRouteEditor = (_route: Route | null) => {}
  const openManualRun = (_action: ManualAction) => {}
  const openPower = (_pbs: PbsDevice, _online: boolean) => {}
  const openStop = (_run: RunSummary) => {}

  return (
    <div className="jn-home">
      <div className="grid-top">
        <Topology
          routes={routes}
          pves={config?.pves ?? []}
          pbss={config?.pbss ?? []}
          status={status}
          guests={groups}
          focus={focus}
          onFocus={setFocus}
          onPower={openPower}
        />
        <div className="rail">
          <ManualRunPanel onRun={openManualRun} />
          <UpcomingRuns routes={routes} status={status} />
        </div>
      </div>

      <RouteStrip
        routes={routes}
        status={status}
        runs={runs}
        onFocus={setFocus}
        onEdit={openRouteEditor}
        onChanged={afterRouteChange}
      />

      <div className="grid-bottom">
        <RunHistory
          runs={runs}
          routes={routes}
          error={runsError}
          liveLines={liveLines}
          liveRunId={liveRunId}
          onStop={openStop}
        />
        <div className="gcell">
          <GuestLastBackup groups={groups} loaded={guestsLoaded} />
        </div>
      </div>

      <section className="panel">
        <div className="panel-hd">
          <h2>{t('dashboard.activityLog')}</h2>
          <span className="count">{t('dashboard.events', { n: logs.length })}</span>
        </div>
        <div style={{ padding: '12px 0 6px' }}>
          <ActivityLog logs={logs} />
        </div>
      </section>
    </div>
  )
}
