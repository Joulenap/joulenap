import { AuthProvider, useAuth } from './auth/AuthContext'
import { FullPageSpinner } from './components/Spinner'
import { Login } from './pages/Login'
import { AppShell } from './shell/AppShell'

function Gate() {
  const { loading, authenticated } = useAuth()
  if (loading) return <FullPageSpinner />
  if (!authenticated) return <Login />
  return <AppShell />
}

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  )
}
