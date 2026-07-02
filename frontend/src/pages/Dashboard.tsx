import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import type { Config, GuestInfo, LogLine, StatusResponse } from '../api/types'
import { ConfirmModal, type ConfirmState } from '../components/ConfirmModal'
import { useConfig } from '../config/ConfigContext'
import { useTaskLog } from '../hooks/useTaskLog'
import { buildCron, parseCron } from '../utils/cron'
import { ActivityLog } from './dashboard/ActivityLog'
import { GuestsPanel } from './dashboard/GuestsPanel'
import { ManualPanel } from './dashboard/ManualPanel'
import { type SchedulerDraft, SchedulerCard } from './dashboard/SchedulerCard'
import { StatTiles } from './dashboard/StatTiles'
import { TaskLog } from './dashboard/TaskLog'

interface DashboardProps {
  status: StatusResponse | null
  refreshStatus: () => Promise<void>
}

interface Draft extends SchedulerDraft {
  guestsMode: 'general' | 'selective'
  selected: number[]
}

function draftFromConfig(cfg: Config): Draft {
  const { time, days } = parseCron(cfg.backup.schedule)
  return {
    time,
    days,
    gcEnabled: cfg.maintenance.gc.enabled,
    keepDaily: cfg.backup.retention.keep_daily,
    keepWeekly: cfg.backup.retention.keep_weekly,
    keepMonthly: cfg.backup.retention.keep_monthly,
    wakeTimeout: cfg.pbs.wait_timeout,
    wakeRetries: cfg.pbs.wol_retries,
    guestsMode: cfg.backup.guests.mode === 'all' ? 'general' : 'selective',
    selected: [...cfg.backup.guests.list],
  }
}

export function Dashboard({ status, refreshStatus }: DashboardProps) {
  const { t } = useTranslation()
  const { config, save } = useConfig()

  const [draft, setDraft] = useState<Draft | null>(null)
  const [enabled, setEnabled] = useState(false)
  const [guests, setGuests] = useState<GuestInfo[]>([])
  const [refreshing, setRefreshing] = useState(false)
  const [logs, setLogs] = useState<LogLine[]>([])
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)

  // Live PVE/PBS task narration (backup, GC, verify) for the Task-log panel.
  const jobRunning = status?.job_running ?? false
  const taskLog = useTaskLog(jobRunning)

  // (Re)sync the editable draft whenever the saved config changes (initial load + save).
  useEffect(() => {
    if (config) {
      setDraft(draftFromConfig(config))
      setEnabled(config.backup.enabled)
    }
  }, [config])

  const loadGuests = useCallback(async () => {
    setRefreshing(true)
    try {
      setGuests(await api.guests())
    } catch {
      setGuests([])
    } finally {
      setRefreshing(false)
    }
  }, [])

  const loadLogs = useCallback(async () => {
    try {
      setLogs(await api.logs(80))
    } catch {
      /* keep last */
    }
  }, [])

  useEffect(() => {
    loadGuests()
    loadLogs()
    const id = setInterval(loadLogs, 8000)
    return () => clearInterval(id)
  }, [loadGuests, loadLogs])

  // Refresh status + logs a few times after kicking off an async job.
  const pollAfterAction = useCallback(() => {
    loadLogs()
    refreshStatus()
    const times = [2000, 5000, 9000]
    times.forEach((ms) => setTimeout(() => { loadLogs(); refreshStatus() }, ms))
  }, [loadLogs, refreshStatus])

  const original = useMemo(() => (config ? draftFromConfig(config) : null), [config])
  const dirty = useMemo(() => {
    if (!draft || !original) return false
    const norm = (d: Draft) => ({ ...d, selected: [...d.selected].sort((a, b) => a - b) })
    return JSON.stringify(norm(draft)) !== JSON.stringify(norm(original))
  }, [draft, original])

  if (!config || !draft) return null

  const patch = (p: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...p } : d))

  const toggleEnabled = async () => {
    const next = !enabled
    setEnabled(next)
    try {
      await api.toggleScheduler(next)
      refreshStatus()
    } catch {
      setEnabled(!next) // revert on failure
    }
  }

  const apply = async () => {
    const next: Config = structuredClone(config)
    next.backup.enabled = enabled
    next.backup.schedule = buildCron({ time: draft.time, days: draft.days })
    next.backup.retention = {
      ...next.backup.retention,
      keep_daily: draft.keepDaily,
      keep_weekly: draft.keepWeekly,
      keep_monthly: draft.keepMonthly,
    }
    next.pbs.wait_timeout = draft.wakeTimeout
    next.pbs.wol_retries = draft.wakeRetries
    next.maintenance.gc.enabled = draft.gcEnabled
    if (draft.guestsMode === 'general') {
      next.backup.guests.mode = 'all'
    } else {
      next.backup.guests.mode = 'include'
      next.backup.guests.list = [...draft.selected].sort((a, b) => a - b)
    }
    await save(next)
    loadLogs()
  }

  const runAction = (
    key: 'backup' | 'gc' | 'on' | 'off',
    fn: () => Promise<unknown>,
    danger = false,
    icon = '▶',
  ) => {
    setConfirm({
      title: t(`dashboard.confirm.${key}Title`),
      message: t(`dashboard.confirm.${key}Msg`, { timeout: draft.wakeTimeout }),
      confirmLabel: t(`dashboard.confirm.${key}Yes`),
      danger,
      icon,
      onConfirm: async () => {
        try {
          await fn()
        } catch {
          /* surfaced in the activity log / status */
        }
        pollAfterAction()
      },
    })
  }

  const toggleGuest = (vmid: number) =>
    setDraft((d) => {
      if (!d) return d
      const sel = new Set(d.selected)
      sel.has(vmid) ? sel.delete(vmid) : sel.add(vmid)
      return { ...d, selected: [...sel] }
    })

  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: '360px 1fr', gap: 26, alignItems: 'start', paddingBottom: 16 }}>
        <ManualPanel
          status={status}
          onBackup={() => runAction('backup', api.runBackup, false, '▶')}
          onGc={() => runAction('gc', api.runGc, false, '⟳')}
          onPowerOn={() => runAction('on', api.powerOn, false, '⏻')}
          onPowerOff={() => runAction('off', api.powerOff, true, '⏻')}
        />
        <StatTiles status={status} />
      </div>

      <SchedulerCard
        enabled={enabled}
        onToggleEnabled={toggleEnabled}
        draft={draft}
        patch={patch}
        dirty={dirty}
        onApply={apply}
      />

      <div style={{ display: 'grid', gridTemplateColumns: '400px 1fr', gap: 22, alignItems: 'start', marginTop: 16 }}>
        <GuestsPanel
          guests={guests}
          mode={draft.guestsMode}
          onModeChange={(m) => patch({ guestsMode: m })}
          selected={new Set(draft.selected)}
          onToggleGuest={toggleGuest}
          onRefresh={loadGuests}
          refreshing={refreshing}
        />
        <ActivityLog logs={logs} />
      </div>

      <TaskLog lines={taskLog.lines} running={jobRunning} runId={taskLog.runId} />

      <ConfirmModal state={confirm} onCancel={() => setConfirm(null)} />
    </>
  )
}
