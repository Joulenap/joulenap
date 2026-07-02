import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import type { Config } from '../api/types'
import i18n from '../i18n'

interface ConfigCtx {
  config: Config | null
  loading: boolean
  reload: () => Promise<void>
  save: (config: Config) => Promise<Config>
}

const Ctx = createContext<ConfigCtx | null>(null)

export function ConfigProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [loading, setLoading] = useState(true)

  const apply = (c: Config) => {
    setConfig(c)
    if (c.app?.language && i18n.language !== c.app.language) i18n.changeLanguage(c.app.language)
  }

  const reload = useCallback(async () => {
    apply(await api.getConfig())
    setLoading(false)
  }, [])

  useEffect(() => {
    reload().catch(() => setLoading(false))
  }, [reload])

  const save = useCallback(async (c: Config) => {
    const saved = await api.putConfig(c)
    apply(saved)
    return saved
  }, [])

  return <Ctx.Provider value={{ config, loading, reload, save }}>{children}</Ctx.Provider>
}

export function useConfig(): ConfigCtx {
  const v = useContext(Ctx)
  if (!v) throw new Error('useConfig must be used within ConfigProvider')
  return v
}
