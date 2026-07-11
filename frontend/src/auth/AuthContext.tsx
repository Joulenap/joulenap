import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, setUnauthorizedHandler } from '../api/client'

interface AuthState {
  loading: boolean
  authenticated: boolean
  setupNeeded: boolean
  username: string | null
  // True when the session expired under us (a 401 reset auth) rather than an explicit logout,
  // so the Login screen can explain the sudden redirect. Cleared on the next successful sign-in.
  expired: boolean
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
    expired: false,
  })

  const refresh = useCallback(async () => {
    const s = await api.authStatus()
    setState({
      loading: false,
      authenticated: s.authenticated,
      setupNeeded: s.setup_needed,
      username: s.username,
      expired: false,
    })
  }, [])

  useEffect(() => {
    refresh().catch(() => setState((p) => ({ ...p, loading: false })))
  }, [refresh])

  // A 401 anywhere (expired cookie) resets auth client-side — no api.logout(), the session is
  // already dead — so the Gate falls back to Login. Guarded on `authenticated` so a stray 401
  // while already logged out doesn't spuriously flag "expired".
  useEffect(() => {
    setUnauthorizedHandler(() =>
      setState((p) => (p.authenticated ? { ...p, authenticated: false, expired: true } : p)),
    )
    return () => setUnauthorizedHandler(null)
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const u = await api.login(username, password)
    setState((p) => ({ ...p, authenticated: true, setupNeeded: false, username: u.username, expired: false }))
  }, [])

  const setup = useCallback(async (username: string, password: string, timezone: string) => {
    const u = await api.setup(username, password, timezone)
    setState((p) => ({ ...p, authenticated: true, setupNeeded: false, username: u.username, expired: false }))
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
