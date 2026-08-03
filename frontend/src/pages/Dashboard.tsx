import { useTranslation } from 'react-i18next'
import type { StatusResponse } from '../api/types'
import { c, panelStyle } from '../theme'

interface DashboardProps {
  status: StatusResponse | null
  // Not used yet; M09's manual-run panel is what calls it. Kept so the shell needs no edit.
  refreshStatus: () => Promise<void>
}

/**
 * Placeholder homepage.
 *
 * The 0.9 dashboard was built around one PVE and one PBS — a single schedule card, a
 * single-target manual panel, guest *selection* inside the page — none of which survives the
 * route model, so its panels were deleted rather than adapted. **M09** rebuilds this page as
 * the route-centric homepage (topology, route strip, right rail, run history with the live
 * task log, read-only guest panel) against these same props.
 *
 * Meanwhile it reports the counts it can see, so the shell is verifiable against the devStub
 * fixtures without a homepage.
 */
export function Dashboard({ status }: DashboardProps) {
  const { t } = useTranslation()
  return (
    <div style={{ ...panelStyle, padding: '22px 24px' }}>
      <span style={{ display: 'block', fontSize: 15, fontWeight: 700, marginBottom: 6 }}>
        {t('dashboard.placeholderTitle')}
      </span>
      <span style={{ display: 'block', fontSize: 13, color: c.textDim, lineHeight: 1.6 }}>
        {t('dashboard.placeholderBody', {
          pves: status?.pves.length ?? 0,
          pbss: status?.pbss.length ?? 0,
        })}
      </span>
    </div>
  )
}
