import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useUnsavedGuard } from '../shell/UnsavedGuard'
import { c } from '../theme'
import { Account } from './settings/Account'
import { Advanced } from './settings/Advanced'
import { BackupSafety } from './settings/BackupSafety'
import { Integrations, UpdateCheck } from './settings/Integrations'
import { Localization } from './settings/Localization'
import { Notifications } from './settings/Notifications'
import { SetupWizard } from './settings/SetupWizard'

export type Tab =
  | 'localization'
  | 'account'
  | 'notifications'
  | 'setup'
  | 'safety'
  | 'integrations'
  | 'advanced'

const NAV: { key: Tab }[] = [
  { key: 'localization' },
  { key: 'account' },
  { key: 'notifications' },
  { key: 'setup' },
  { key: 'safety' },
  { key: 'integrations' },
  { key: 'advanced' },
]

export function Settings(_props: { onClose: () => void; initialTab?: Tab }) {
  const { t } = useTranslation()
  const { guard } = useUnsavedGuard()
  // Settings is remounted each time the shell switches to it (unmounted on the main view), so
  // this initial value applies afresh every open — the dashboard's "Run setup wizard" CTA
  // opens straight on the setup tab, while the gear opens Localization as before.
  const [tab, setTab] = useState<Tab>(_props.initialTab ?? 'localization')

  return (
    <div className="jn-settings">
      <nav
        className="jn-settings-nav"
        style={{
          background: c.panel,
          border: `1px solid ${c.border}`,
          borderRadius: 12,
          padding: 7,
        }}
      >
        {NAV.map(({ key }) => {
          const active = tab === key
          return (
            <button
              key={key}
              className="jn-settings-navbtn"
              onClick={() => guard(() => setTab(key))}
              style={{
                background: active ? 'rgba(232,131,15,.1)' : 'transparent',
                border: `1px solid ${active ? 'rgba(232,131,15,.32)' : 'transparent'}`,
                borderRadius: 8,
                padding: '9px 11px',
                cursor: 'pointer',
                color: active ? c.text : c.textMid,
              }}
            >
              <span
                className="jn-settings-rail"
                style={{
                  borderRadius: 2,
                  background: active ? c.accent : 'transparent',
                }}
              />
              <span style={{ display: 'flex', flexDirection: 'column', gap: 1, alignItems: 'flex-start', minWidth: 0 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>{t(`settings.nav.${key}`)}</span>
                <span
                  className="jn-settings-hint"
                  style={{
                    fontSize: 11,
                    fontWeight: 500,
                    color: c.textFaint,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {t(`settings.nav.${key}Hint`)}
                </span>
              </span>
            </button>
          )
        })}
      </nav>

      <div style={{ minWidth: 0 }}>
        {tab === 'localization' && <Localization />}
        {tab === 'account' && <Account />}
        {tab === 'notifications' && <Notifications />}
        {tab === 'setup' && <SetupWizard />}
        {tab === 'safety' && <BackupSafety />}
        {tab === 'integrations' && (
          <>
            <Integrations />
            <UpdateCheck />
          </>
        )}
        {tab === 'advanced' && <Advanced />}
      </div>
    </div>
  )
}
