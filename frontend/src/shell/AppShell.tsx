import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { Spinner } from '../components/Spinner'
import { ConfigProvider, useConfig } from '../config/ConfigContext'
import { Dashboard } from '../pages/Dashboard'
import { Settings } from '../pages/Settings'
import { useStatus } from '../hooks/useStatus'
import { c } from '../theme'
import { WizardProvider } from '../wizard/WizardContext'
import { Header } from './Header'

type View = 'main' | 'settings'

function ShellInner() {
  const { logout } = useAuth()
  const { config, loading } = useConfig()
  const { status, refresh } = useStatus()
  const [view, setView] = useState<View>('main')
  const [version, setVersion] = useState('')

  // Version is static per deploy — fetch the backend's once for the footer.
  useEffect(() => {
    api
      .health()
      .then((h) => setVersion(h.version))
      .catch(() => {})
  }, [])

  return (
    <div className="jn-shell">
      <div style={{ maxWidth: 1220, margin: '0 auto' }}>
        <Header
          host={config?.pbs.host ?? ''}
          status={status}
          view={view}
          onToggleView={() => setView((v) => (v === 'main' ? 'settings' : 'main'))}
          onLogout={logout}
        />
        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
            <Spinner size={26} />
          </div>
        ) : view === 'main' ? (
          <Dashboard status={status} refreshStatus={refresh} />
        ) : (
          <Settings onClose={() => setView('main')} />
        )}
        <footer
          style={{
            textAlign: 'center',
            marginTop: 28,
            fontSize: 12,
            color: c.textFaint,
          }}
        >
          Joulenap{version && ` v${version}`}
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
        <ShellInner />
      </WizardProvider>
    </ConfigProvider>
  )
}
