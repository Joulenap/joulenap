import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import type { LogLine, PbsDevice, Route, RunSummary, StatusResponse } from '../api/types'
import { useConfig } from '../config/ConfigContext'
import { useGuestsAll } from '../hooks/useGuestsAll'
import { useRuns } from '../hooks/useRuns'
import { useTaskLog } from '../hooks/useTaskLog'
import { runKindLabelKey } from '../utils/status'
import { ActionDialog, type ActionSpec } from './dashboard/ActionDialog'
import { ActivityLog } from './dashboard/ActivityLog'
import { GuestLastBackup } from './dashboard/GuestLastBackup'
import { ManualRunPanel, type ManualAction } from './dashboard/ManualRunPanel'
import { RouteModal } from './dashboard/RouteModal'
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

  // `null` = closed; `{route: null}` = the create form.
  const [editing, setEditing] = useState<{ route: Route | null } | null>(null)
  const [action, setAction] = useState<ActionSpec | null>(null)
  // A dialog closes on confirm, so a rejected action has nowhere of its own to report from.
  const [actionError, setActionError] = useState<string | null>(null)

  const afterRouteChange = () => {
    reload().catch(() => {})
    refreshStatus().catch(() => {})
  }

  /** Fire an action, then re-read the state it changed. */
  const run = (work: Promise<unknown>) => {
    setActionError(null)
    work
      .then(() => refreshStatus())
      .catch((e: Error) =>
        setActionError(t('dashboard.confirm.failed', { reason: e.message })),
      )
  }

  const routeOptions = routes.map((r) => ({ value: r.id, label: r.name || r.id }))
  const pbsOptions = (config?.pbss ?? []).map((p) => ({ value: p.id, label: p.id }))
  const powerOffLabel = t('dashboard.confirm.powerOffAfter')

  const openRouteEditor = (route: Route | null) => setEditing({ route })

  const openManualRun = (kind: ManualAction) => {
    if (kind === 'route') {
      setAction({
        title: t('dashboard.confirm.routeTitle'),
        select: {
          label: t('dashboard.confirm.routeLabel'),
          options: routeOptions,
          emptyNote: t('dashboard.confirm.noRoutes'),
        },
        toggle: powerOffLabel,
        confirmLabel: t('dashboard.confirm.routeYes'),
        // `keep_on` is the inverse of the toggle — see api/client.ts.
        onConfirm: (id, powerOff) => run(api.runRoute(id, !powerOff)),
      })
      return
    }
    const gc = kind === 'gc'
    setAction({
      title: t(gc ? 'dashboard.confirm.gcTitle' : 'dashboard.confirm.verifyTitle'),
      note: t(gc ? 'dashboard.confirm.gcNote' : 'dashboard.confirm.verifyNote'),
      select: {
        label: t('dashboard.confirm.gcLabel'),
        options: pbsOptions,
        emptyNote: t('dashboard.confirm.noPbs'),
      },
      toggle: powerOffLabel,
      confirmLabel: t(gc ? 'dashboard.confirm.gcYes' : 'dashboard.confirm.verifyYes'),
      onConfirm: (id, powerOff) => run(api.runMaintenance(id, kind, !powerOff)),
    })
  }

  const openPower = (pbs: PbsDevice, online: boolean) =>
    setAction({
      title: t(online ? 'dashboard.confirm.offTitle' : 'dashboard.confirm.onTitle'),
      note: online
        ? t('dashboard.confirm.offMsg')
        : t('dashboard.confirm.onNote', { pbs: pbs.id }),
      confirmLabel: t(online ? 'dashboard.confirm.offYes' : 'dashboard.confirm.onYes'),
      onConfirm: () => run(api.pbsPower(pbs.id, online ? 'poweroff' : 'wake')),
    })

  const openStop = (run_: RunSummary) =>
    setAction({
      title: t('dashboard.confirm.stopRouteTitle', {
        name: run_.route_name || t(runKindLabelKey(run_.kind)),
      }),
      note: t('dashboard.confirm.stopRouteMsg'),
      toggle: powerOffLabel,
      confirmLabel: t('dashboard.stopRun'),
      danger: true,
      // stopRun takes power_off directly, unlike runRoute's inverted keep_on.
      onConfirm: (_choice, powerOff) => run(api.stopRun(run_.id, powerOff)),
    })

  return (
    <div className="jn-home">
      {actionError && (
        <div className="form-banner" role="alert">
          {actionError}
        </div>
      )}
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
          <span className="count">{t('dashboard.events', { count: logs.length })}</span>
        </div>
        <div style={{ padding: '12px 0 6px' }}>
          <ActivityLog logs={logs} />
        </div>
      </section>

      {editing && (
        <RouteModal
          route={editing.route}
          routes={routes}
          pves={config?.pves ?? []}
          pbss={config?.pbss ?? []}
          groups={groups}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            afterRouteChange()
          }}
        />
      )}
      {action && <ActionDialog spec={action} onClose={() => setAction(null)} />}
    </div>
  )
}
