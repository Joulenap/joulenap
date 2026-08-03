import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { Route, RunSummary, StatusResponse } from '../../api/types'
import { Toggle } from '../../components/Toggle'
import { fmtDT } from '../../utils/format'
import { routeKindBadge, routeSourceIds, scheduleSummary } from '../../utils/routes'
import { runStatusStyle } from '../../utils/status'

interface RouteStripProps {
  routes: Route[]
  status: StatusResponse | null
  runs: RunSummary[]
  onFocus: (routeId: string | null) => void
  onEdit: (route: Route | null) => void
  /** Re-read the config after a toggle, so the strip and the topology agree. */
  onChanged: () => void
}

export function RouteStrip({
  routes,
  status,
  runs,
  onFocus,
  onEdit,
  onChanged,
}: RouteStripProps) {
  const { t } = useTranslation()
  return (
    <section className="panel">
      <div className="panel-hd">
        <h2>
          {t('dashboard.routes')} <span className="count">({routes.length})</span>
        </h2>
        <button type="button" className="btn btn-accent" onClick={() => onEdit(null)}>
          {t('dashboard.newRoute')}
        </button>
      </div>
      {routes.length === 0 ? (
        <div className="panel-empty">{t('dashboard.noRoutes')}</div>
      ) : (
        <div className="panel-bd route-list">
          {routes.map((route) => (
            <RouteCard
              key={route.id}
              route={route}
              status={status}
              runs={runs}
              onFocus={onFocus}
              onEdit={onEdit}
              onChanged={onChanged}
            />
          ))}
        </div>
      )}
    </section>
  )
}

function RouteCard({
  route,
  status,
  runs,
  onFocus,
  onEdit,
  onChanged,
}: {
  route: Route
  status: StatusResponse | null
  runs: RunSummary[]
  onFocus: (routeId: string | null) => void
  onEdit: (route: Route) => void
  onChanged: () => void
}) {
  const { t } = useTranslation()
  const [saving, setSaving] = useState(false)
  const badge = routeKindBadge(route.kind)
  const sched = scheduleSummary(route.schedule)
  const running = status?.running?.route_id === route.id
  const queued = status?.queued.some((q) => q.route_id === route.id)
  // The history the page already polls also carries every route's last outcome, so the
  // result badge costs no extra request.
  const last = runs.find((r) => r.route_id === route.id && r.status !== 'running')

  const toggle = async () => {
    setSaving(true)
    try {
      await api.updateRoute(route.id, { ...route, enabled: !route.enabled })
      onChanged()
    } catch {
      // The next config reload re-renders the real state; a failed toggle simply snaps back.
      onChanged()
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className={`route-card${route.enabled ? '' : ' off'}`}
      tabIndex={0}
      onMouseEnter={() => onFocus(route.id)}
      onMouseLeave={() => onFocus(null)}
      onFocus={() => onFocus(route.id)}
      onBlur={() => onFocus(null)}
    >
      <div>
        <div className="route-name-row">
          <span className="route-dot" style={{ background: route.color }} />
          <span className="route-name">{route.name || route.id}</span>
          <span className={`badge ${badge.cls}`}>{t(badge.labelKey)}</span>
        </div>
        <div className="route-path">
          {routeSourceIds(route).map((id) => (
            <span className="chip-s" key={id}>
              {id}
            </span>
          ))}
          <span className="arrow">→</span>
          <span className="chip-s">{route.target}</span>
        </div>
      </div>

      <div className="route-mid">
        <div>
          {sched.cron ? (
            <span className="mono cron">{sched.cron}</span>
          ) : (
            <>
              <span className="mono cron">{sched.time}</span> ·{' '}
              {sched.daysKey === 'dashboard.everyDay'
                ? t('dashboard.everyDay')
                : sched.days.map((d) => t(`dashboard.days.${d}`)).join(', ')}
            </>
          )}
        </div>
        <div>{routeDetail(route, t)}</div>
      </div>

      <div className="route-right">
        {running ? (
          <span className="res" style={badgeStyle('running')}>
            ● {t('dashboard.runRunning')}
          </span>
        ) : queued ? (
          <span className="res" style={badgeStyle('running')}>
            {t('dashboard.runQueued')}
          </span>
        ) : last ? (
          <span className="res" style={badgeStyle(last.status)}>
            {t(runStatusStyle(last.status).labelKey)}
            {last.finished_at ? ` · ${fmtDT(new Date(last.finished_at))}` : ''}
          </span>
        ) : (
          <span className="res" style={{ color: 'var(--jn-text-muted)' }}>
            {t('dashboard.neverRun')}
          </span>
        )}
        <Toggle on={route.enabled} onClick={saving ? () => {} : toggle} />
        <button type="button" className="btn" onClick={() => onEdit(route)}>
          {t('common.edit')}
        </button>
      </div>
    </div>
  )
}

function badgeStyle(status: string) {
  const s = runStatusStyle(status)
  return { color: s.color, background: s.bg }
}

/** The second line of the middle column: what this route actually does, per kind. */
function routeDetail(route: Route, t: (k: string, o?: Record<string, unknown>) => string): string {
  const parts: string[] = []
  if (route.kind === 'backup') {
    const explicit = route.sources.filter((s) => s.guests.mode === 'include')
    parts.push(
      explicit.length
        ? t('dashboard.guestsSelected', {
            n: explicit.reduce((sum, s) => sum + s.guests.list.length, 0),
          })
        : t('dashboard.guestsAll'),
    )
  } else if (route.kind === 'sync') {
    parts.push(t(`dashboard.sync_${route.sync_direction}`))
  } else {
    parts.push(t(`dashboard.kind_${route.kind}_detail`))
  }
  // GC and verify-after are inert on External and Verify routes (M06), so they are only
  // worth naming where they actually run.
  if (route.kind === 'backup' || route.kind === 'sync') {
    parts.push(route.options.gc ? t('dashboard.withGc') : t('dashboard.noGc'))
  }
  return parts.join(' · ')
}
