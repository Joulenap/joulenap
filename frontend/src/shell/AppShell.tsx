import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { Spinner } from '../components/Spinner'
import { ConfigProvider, useConfig } from '../config/ConfigContext'
import { Dashboard } from '../pages/Dashboard'
import { Settings, type Tab } from '../pages/Settings'
import { isConfigured } from '../pages/settings/SetupWizard'
import { useStatus } from '../hooks/useStatus'
import { c } from '../theme'
import { WizardProvider } from '../wizard/WizardContext'
import { Header } from './Header'
import { UnsavedGuardProvider, useUnsavedGuard } from './UnsavedGuard'

type View = 'main' | 'settings'

function ShellInner() {
  const { t } = useTranslation()
  const { logout } = useAuth()
  const { config, loading } = useConfig()
  const { status, refresh, stale } = useStatus()
  const { guard } = useUnsavedGuard()
  const [view, setView] = useState<View>('main')
  const [settingsTab, setSettingsTab] = useState<Tab>('localization')
  const [upd, setUpd] = useState<Awaited<ReturnType<typeof api.update>> | null>(null)

  const openSettings = (tab: Tab) => {
    setSettingsTab(tab)
    setView('settings')
  }

  // Fresh install / wizard never completed: PVE+PBS aren't wired up, so backups can't run.
  // Nudge the user into the wizard — only on the dashboard, so we don't nag while they're
  // already in Settings configuring it.
  const notConfigured = view === 'main' && !!config && !isConfigured(config)

  // The running version for the footer, plus the newer-release badge when the user opted
  // into the update check (the backend caches it; disabled => no outbound call at all).
  // Re-runs when the toggle flips so the badge appears without a reload.
  const updateCheck = config?.app.update_check
  useEffect(() => {
    api.update().then(setUpd).catch(() => {})
  }, [updateCheck])

  return (
    <div className="jn-shell">
      <div style={{ maxWidth: 1220, margin: '0 auto' }}>
        {stale && (
          <div
            role="status"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              background: 'rgba(229,103,91,.12)',
              border: `1px solid ${c.red}`,
              borderRadius: 8,
              padding: '9px 14px',
              marginBottom: 12,
              fontSize: 12.5,
              color: c.red,
            }}
          >
            ⚠ {t('common.backendUnreachable')}
          </div>
        )}
        {notConfigured && (
          <div
            role="status"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 14,
              flexWrap: 'wrap',
              background: 'rgba(232,131,15,.1)',
              border: '1px solid rgba(232,131,15,.4)',
              borderRadius: 8,
              padding: '10px 14px',
              marginBottom: 12,
            }}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: c.textMid }}>
              ⚙ {t('common.notConfigured')}
            </span>
            <button
              onClick={() => guard(() => openSettings('setup'))}
              style={{
                background: c.accent,
                color: c.accentInk,
                border: 'none',
                borderRadius: 7,
                padding: '7px 16px',
                fontSize: 12.5,
                fontWeight: 600,
                cursor: 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              {t('common.runSetup')}
            </button>
          </div>
        )}
        <Header
          host={config?.pbs.host ?? ''}
          status={status}
          view={view}
          onToggleView={() => guard(() => (view === 'main' ? openSettings('localization') : setView('main')))}
          onLogout={logout}
        />
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
            <Spinner size={26} />
          </div>
        ) : view === 'main' ? (
          <Dashboard status={status} refreshStatus={refresh} />
        ) : (
          <Settings onClose={() => setView('main')} initialTab={settingsTab} />
        )}
        <footer
          style={{
            textAlign: 'center',
            marginTop: 28,
            fontSize: 12,
            color: c.textFaint,
          }}
        >
          Joulenap{upd && ` v${upd.current}`}
          {upd?.update_available && (
            <>
              {' · '}
              <a
                href={upd.url}
                target="_blank"
                rel="noreferrer"
                style={{ color: c.accent, fontWeight: 600 }}
              >
                {t('settings.updates.available', { version: upd.latest.replace(/^v/, '') })}
              </a>
            </>
          )}
        </footer>
      </div>
    </div>
  )
}

export function AppShell() {
  // WizardProvider sits above the view switch so setup-wizard progress survives navigation;
  // it unmounts with AppShell on logout, clearing the secrets it holds (see WizardContext).
  return (
    <ConfigProvider>
      <WizardProvider>
        <UnsavedGuardProvider>
          <ShellInner />
        </UnsavedGuardProvider>
      </WizardProvider>
    </ConfigProvider>
  )
}
