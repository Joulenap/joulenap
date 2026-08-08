import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import type { Route, RunSummary, StatusResponse } from '../../api/types'
import { Toggle } from '../../components/Toggle'
import { RUNS_LIMIT } from '../../hooks/useRuns'
import { fmtDT } from '../../utils/format'
import { routeKindBadge, routeSourceIds, scheduleSummary } from '../../utils/routes'
import { runStatusStyle } from '../../utils/status'

interface RouteStripProps {
  routes: Route[]
  status: StatusResponse | null
  runs: RunSummary[]
  onFocus: (routeId: string | null) => void
  onEdit: (route: Route | null) => void
  onRun: (route: Route) => void
  /** Re-read the config after a toggle, so the strip and the topology agree. */
  onChanged: () => void
  /** Surface a failed toggle in the page's action banner instead of a silent snap-back. */
  onError: (message: string) => void
}

export function RouteStrip({
  routes,
  status,
  runs,
  onFocus,
  onEdit,
  onRun,
  onChanged,
  onError,
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
              onRun={onRun}
              onChanged={onChanged}
              onError={onError}
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
  onRun,
  onChanged,
  onError,
}: {
  route: Route
  status: StatusResponse | null
  runs: RunSummary[]
  onFocus: (routeId: string | null) => void
  onEdit: (route: Route) => void
  onRun: (route: Route) => void
  onChanged: () => void
  onError: (message: string) => void
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
  // ...but that buffer is the newest RUNS_LIMIT runs across *all* routes, so with a few daily
  // routes a weekly or monthly one drops out of it within a week or two. Claiming it has
  // never run is about the worst thing a backup UI can say incorrectly, so once the window is
  // full, absence only licenses "nothing recent".
  const agedOut = !last && runs.length >= RUNS_LIMIT

  const toggle = async () => {
    setSaving(true)
    try {
      await api.updateRoute(route.id, { ...route, enabled: !route.enabled })
      onChanged()
    } catch (e) {
      // The config reload re-renders the real state (the toggle snaps back) — but the
      // snap-back alone reads as a glitch, so say *why* it refused to stick.
      onError(
        t('dashboard.toggleFailed', {
          name: route.name || route.id,
          reason: e instanceof Error ? e.message : String(e),
        }),
      )
      onChanged()
    } finally {
      setSaving(false)
    }
  }

  return (
    // Hover-only highlight: the dimming is a visual affordance with no announced effect, so
    // the card must not be a tab stop — its real controls (Run/toggle/Edit) already are.
    <div
      className={`route-card${route.enabled ? '' : ' off'}`}
      onMouseEnter={() => onFocus(route.id)}
      onMouseLeave={() => onFocus(null)}
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
          <span className="arrow" aria-hidden="true">
            →
          </span>
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
              {/* scheduleSummary() picks the key; `dashboard.onDays` carries the preposition
                  Italian needs around the list, so pass the days through it rather than
                  emitting them bare. */}
              {t(sched.daysKey, {
                days: sched.days.map((d) => t(`dashboard.days.${d}`)).join(', '),
              })}
            </>
          )}
        </div>
        <div>{routeDetail(route, t)}</div>
      </div>

      {/* The outcome pill is its own grid column, not part of .route-right: its text length
          varies per route ("Running" vs "OK · Sun 05/07 07:41"), and while it shared a column
          with the fixed-width controls that variation resized the whole card's grid, so the
          middle column started at a different x on every row. */}
      <div className="route-status">
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
            {t(agedOut ? 'dashboard.noRecentRuns' : 'dashboard.neverRun')}
          </span>
        )}
      </div>

      <div className="route-right">
        <Toggle
          on={route.enabled}
          onClick={saving ? () => {} : toggle}
          label={t('dashboard.routeEnabledLabel', { name: route.name })}
        />
        <button
          type="button"
          className="btn"
          // Already in flight or queued: a second submit would only queue a duplicate.
          disabled={running || queued}
          onClick={() => onRun(route)}
        >
          {t('dashboard.runNow')}
        </button>
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
            count: explicit.reduce((sum, s) => sum + s.guests.list.length, 0),
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
