import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api/client'

interface AuthState {
  loading: boolean
  authenticated: boolean
  setupNeeded: boolean
  username: string | null
}

interface AuthContextValue extends AuthState {
  refresh: () => Promise<void>
  login: (username: string, password: string) => Promise<void>
  setup: (username: string, password: string, timezone: string) => Promise<void>
  logout: () => Promise<void>
  setUsername: (username: string) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    loading: true,
    authenticated: false,
    setupNeeded: false,
    username: null,
  })

  const refresh = useCallback(async () => {
    const s = await api.authStatus()
    setState({
      loading: false,
      authenticated: s.authenticated,
      setupNeeded: s.setup_needed,
      username: s.username,
    })
  }, [])

  useEffect(() => {
    refresh().catch(() => setState((p) => ({ ...p, loading: false })))
  }, [refresh])

  const login = useCallback(async (username: string, password: string) => {
    const u = await api.login(username, password)
    setState((p) => ({ ...p, authenticated: true, setupNeeded: false, username: u.username }))
  }, [])

  const setup = useCallback(async (username: string, password: string, timezone: string) => {
    const u = await api.setup(username, password, timezone)
    setState((p) => ({ ...p, authenticated: true, setupNeeded: false, username: u.username }))
  }, [])

  const logout = useCallback(async () => {
    await api.logout()
    setState((p) => ({ ...p, authenticated: false }))
  }, [])

  const setUsername = useCallback((username: string) => {
    setState((p) => ({ ...p, username }))
  }, [])

  return (
    <AuthContext.Provider value={{ ...state, refresh, login, setup, logout, setUsername }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
