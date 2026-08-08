import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { Spinner } from '../components/Spinner'
import { ConfigProvider, useConfig } from '../config/ConfigContext'
import { Dashboard } from '../pages/Dashboard'
import { Settings, type Tab } from '../pages/Settings'
import { useStatus } from '../hooks/useStatus'
import { c, tint } from '../theme'
import { AddPveWizard } from '../wizard/AddPveWizard'
import { Header } from './Header'
import { UnsavedGuardProvider, useUnsavedGuard } from './UnsavedGuard'

type View = 'main' | 'settings'

// One full-width strip above or below the header. Red = something is wrong right now, amber =
// the app is deliberately not doing its job (paused, or not set up yet).
function Banner({ tone, children }: { tone: 'red' | 'amber'; children: ReactNode }) {
  const red = tone === 'red'
  return (
    <div
      role={red ? 'alert' : 'status'}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        background: red ? tint(c.red, 12) : tint(c.accent, 10),
        border: `1px solid ${red ? c.red : tint(c.accent, 40)}`,
        borderRadius: 8,
        padding: '10px 14px',
        marginBottom: 12,
        fontSize: 12.5,
        lineHeight: 1.5,
        color: red ? c.red : c.textMid,
      }}
    >
      {children}
    </div>
  )
}

function ShellInner() {
  const { t } = useTranslation()
  const { logout } = useAuth()
  const { config, loading } = useConfig()
  const { status, refresh, stale } = useStatus()
  const { guard } = useUnsavedGuard()
  const [view, setView] = useState<View>('main')
  const [settingsTab, setSettingsTab] = useState<Tab>('devices')
  const [upd, setUpd] = useState<Awaited<ReturnType<typeof api.update>> | null>(null)
  const [setupOpen, setSetupOpen] = useState(false)

  const openSettings = (tab: Tab) => {
    setSettingsTab(tab)
    setView('settings')
  }

  // Fresh install: with no PVE or no PBS there is nothing a route could connect, so nothing
  // can run. Only nagged on the homepage, not while the user is in Settings fixing it.
  const notConfigured =
    view === 'main' && !!config && (!config.pves.length || !config.pbss.length)

  // The running version for the footer, plus the newer-release badge when the user opted
  // into the update check (the backend caches it; disabled => no outbound call at all).
  // Re-runs when the toggle flips so the badge appears without a reload.
  const updateCheck = config?.app.update_check
  useEffect(() => {
    api.update().then(setUpd).catch(() => {})
  }, [updateCheck])

  return (
    <div className="jn-shell">
      {/* One canvas width for every view. Settings used to narrow to 1220px for its form
          measures, but the header lives in here too, so switching views visibly resized the
          chrome — worse than a slightly long field row. */}
      <div style={{ maxWidth: 1400, margin: '0 auto' }}>
        {stale && <Banner tone="red">⚠ {t('common.backendUnreachable')}</Banner>}
        {/* The first-run CTA: flow A also covers the PBS, so one button is the whole setup.
            It opens from the shell rather than from Settings so a fresh install never has to
            find the tab first. */}
        {notConfigured && (
          <Banner tone="amber">
            <span style={{ flex: 1 }}>⚙ {t('common.notConfigured')}</span>
            <button type="button" className="btn btn-accent" onClick={() => setSetupOpen(true)}>
              {t('common.runSetup')}
            </button>
          </Banner>
        )}
        <Header
          status={status}
          view={view}
          onToggleView={() => guard(() => (view === 'main' ? openSettings('devices') : setView('main')))}
          onLogout={logout}
        />
        {/* Below the header, not above it: these two describe the running app, and the pill
            they explain is in the header. A refused 0.9 -> 1.0 migration left the app with no
            devices and no routes — without this banner that is indistinguishable from a fresh
            install, and one save from the YAML editor would overwrite the user's only config. */}
        {status?.config_error && (
          <Banner tone="red">⚠ {t('common.configError', { reason: status.config_error })}</Banner>
        )}
        {status?.state === 'paused' && (
          <Banner tone="amber">⏸ {t('common.schedulerPaused')}</Banner>
        )}
        <main>
          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
              <Spinner size={26} />
            </div>
          ) : view === 'main' ? (
            <Dashboard status={status} refreshStatus={refresh} />
          ) : (
            <Settings
              onClose={() => setView('main')}
              initialTab={settingsTab}
              status={status}
              refreshStatus={refresh}
            />
          )}
        </main>
        <footer
          style={{
            textAlign: 'center',
            marginTop: 28,
            fontSize: 12.5,
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
        {setupOpen && <AddPveWizard onClose={() => setSetupOpen(false)} />}
      </div>
    </div>
  )
}

export function AppShell() {
  return (
    <ConfigProvider>
      <UnsavedGuardProvider>
        <ShellInner />
      </UnsavedGuardProvider>
    </ConfigProvider>
  )
}
