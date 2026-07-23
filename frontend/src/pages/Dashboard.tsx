import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, api } from '../api/client'
import type { Config, GuestInfo, LogLine, StatusResponse } from '../api/types'
import { ConfirmModal, type ConfirmState } from '../components/ConfirmModal'
import { useConfig } from '../config/ConfigContext'
import { useRegisterDirty } from '../shell/UnsavedGuard'
import { useTaskLog } from '../hooks/useTaskLog'
import { buildCron, isAdvancedSchedule, parseCron } from '../utils/cron'
import { guestsSelectionError } from '../utils/guests'
import { runKindLabelKey } from '../utils/status'
import { GuestsPanel } from './dashboard/GuestsPanel'
import { HistoryCard } from './dashboard/HistoryCard'
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
  const [guestsErr, setGuestsErr] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [logs, setLogs] = useState<LogLine[]>([])
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  // Scheduler "Apply changes" feedback, mirroring the settings tabs (FE-H2): busy disables
  // the button (no double-PUT), savedNote shows success, err surfaces a failed save.
  const [busy, setBusy] = useState(false)
  const [savedNote, setSavedNote] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // Failure of a manual action's *start* (Run backup/GC, Power on/off) — surfaced inline in the
  // ManualPanel rather than swallowed, since the activity log may not carry a start-time error
  // (FE-M4). Kept separate from the scheduler `err` so a "Power on failed" isn't shown on the
  // schedule card.
  const [actionErr, setActionErr] = useState<string | null>(null)
  // Failure of the instant "Enabled" scheduler toggle — shown next to the toggle so its silent
  // revert is explained (FE-M5). Separate from the Apply `err` slot at the bottom of the card.
  const [toggleErr, setToggleErr] = useState<string | null>(null)
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
      setGuestsErr(null)
    } catch (e) {
      // Keep the last-known list (like loadLogs) instead of blanking to an empty "0" panel;
      // surface the failure too, since guests have no auto-retry timer (FE-M8). The backup
      // itself never depends on this list — it's resolved live server-side at run time.
      setGuestsErr(e instanceof ApiError ? e.message : t('dashboard.guestsError'))
    } finally {
      setRefreshing(false)
    }
  }, [t])

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
  useRegisterDirty(dirty)

  if (!config || !draft) return null

  const patch = (p: Partial<Draft>) => {
    setDraft((d) => (d ? { ...d, ...p } : d))
    setSavedNote(false)
    setErr(null)
  }

  const toggleEnabled = async () => {
    const next = !enabled
    setEnabled(next)
    setToggleErr(null)
    try {
      await api.toggleScheduler(next)
      refreshStatus()
    } catch (e) {
      setEnabled(!next) // revert on failure
      setToggleErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    }
  }

  const apply = async () => {
    // Block Selective mode with no guests: it would save a schedule that wakes the PBS
    // and aborts every run without backing anything up (UX-8). Cleared on the next
    // guest toggle / mode change (patch + toggleGuest reset err).
    const guestErr = guestsSelectionError(draft.guestsMode, draft.selected.length)
    if (guestErr) {
      setErr(t(guestErr))
      return
    }
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
    setBusy(true)
    setErr(null)
    try {
      await save(next)
      setSavedNote(true)
      loadLogs()
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
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
        setActionErr(null)
        try {
          await fn(keepOnRef.current)
        } catch (e) {
          // Start-time failure (e.g. 502 WoL send failed, 409 already running, 500): show it —
          // the activity log can't be relied on to carry it.
          setActionErr(e instanceof ApiError ? e.message : t('dashboard.actionFailed'))
        }
        pollAfterAction()
      },
    })
  }

  // Stop the in-flight run (11.2). Reuses the same confirm as the run actions, with the
  // toggle repurposed: default OFF, because cancelling usually means "I want the box now".
  const stopAction = () => {
    const runId = status?.running_run_id
    if (typeof runId !== 'number') return
    const kind = status?.running_kind ?? 'cycle'
    setKeepOn(false)
    setConfirm({
      title: t('dashboard.confirm.stopTitle'),
      message: t('dashboard.confirm.stopMsg', { job: t(runKindLabelKey(kind)) }),
      confirmLabel: t('dashboard.confirm.stopYes'),
      danger: true,
      icon: '■',
      toggle: { label: t('dashboard.confirm.stopPowerOff'), value: false, onChange: setKeepOn },
      onConfirm: async () => {
        setActionErr(null)
        try {
          await api.cancelJob(runId, keepOnRef.current)
        } catch (e) {
          setActionErr(e instanceof ApiError ? e.message : t('dashboard.actionFailed'))
        }
        pollAfterAction()
      },
    })
  }

  const toggleGuest = (vmid: number) => {
    setSavedNote(false)
    setErr(null)
    setDraft((d) => {
      if (!d) return d
      const sel = new Set(d.selected)
      sel.has(vmid) ? sel.delete(vmid) : sel.add(vmid)
      return { ...d, selected: [...sel] }
    })
  }

  return (
    <>
      <div className="jn-row-actions">
        <ManualPanel
          status={status}
          error={actionErr}
          onBackup={() => runAction('backup', (k) => api.runBackup(k), false, '▶')}
          onGc={() => runAction('gc', (k) => api.runGc(k), false, '⟳')}
          onStop={stopAction}
          onPowerOn={() => runAction('on', () => api.powerOn(), false, '⏻')}
          onPowerOff={() => runAction('off', () => api.powerOff(), true, '⏻')}
        />
        <StatTiles status={status} />
      </div>

      <SchedulerCard
        enabled={enabled}
        onToggleEnabled={toggleEnabled}
        toggleError={toggleErr}
        draft={draft}
        patch={patch}
        dirty={dirty}
        onApply={apply}
        busy={busy}
        saved={savedNote}
        error={err}
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
          error={guestsErr}
        />
        <HistoryCard logs={logs} />
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
