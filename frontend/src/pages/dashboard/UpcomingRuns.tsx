import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Route, StatusResponse } from '../../api/types'
import { fmtDT } from '../../utils/format'
import { upcomingRows } from '../../utils/upcoming'

/** Natural row height from dashboard.css (.uprow padding + line-height). */
const ROW_PX = 38

/**
 * The elastic panel of the right rail: it is the one that grows, so the rail matches the
 * topology's height.
 *
 * The fill is row-granular. `.uplist` is `flex:1; overflow:hidden`, so its height is handed
 * down by the panel stretch and never depends on how many rows are inside it — measuring it
 * cannot feed back into itself. The rows are `flex: 1 0 auto`, so whatever pixels are left
 * over after whole rows are shared out and the list sits flush with the panel bottom.
 */
export function UpcomingRuns({
  routes,
  status,
}: {
  routes: Route[]
  status: StatusResponse | null
}) {
  const { t } = useTranslation()
  const listRef = useRef<HTMLDivElement | null>(null)
  const [capacity, setCapacity] = useState(0)

  useEffect(() => {
    const el = listRef.current
    if (!el) return
    const measure = () => setCapacity(Math.floor(el.clientHeight / ROW_PX))
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const running = status?.running
  const rows = upcomingRows({
    running: running
      ? {
          routeId: running.route_id,
          routeName: running.route_name || running.route_id || t('dashboard.adhocRun'),
        }
      : null,
    nextRuns: status?.next_runs ?? [],
    routes,
    capacity,
  })

  return (
    <section className="panel elastic">
      <div className="panel-hd">
        <h2>{t('dashboard.upcomingRuns')}</h2>
      </div>
      <div className="uplist" ref={listRef}>
        {rows.length === 0 ? (
          <div className="panel-empty">
            {status?.state === 'paused' ? t('common.schedulerPaused') : t('dashboard.noUpcoming')}
          </div>
        ) : (
          rows.map((row) => (
            <div className={`uprow${row.now ? ' now' : ''}`} key={row.key}>
              <span className="rdot" style={{ background: row.color }} />
              <span className="rname">{row.routeName}</span>
              <span className="uwhen">
                {row.at ? fmtDT(new Date(row.at)) : t('dashboard.runRunning')}
              </span>
            </div>
          ))
        )}
      </div>
    </section>
  )
}
