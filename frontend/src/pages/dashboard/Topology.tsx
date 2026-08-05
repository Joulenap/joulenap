import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { PbsDevice, PveDevice, Route, StatusResponse } from '../../api/types'
import { fmtBytesTB, fmtDT } from '../../utils/format'
import type { PveGuests } from '../../utils/guestPanel'
import {
  pbsNodeId,
  pveNodeId,
  routeDevices,
  routeLinks,
  routePbss,
  routeUsesPve,
} from '../../utils/routes'
import { crossColumnMx, fanSpread, sameColumnMx, wirePath } from '../../utils/topology'

const PveIcon = () => (
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <rect x="1.5" y="2" width="13" height="5" rx="1.2" />
    <rect x="1.5" y="9" width="13" height="5" rx="1.2" />
    <circle cx="4.2" cy="4.5" r="0.9" fill="currentColor" stroke="none" />
    <circle cx="4.2" cy="11.5" r="0.9" fill="currentColor" stroke="none" />
  </svg>
)

const PbsIcon = () => (
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <rect x="2" y="1.5" width="12" height="13" rx="1.5" />
    <path d="M5 4.5h6M5 7h6" />
    <circle cx="8" cy="11" r="1.4" />
  </svg>
)

interface Wire {
  key: string
  routeId: string
  color: string
  d: string
  /** Where the arrowhead dot sits: the target end. */
  tx: number
  ty: number
  running: boolean
  dashed: boolean
}

interface TopologyProps {
  routes: Route[]
  pves: PveDevice[]
  pbss: PbsDevice[]
  status: StatusResponse | null
  guests: PveGuests[]
  focus: string | null
  onFocus: (routeId: string | null) => void
  onPower: (pbs: PbsDevice, online: boolean) => void
}

export function Topology({
  routes,
  pves,
  pbss,
  status,
  guests,
  focus,
  onFocus,
  onPower,
}: TopologyProps) {
  const { t } = useTranslation()
  const diagramRef = useRef<HTMLDivElement | null>(null)
  const cardsRef = useRef(new Map<string, HTMLElement>())
  const [wires, setWires] = useState<Wire[]>([])
  const [box, setBox] = useState({ w: 0, h: 0 })

  const runningRouteId = status?.running?.route_id ?? null

  const setCard = useCallback((key: string, el: HTMLElement | null) => {
    if (el) cardsRef.current.set(key, el)
    else cardsRef.current.delete(key)
  }, [])

  // Geometry comes from the laid-out DOM (the cards are a plain flex column, so nothing
  // else knows where they end up), but the wires themselves are React elements — the SVG is
  // never mutated behind React's back.
  const draw = useCallback(() => {
    const diagram = diagramRef.current
    if (!diagram) return
    const d = diagram.getBoundingClientRect()
    const anchor = (el: HTMLElement, side: 'left' | 'right', offsetY: number) => {
      const r = el.getBoundingClientRect()
      return {
        x: (side === 'right' ? r.right : r.left) - d.left,
        y: r.top - d.top + r.height / 2 + offsetY,
      }
    }

    // How many wires land on each target, so a fan-in can be spread instead of overlapping.
    const totals = new Map<string, number>()
    for (const route of routes) {
      for (const [, to] of routeLinks(route)) totals.set(to, (totals.get(to) ?? 0) + 1)
    }

    const inbound = new Map<string, number>()
    const next: Wire[] = []
    for (const route of routes) {
      for (const [from, to] of routeLinks(route)) {
        const srcEl = cardsRef.current.get(from)
        const tgtEl = cardsRef.current.get(to)
        if (!srcEl || !tgtEl) continue
        const i = (inbound.get(to) ?? 0) + 1
        inbound.set(to, i)
        const spread = fanSpread(i, totals.get(to) ?? 1)
        const sameCol = srcEl.parentElement === tgtEl.parentElement
        const s = anchor(srcEl, 'right', 0)
        const tgt = anchor(tgtEl, sameCol ? 'right' : 'left', sameCol ? 0 : spread)
        next.push({
          key: `${route.id}:${from}->${to}`,
          routeId: route.id,
          color: route.color,
          d: wirePath(s, tgt, sameCol ? sameColumnMx(s, tgt) : crossColumnMx(s, tgt)),
          tx: tgt.x,
          ty: tgt.y,
          running: route.id === runningRouteId,
          dashed: route.kind === 'sync',
        })
      }
    }
    setBox({ w: d.width, h: d.height })
    setWires(next)
  }, [routes, runningRouteId])

  useLayoutEffect(draw, [draw])

  useEffect(() => {
    const diagram = diagramRef.current
    if (!diagram) return
    const ro = new ResizeObserver(draw)
    ro.observe(diagram)
    window.addEventListener('resize', draw)
    // Web fonts land after first paint and reflow every card; without this the wires stay
    // pinned to the fallback-font layout.
    document.fonts?.ready.then(draw).catch(() => {})
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', draw)
    }
  }, [draw])

  const dimmed = (deviceKey: string) => {
    if (!focus) return ''
    const route = routes.find((r) => r.id === focus)
    if (!route) return ''
    return routeDevices(route).includes(deviceKey) ? ' hl' : ' dim'
  }

  return (
    <section className="panel">
      <div className="panel-hd">
        <h2>{t('dashboard.topology')}</h2>
        <div className="rlegend">
          {routes.map((route) => (
            <LegendPill
              key={route.id}
              route={route}
              status={status}
              onFocus={onFocus}
            />
          ))}
        </div>
      </div>
      <div className="panel-bd diagram-scroll">
        <div className="diagram" ref={diagramRef}>
          <svg className="wires" aria-hidden="true" viewBox={`0 0 ${box.w || 1} ${box.h || 1}`}>
            {wires.map((w) => {
              const off = focus !== null && focus !== w.routeId
              return (
                <g key={w.key}>
                  <path
                    d={w.d}
                    stroke={w.color}
                    strokeDasharray={!w.running && w.dashed ? '5 5' : undefined}
                    className={`${w.running ? 'running' : ''}${off ? ' dim' : ''}${
                      focus === w.routeId ? ' hl' : ''
                    }`}
                  />
                  <circle
                    cx={w.tx}
                    cy={w.ty}
                    r={3.2}
                    fill={w.color}
                    className={off ? 'dim' : ''}
                  />
                </g>
              )
            })}
          </svg>

          <div className="dcol">
            <span className="dcol-label">{t('dashboard.colPve')}</span>
            {pves.map((pve) => (
              <PveCard
                key={pve.id}
                pve={pve}
                routes={routes}
                status={status}
                guests={guests.find((g) => g.pve === pve.id)}
                className={dimmed(pveNodeId(pve.id))}
                setCard={setCard}
              />
            ))}
          </div>

          <div className="dcol">
            <span className="dcol-label">{t('dashboard.colPbs')}</span>
            {pbss.map((pbs) => (
              <PbsCard
                key={pbs.id}
                pbs={pbs}
                routes={routes}
                status={status}
                className={dimmed(pbsNodeId(pbs.id))}
                setCard={setCard}
                onPower={onPower}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}

function LegendPill({
  route,
  status,
  onFocus,
}: {
  route: Route
  status: StatusResponse | null
  onFocus: (id: string | null) => void
}) {
  const { t } = useTranslation()
  const running = status?.running?.route_id === route.id
  const next = status?.next_runs.find((n) => n.route_id === route.id)
  return (
    <span
      className="rpill"
      tabIndex={0}
      onMouseEnter={() => onFocus(route.id)}
      onMouseLeave={() => onFocus(null)}
      onFocus={() => onFocus(route.id)}
      onBlur={() => onFocus(null)}
    >
      <span className="rdot" style={{ background: route.color }} />
      {route.name || route.id}
      <span className={`rwhen${running ? ' run' : ''}`}>
        {running
          ? t('dashboard.runRunning')
          : next
            ? fmtDT(new Date(next.at))
            : t('dashboard.routePaused')}
      </span>
    </span>
  )
}

function PveCard({
  pve,
  routes,
  status,
  guests,
  className,
  setCard,
}: {
  pve: PveDevice
  routes: Route[]
  status: StatusResponse | null
  guests: PveGuests | undefined
  className: string
  setCard: (key: string, el: HTMLElement | null) => void
}) {
  const { t } = useTranslation()
  const online = status?.pves.find((p) => p.id === pve.id)?.online ?? false

  // The run in flight, and the queue behind it, both belong to a *route*; a PVE card cares
  // only about whether one of them reads from this box.
  const routeById = (id: string | null | undefined) => routes.find((r) => r.id === id)
  const runningRoute = routeById(status?.running?.route_id)
  const backingUp = !!runningRoute && routeUsesPve(runningRoute, pve.id)
  const queuedRoute = status?.queued
    .map((q) => routeById(q.route_id))
    .find((r) => r && routeUsesPve(r, pve.id))

  // Cluster or standalone is not in the config (M01: nodes are discovered at runtime), so
  // it is read off the guest listing — the same call the guest panel already makes. Falls
  // back to the host when that PVE could not be listed.
  const nodes = guests && !guests.error ? new Set(guests.guests.map((g) => g.node)).size : 0
  const sub = nodes
    ? nodes > 1
      ? t('dashboard.pveCluster', { nodes, count: guests?.guests.length ?? 0 })
      : t('dashboard.pveStandalone', { count: guests?.guests.length ?? 0 })
    : pve.host

  return (
    <div className={`device${className}`} ref={(el) => setCard(pveNodeId(pve.id), el)}>
      <div className="device-top">
        <span className="device-ico">
          <PveIcon />
        </span>
        <span className="device-name">{pve.id}</span>
      </div>
      <div className="device-host mono">{sub}</div>
      <div className="device-state">
        <span className={`dot ${online ? 'on' : 'off'}${backingUp ? ' pulse' : ''}`} />
        <span className={online ? 'state-on' : 'state-off'}>
          {backingUp
            ? t('dashboard.pveBackingUp')
            : queuedRoute
              ? t('dashboard.pveQueued', { route: queuedRoute.name || queuedRoute.id })
              : online
                ? t('dashboard.deviceOnline')
                : t('dashboard.deviceOffline')}
        </span>
      </div>
    </div>
  )
}

function PbsCard({
  pbs,
  routes,
  status,
  className,
  setCard,
  onPower,
}: {
  pbs: PbsDevice
  routes: Route[]
  status: StatusResponse | null
  className: string
  setCard: (key: string, el: HTMLElement | null) => void
  onPower: (pbs: PbsDevice, online: boolean) => void
}) {
  const { t } = useTranslation()
  const state = status?.pbss.find((p) => p.id === pbs.id)
  const online = state?.online ?? false
  const busy = (state?.holders ?? 0) > 0
  const ds = state?.datastore
  const runningRoute = routes.find((r) => r.id === status?.running?.route_id)
  const working = busy && !!runningRoute && routePbss(runningRoute).includes(pbs.id)

  return (
    <div className={`device${className}`} ref={(el) => setCard(pbsNodeId(pbs.id), el)}>
      <div className="device-top">
        <span className="device-ico">
          <PbsIcon />
        </span>
        <span className="device-name">{pbs.id}</span>
        {/* An always-on box has no power button at all: Joulenap neither wakes nor shuts it
            down, so offering the control would promise something it cannot do. */}
        {pbs.managed_power && (
          <button
            type="button"
            className="pwr"
            disabled={busy}
            onClick={() => onPower(pbs, online)}
            title={
              busy
                ? t('dashboard.powerLocked')
                : online
                  ? t('dashboard.powerOff')
                  : t('dashboard.powerOn')
            }
            aria-label={
              busy
                ? t('dashboard.powerLocked')
                : online
                  ? t('dashboard.powerOff')
                  : t('dashboard.powerOn')
            }
          >
            ⏻
          </button>
        )}
      </div>
      <div className="device-host mono">
        {t('dashboard.datastoreNamed', { name: pbs.datastore || '—' })}
      </div>
      {ds && (
        <>
          <div className="dbar">
            <div style={{ width: `${Math.min(100, Math.round(ds.used_pct))}%` }} />
          </div>
          <div className="dbar-lab">
            <span>{Math.round(ds.used_pct)}%</span>
            <span>
              {fmtBytesTB(ds.used)} / {fmtBytesTB(ds.total)}
            </span>
          </div>
        </>
      )}
      <div className="device-state">
        <span className={`dot ${online ? 'on' : 'off'}${working ? ' pulse' : ''}`} />
        <span className={online ? 'state-on' : 'state-off'}>
          {/* An always-on box is never "sleeping" — if it is unreachable that is a fault,
              and saying "Always on" while it is down would hide it. */}
          {!pbs.managed_power
            ? online
              ? t('dashboard.pbsAlwaysOn')
              : t('dashboard.deviceOffline')
            : working
              ? t('dashboard.pbsWorking', {
                  route: runningRoute?.name || runningRoute?.id,
                })
              : online
                ? t('dashboard.deviceOnline')
                : t('dashboard.pbsSleeping')}
        </span>
      </div>
    </div>
  )
}
