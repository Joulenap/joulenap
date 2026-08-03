import { useTranslation } from 'react-i18next'

export type ManualAction = 'route' | 'gc' | 'verify'

/**
 * Three buttons, three dialogs. The dialogs themselves land in M10 — each of these picks a
 * subject (a route, or a PBS) and carries a "power off when finished" toggle, which is a
 * form, not a button.
 */
export function ManualRunPanel({ onRun }: { onRun: (action: ManualAction) => void }) {
  const { t } = useTranslation()
  return (
    <section className="panel">
      <div className="panel-hd">
        <h2>{t('dashboard.manualRun')}</h2>
      </div>
      <div className="panel-bd man-btns">
        <button type="button" className="btn btn-accent" onClick={() => onRun('route')}>
          ▶ {t('dashboard.runRoute')}
        </button>
        <button type="button" className="btn" onClick={() => onRun('gc')}>
          {t('dashboard.runGcShort')}
        </button>
        <button type="button" className="btn" onClick={() => onRun('verify')}>
          {t('dashboard.runVerifyShort')}
        </button>
      </div>
    </section>
  )
}
