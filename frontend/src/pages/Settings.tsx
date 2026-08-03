import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { StatusResponse } from '../api/types'
import { useUnsavedGuard } from '../shell/UnsavedGuard'
import { SETTINGS_TABS, type SettingsTab, normalizeTab } from '../utils/deviceForm'
import { Account } from './settings/Account'
import { Advanced } from './settings/Advanced'
import { Devices } from './settings/Devices'
import { Integrations } from './settings/Integrations'
import { Notifications } from './settings/Notifications'

export type Tab = SettingsTab

interface SettingsProps {
  onClose: () => void
  initialTab?: Tab
  /** Feeds the device cards' connection dots; null until the first poll lands. */
  status: StatusResponse | null
  /** Re-reads /status so the shell's "scheduler paused" banner follows the kill-switch at
   *  once instead of after the next 5s tick. */
  refreshStatus: () => void
}

/**
 * The five top tabs of the mockup (`design/overhaul/src/set2.html`).
 *
 * The 0.9 left sidebar is gone, and with it "Setup" (the wizard is a flow, not a place — M12)
 * and "Localization" (folded into Account). Switching tabs goes through the unsaved guard, so
 * a half-edited form still asks before it is thrown away.
 */
export function Settings({ initialTab, status, refreshStatus }: SettingsProps) {
  const { t } = useTranslation()
  const { guard } = useUnsavedGuard()
  // Settings is remounted each time the shell switches to it (it unmounts on the main view),
  // so this initial value applies afresh on every open.
  const [tab, setTab] = useState<Tab>(normalizeTab(initialTab))

  return (
    <div className="jn-settings">
      <h1 className="page-title">{t('settings.title')}</h1>

      <nav className="tabs" role="tablist" aria-label={t('settings.title')}>
        {SETTINGS_TABS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={`tab${tab === key ? ' on' : ''}`}
            onClick={() => guard(() => setTab(key))}
          >
            {t(`settings.tabs.${key}`)}
          </button>
        ))}
      </nav>

      {tab === 'devices' && <Devices status={status} />}
      {tab === 'account' && <Account />}
      {tab === 'notifications' && <Notifications />}
      {tab === 'integrations' && <Integrations />}
      {tab === 'advanced' && <Advanced refreshStatus={refreshStatus} />}
    </div>
  )
}
