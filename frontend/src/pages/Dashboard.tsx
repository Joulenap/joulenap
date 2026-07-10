import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import type { Config, GuestInfo, LogLine, StatusResponse } from '../api/types'
import { ConfirmModal, type ConfirmState } from '../components/ConfirmModal'
import { useConfig } from '../config/ConfigContext'
import { useTaskLog } from '../hooks/useTaskLog'
import { buildCron, isAdvancedSchedule, parseCron } from '../utils/cron'
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
  // 'exclude' is a read-only escape hatch: the backend supports it but the simple
  // switcher can't represent "back up everything except these", so the panel locks it
  // (mirrors the advanced-schedule escape hatch) rather than corrupting it into 'include'.
  guestsMode: 'general' | 'selective' | 'exclude'
  selected: number[]
}

function draftFromConfig(cfg: Config): Draft {
  const { time, days, dom, month } = parseCron(cfg.backup.schedule)
  return {
    time,
    days,
    dom: dom ?? '*',
    month: month ?? '*',
    rawSchedule: cfg.backup.schedule,
    gcEnabled: cfg.maintenance.gc.enabled,
    keepDaily: cfg.backup.retention.keep_daily,
    keepWeekly: cfg.backup.retention.keep_weekly,
    keepMonthly: cfg.backup.retention.keep_monthly,
    wakeTimeout: cfg.pbs.wait_timeout,
    wakeRetries: cfg.pbs.wol_retries,
    guestsMode:
      cfg.backup.guests.mode === 'all'
        ? 'general'
        : cfg.backup.guests.mode === 'exclude'
          ? 'exclude'
          : 'selective',
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
  // "Keep PBS on after the job" choice for the backup/GC confirm dialog. A ref mirrors it so
  // the confirm's onConfirm (captured at setConfirm time) reads the latest value.
  const [keepOn, setKeepOn] = useState(false)
  const keepOnRef = useRef(false)

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

  // Refresh status + logs a few times after kicking off an async job. Track the timer ids so
  // they can be cancelled on unmount (consistent with the cleaned-up intervals above).
  const pollTimers = useRef<number[]>([])
  const pollAfterAction = useCallback(() => {
    loadLogs()
    refreshStatus()
    const times = [2000, 5000, 9000]
    times.forEach((ms) =>
      pollTimers.current.push(window.setTimeout(() => { loadLogs(); refreshStatus() }, ms)),
    )
  }, [loadLogs, refreshStatus])

  useEffect(() => () => pollTimers.current.forEach(clearTimeout), [])

  useEffect(() => {
    keepOnRef.current = keepOn
  }, [keepOn])

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
    next.backup.schedule = isAdvancedSchedule({
      time: draft.time,
      days: draft.days,
      dom: draft.dom,
      month: draft.month,
    })
      ? draft.rawSchedule
      : buildCron({ time: draft.time, days: draft.days, dom: draft.dom, month: draft.month })
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
    } else if (draft.guestsMode === 'exclude') {
      // Locked/read-only: preserve the stored exclude set verbatim, never rewrite it as
      // 'include' (which would invert the backup set — FE-H1).
      next.backup.guests.mode = 'exclude'
      next.backup.guests.list = [...draft.selected].sort((a, b) => a - b)
    } else {
      next.backup.guests.mode = 'include'
      next.backup.guests.list = [...draft.selected].sort((a, b) => a - b)
    }
    await save(next)
    loadLogs()
  }

  const runAction = (
    key: 'backup' | 'gc' | 'on' | 'off',
    fn: (keepOn: boolean) => Promise<unknown>,
    danger = false,
    icon = '▶',
  ) => {
    const isJob = key === 'backup' || key === 'gc'
    // Default the switch to the PBS's current state: already-on stays on (e.g. woken for a
    // restore); asleep goes back to sleep after the job. The user can override either way.
    const initialKeepOn = !!status?.pbs_online
    if (isJob) setKeepOn(initialKeepOn)
    setConfirm({
      title: t(`dashboard.confirm.${key}Title`),
      message: t(`dashboard.confirm.${key}Msg`, { timeout: draft.wakeTimeout }),
      confirmLabel: t(`dashboard.confirm.${key}Yes`),
      danger,
      icon,
      ...(isJob
        ? { toggle: { label: t('dashboard.confirm.keepOn'), value: initialKeepOn, onChange: setKeepOn } }
        : {}),
      onConfirm: async () => {
        try {
          await fn(keepOnRef.current)
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
      <div className="jn-row-actions">
        <ManualPanel
          status={status}
          onBackup={() => runAction('backup', (k) => api.runBackup(k), false, '▶')}
          onGc={() => runAction('gc', (k) => api.runGc(k), false, '⟳')}
          onPowerOn={() => runAction('on', () => api.powerOn(), false, '⏻')}
          onPowerOff={() => runAction('off', () => api.powerOff(), true, '⏻')}
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

      <div className="jn-row-guests">
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

      <ConfirmModal
        state={
          confirm && confirm.toggle
            ? { ...confirm, toggle: { ...confirm.toggle, value: keepOn, onChange: setKeepOn } }
            : confirm
        }
        onCancel={() => setConfirm(null)}
      />
    </>
  )
}
