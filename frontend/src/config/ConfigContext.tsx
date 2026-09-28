import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import type { Config, ConfigPatch } from '../api/types'
import i18n from '../i18n'
import { applyTheme, currentTheme } from '../theme'

interface ConfigCtx {
  config: Config | null
  loading: boolean
  reload: () => Promise<void>
  /** Send only what changed (see `ConfigPatch`); resolves to the whole saved config. */
  save: (patch: ConfigPatch) => Promise<Config>
}

const Ctx = createContext<ConfigCtx | null>(null)

export function ConfigProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [loading, setLoading] = useState(true)

  const apply = (c: Config) => {
    setConfig(c)
    if (c.app?.language && i18n.language !== c.app.language) i18n.changeLanguage(c.app.language)
    if (c.app?.theme && c.app.theme !== currentTheme()) applyTheme(c.app.theme)
  }

  const reload = useCallback(async () => {
    apply(await api.getConfig())
    setLoading(false)
  }, [])

  useEffect(() => {
    reload().catch(() => setLoading(false))
  }, [reload])

  const save = useCallback(async (patch: ConfigPatch) => {
    const saved = await api.putConfig(patch)
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
