import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { c } from '../theme'
import { Account } from './settings/Account'
import { BackupSafety } from './settings/BackupSafety'
import { Localization } from './settings/Localization'
import { Notifications } from './settings/Notifications'
import { SetupWizard } from './settings/SetupWizard'

type Tab = 'localization' | 'account' | 'notifications' | 'setup' | 'safety'

const NAV: { key: Tab }[] = [
  { key: 'localization' },
  { key: 'account' },
  { key: 'notifications' },
  { key: 'setup' },
  { key: 'safety' },
]

export function Settings(_props: { onClose: () => void }) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<Tab>('localization')

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: 22, alignItems: 'start', paddingBottom: 28 }}>
      <nav
        style={{
          background: c.panel,
          border: `1px solid ${c.border}`,
          borderRadius: 12,
          padding: 7,
          display: 'flex',
          flexDirection: 'column',
          gap: 3,
          position: 'sticky',
          top: 22,
        }}
      >
        {NAV.map(({ key }) => {
          const active = tab === key
          return (
            <button
              key={key}
              onClick={() => setTab(key)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 11,
                width: '100%',
                textAlign: 'left',
                background: active ? 'rgba(232,131,15,.1)' : 'transparent',
                border: `1px solid ${active ? 'rgba(232,131,15,.32)' : 'transparent'}`,
                borderRadius: 8,
                padding: '9px 11px',
                cursor: 'pointer',
                color: active ? '#f0f2f4' : c.textMid,
              }}
            >
              <span
                style={{
                  width: 3,
                  alignSelf: 'stretch',
                  minHeight: 30,
                  borderRadius: 2,
                  background: active ? c.accent : 'transparent',
                  flex: '0 0 auto',
                }}
              />
              <span style={{ display: 'flex', flexDirection: 'column', gap: 1, alignItems: 'flex-start', minWidth: 0 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>{t(`settings.nav.${key}`)}</span>
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 500,
                    color: '#6f7884',
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
      </div>
    </div>
  )
}
