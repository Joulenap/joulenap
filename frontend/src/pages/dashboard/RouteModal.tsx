import { Fragment, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, api } from '../../api/client'
import type { PbsDevice, PveDevice, Route } from '../../api/types'
import { ConfirmModal, type ConfirmState } from '../../components/ConfirmModal'
import { Modal } from '../../components/Modal'
import { Toggle } from '../../components/Toggle'
import { useRevealBanner } from '../../components/useRevealBanner'
import { useRegisterDirty, useUnsavedGuard } from '../../shell/UnsavedGuard'
import { guestTypeLabel, type PveGuests } from '../../utils/guestPanel'
import { deviceId, pbsNodeId, pveNodeId, routeKindBadge } from '../../utils/routes'
import {
  ROUTE_COLORS,
  type FieldError,
  type RouteDraft,
  type RouteField,
  draftFromRoute,
  draftToRoute,
  guestTally,
  inferKind,
  isPicked,
  pveSources,
  retentionOverlaps,
  saveErrors,
  sectionsFor,
  showsGuestGroups,
  toggleGuest,
  validateDraft,
} from '../../utils/routeForm'

const DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
const COLOR_LABELS = ['Orange', 'Blue', 'Amber', 'Violet', 'Teal', 'Pink'].map(
  (n) => `dashboard.routeModal.color${n}`,
)
// The visible caption stays vzdump jargon ("keep-last"), which is what the PBS docs and the
// mockup show; the accessible name is real copy, so it gets a key.
const RETENTION_FIELDS = [
  ['keep_last', 'dashboard.routeModal.keepLast'],
  ['keep_daily', 'dashboard.routeModal.keepDaily'],
  ['keep_weekly', 'dashboard.routeModal.keepWeekly'],
  ['keep_monthly', 'dashboard.routeModal.keepMonthly'],
  ['keep_yearly', 'dashboard.routeModal.keepYearly'],
] as const
const MODE_OPTIONS = [
  ['snapshot', 'dashboard.routeModal.modeSnapshot'],
  ['suspend', 'dashboard.routeModal.modeSuspend'],
  ['stop', 'dashboard.routeModal.modeStop'],
] as const

interface RouteModalProps {
  /** `null` opens the create form. */
  route: Route | null
  /** Every route, for the id-collision check and the retention-overlap warning. */
  routes: Route[]
  pves: PveDevice[]
  pbss: PbsDevice[]
  /** The guest lists the page already polls — the modal issues no requests of its own. */
  groups: PveGuests[]
  onClose: () => void
  onSaved: () => void
}

/**
 * The route create/edit form (`design/overhaul/src/home2.html`).
 *
 * Everything that decides something lives in `utils/routeForm.ts` — the kind a device
 * selection implies, which sections that kind shows, the guest counter, what may be saved,
 * which other routes it will fight with over retention. This file only renders it, which is
 * what makes those rules testable at all (the harness has no DOM).
 */
export function RouteModal({ route, routes, pves, pbss, groups, onClose, onSaved }: RouteModalProps) {
  const { t } = useTranslation()
  const { guard } = useUnsavedGuard()
  const initial = useRef(draftFromRoute(route, pbss))
  const nameRef = useRef<HTMLInputElement>(null)
  const [draft, setDraft] = useState<RouteDraft>(initial.current)
  // Nothing is flagged until the first Save: complaining that a brand-new form has no name is
  // noise. After that the client-side rules re-run on every keystroke, so a fix clears its own
  // error instead of waiting for another click.
  const [attempted, setAttempted] = useState(false)
  const [serverErrors, setServerErrors] = useState<FieldError[]>([])
  const [saving, setSaving] = useState(false)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)

  const kind = inferKind(draft.sourceIds, draft.onWake)
  const sections = sectionsFor(kind)
  const badge = routeKindBadge(kind)
  const sources = pveSources(draft.sourceIds)
  const tally = guestTally(draft.sourceIds, groups, draft.selection)
  const overlaps = retentionOverlaps(draft, routes)

  const dirty = JSON.stringify(draft) !== JSON.stringify(initial.current)
  useRegisterDirty(dirty)
  const close = () => guard(onClose)

  const errors: FieldError[] = [
    ...(attempted ? validateDraft(draft, pves, pbss) : []),
    ...serverErrors,
  ]

  const patch = (over: Partial<RouteDraft>) => {
    // The backend's verdict was about the body we sent, so it stops applying the moment the
    // form changes; the client-side rules take over from here.
    setServerErrors([])
    setDraft((d) => ({ ...d, ...over }))
  }

  const errorsFor = (field: RouteField) => errors.filter((e) => e.field === field)
  const formErrors = errors.filter((e) => !e.field)
  useRevealBanner(formErrors.length > 0)
  const text = (e: FieldError) => e.message ?? t(e.key ?? '', e.params ?? {})

  // Keyed `pve:<id>` / `pbs:<id>`: a PVE and a PBS may share an id, and a bare one made the
  // two chips toggle each other.
  const toggleSource = (key: string) =>
    patch({
      sourceIds: draft.sourceIds.includes(key)
        ? draft.sourceIds.filter((s) => s !== key)
        : [...draft.sourceIds, key],
    })

  // Picking a target drops it from the sources: a box cannot sync with itself, and the
  // backend rejects it outright. Only the PBS key can match, so a PVE of the same name stays.
  const pickTarget = (id: string) =>
    patch({ target: id, sourceIds: draft.sourceIds.filter((s) => s !== pbsNodeId(id)) })

  const save = async () => {
    setAttempted(true)
    if (validateDraft(draft, pves, pbss).length) return
    const taken = routes.map((r) => r.id)
    const body = draftToRoute(draft, taken)
    setSaving(true)
    try {
      if (route) await api.updateRoute(route.id, body)
      else await api.createRoute(body)
      onSaved()
    } catch (e) {
      setServerErrors(
        e instanceof ApiError ? saveErrors(e) : [{ key: 'dashboard.routeModal.errSave' }],
      )
    } finally {
      setSaving(false)
    }
  }

  const askDelete = () => {
    if (!route) return
    setConfirm({
      title: t('dashboard.routeModal.deleteTitle'),
      message: t('dashboard.routeModal.deleteBody', { name: route.name || route.id }),
      confirmLabel: t('dashboard.routeModal.deleteYes'),
      danger: true,
      icon: '⚠',
      onConfirm: () => {
        api
          .deleteRoute(route.id)
          .then(onSaved)
          .catch((e) =>
            setServerErrors(
              e instanceof ApiError ? [{ message: e.message }] : [{ key: 'dashboard.routeModal.errSave' }],
            ),
          )
      },
    })
  }

  const footer = (
    <>
      {route && (
        <button type="button" className="btn btn-outline-danger foot-left" onClick={askDelete}>
          {t('dashboard.routeModal.delete')}
        </button>
      )}
      <button type="button" className="btn" onClick={close}>
        {t('common.cancel')}
      </button>
      <button type="button" className="btn btn-accent" disabled={saving} onClick={save}>
        {t('dashboard.routeModal.save')}
      </button>
    </>
  )

  return (
    <>
      <Modal
        size="lg"
        title={
          route
            ? t('dashboard.routeModal.editTitle', { name: route.name || route.id })
            : t('dashboard.routeModal.newTitle')
        }
        onClose={close}
        footer={footer}
        initialFocusRef={nameRef}
      >
        <div className="rm-bd">
          {formErrors.length > 0 && (
            <div className="form-banner" role="alert">
              {formErrors.map((e, i) => (
                <div key={i}>{text(e)}</div>
              ))}
            </div>
          )}

          {/* Name + colour */}
          <div className="frow">
            <div className="field">
              <label htmlFor="rm-name">{t('dashboard.routeModal.name')}</label>
              <input
                id="rm-name"
                ref={nameRef}
                type="text"
                autoComplete="off"
                value={draft.name}
                placeholder={t('dashboard.routeModal.namePlaceholder')}
                onChange={(e) => patch({ name: e.target.value })}
              />
              {errorsFor('name').map((e, i) => (
                <span className="err" key={i}>
                  {text(e)}
                </span>
              ))}
            </div>
            <div className="field">
              <span className="lab" id="rm-color">
                {t('dashboard.routeModal.color')}
              </span>
              <div className="swatches" role="group" aria-labelledby="rm-color">
                {ROUTE_COLORS.map((hex, i) => (
                  <button
                    type="button"
                    key={hex}
                    className={`swatch${draft.color === hex ? ' sel' : ''}`}
                    style={{ background: hex }}
                    aria-label={t(COLOR_LABELS[i])}
                    aria-pressed={draft.color === hex}
                    onClick={() => patch({ color: hex })}
                  />
                ))}
                {/* A native colour input disguised as a rainbow swatch: no picker library, and
                    the OS one is what the user already knows. */}
                <label
                  className={`swatch custom${ROUTE_COLORS.includes(draft.color) ? '' : ' sel'}`}
                  title={t('dashboard.routeModal.colorCustom')}
                  style={ROUTE_COLORS.includes(draft.color) ? undefined : { background: draft.color }}
                >
                  <input
                    type="color"
                    value={draft.color}
                    aria-label={t('dashboard.routeModal.colorCustom')}
                    onChange={(e) => patch({ color: e.target.value })}
                  />
                </label>
              </div>
            </div>
          </div>

          {/* Sources + target */}
          <div className="frow">
            <div className="field">
              <span className="lab" id="rm-sources">
                {t('dashboard.routeModal.sources')}
              </span>
              <div className="chips" role="group" aria-labelledby="rm-sources">
                {pves.length + pbss.length === 0 && (
                  <span className="help">{t('dashboard.routeModal.sourcesEmpty')}</span>
                )}
                {pves.map((p) => (
                  <SourceChip
                    key={pveNodeId(p.id)}
                    id={p.id}
                    kindLabel="PVE"
                    on={draft.sourceIds.includes(pveNodeId(p.id))}
                    onClick={() => toggleSource(pveNodeId(p.id))}
                  />
                ))}
                {pbss.map((p) => (
                  <SourceChip
                    key={pbsNodeId(p.id)}
                    id={p.id}
                    kindLabel="PBS"
                    on={draft.sourceIds.includes(pbsNodeId(p.id))}
                    disabled={p.id === draft.target}
                    title={p.id === draft.target ? t('dashboard.routeModal.targetIsSource') : undefined}
                    onClick={() => toggleSource(pbsNodeId(p.id))}
                  />
                ))}
              </div>
              <span className="help">{t('dashboard.routeModal.sourcesHelp')}</span>
              {errorsFor('sources').map((e, i) => (
                <span className="err" key={i}>
                  {text(e)}
                </span>
              ))}
            </div>
            <div className="field">
              <label htmlFor="rm-target">{t('dashboard.routeModal.target')}</label>
              <select id="rm-target" value={draft.target} onChange={(e) => pickTarget(e.target.value)}>
                {pbss.map((p) => (
                  <option key={p.id} value={p.id}>
                    {t('dashboard.routeModal.targetOption', { id: p.id, datastore: p.datastore })}
                  </option>
                ))}
              </select>
              <span className="help">{t('dashboard.routeModal.targetHelp')}</span>
              {errorsFor('target').map((e, i) => (
                <span className="err" key={i}>
                  {text(e)}
                </span>
              ))}
            </div>
          </div>

          {/* On wake — only a route with no source at all has this choice to make */}
          {sections.onWake && (
            <div className="field">
              <span className="lab" id="rm-onwake">
                {t('dashboard.routeModal.onWake')}
              </span>
              <div className="seg seg-wide" role="group" aria-labelledby="rm-onwake">
                <SegButton
                  on={draft.onWake === 'external'}
                  onClick={() => patch({ onWake: 'external' })}
                  label={t('dashboard.routeModal.onWakeExternal')}
                />
                <SegButton
                  on={draft.onWake === 'verify'}
                  onClick={() => patch({ onWake: 'verify' })}
                  label={t('dashboard.routeModal.onWakeVerify')}
                />
              </div>
              <span className="help">{t('dashboard.routeModal.onWakeHelp')}</span>
            </div>
          )}

          {/* Live preview */}
          <div className="field">
            <div className="preview-strip" aria-live="polite">
              <span className="preview-lab">{t('dashboard.routeModal.preview')}</span>
              {draft.sourceIds.length === 0 ? (
                <>
                  <span className="chip-s">{draft.target}</span>
                  <span className={`badge ${badge.cls}`}>{t(badge.labelKey)}</span>
                  <span className="preview-note">
                    {t(
                      draft.onWake === 'verify'
                        ? 'dashboard.routeModal.previewVerify'
                        : 'dashboard.routeModal.previewExternal',
                    )}
                  </span>
                </>
              ) : (
                <>
                  {draft.sourceIds.map((id, i) => (
                    <Fragment key={id}>
                      {i > 0 && <span className="arrow">+</span>}
                      {/* The draft holds kind-prefixed keys so a PVE and a PBS can share
                          an id; the preview shows the device, not the key. */}
                      <span className="chip-s">{deviceId(id)}</span>
                    </Fragment>
                  ))}
                  <svg width="46" height="10" aria-hidden="true">
                    <line
                      x1="1"
                      y1="5"
                      x2="38"
                      y2="5"
                      stroke={draft.color}
                      strokeWidth="2"
                      strokeDasharray={kind === 'sync' ? '5 4' : undefined}
                    />
                    <circle cx="41" cy="5" r="3" fill={draft.color} />
                  </svg>
                  <span className="chip-s">{draft.target}</span>
                  <span className={`badge ${badge.cls}`}>{t(badge.labelKey)}</span>
                  {kind === 'sync' && draft.sourceIds.length > 1 && (
                    <span className="preview-err">{t('dashboard.routeModal.errSyncSingle')}</span>
                  )}
                </>
              )}
            </div>
          </div>

          {/* Schedule */}
          {draft.cron ? (
            <div className="field">
              <span className="lab">{t('dashboard.scheduleTitle')}</span>
              <span className="help">{t('dashboard.scheduleCustom', { cron: draft.cron })}</span>
            </div>
          ) : (
            <div className="sched-row">
              <div className="field" style={{ width: 148 }}>
                <label htmlFor="rm-time">{t('dashboard.routeModal.time')}</label>
                <input
                  id="rm-time"
                  type="time"
                  value={draft.time}
                  onChange={(e) => patch({ time: e.target.value })}
                />
              </div>
              <div className="field">
                <span className="lab" id="rm-days">
                  {t('dashboard.routeModal.days')}
                </span>
                <div className="days" role="group" aria-labelledby="rm-days">
                  {DAY_KEYS.map((key, i) => (
                    <button
                      type="button"
                      key={key}
                      className={`day-btn${draft.days[i] ? ' on' : ''}`}
                      aria-pressed={draft.days[i]}
                      onClick={() =>
                        patch({ days: draft.days.map((on, j) => (i === j ? !on : on)) })
                      }
                    >
                      {t(`dashboard.days.${key}`)}
                    </button>
                  ))}
                </div>
                {errorsFor('days').map((e, i) => (
                  <span className="err" key={i}>
                    {text(e)}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Guests */}
          {sections.guests && (
            <div className="field">
              <span className="lab" id="rm-guests">
                {t('dashboard.routeModal.guests')}{' '}
                <span className="mono" style={{ fontWeight: 400, color: 'var(--jn-text-faint)', fontSize: 11 }}>
                  {tally.total === 0
                    ? ''
                    : draft.guestMode === 'all'
                      ? t('dashboard.routeModal.guestsCountAll', { count: tally.total })
                      : t('dashboard.routeModal.guestsCountSel', { chosen: tally.chosen, total: tally.total })}
                </span>
              </span>
              <div className="seg" role="group" aria-labelledby="rm-guests">
                <SegButton
                  on={draft.guestMode === 'all'}
                  onClick={() => patch({ guestMode: 'all', selection: {} })}
                  label={t('dashboard.routeModal.guestsAll')}
                />
                <SegButton
                  on={draft.guestMode === 'include'}
                  onClick={() => patch({ guestMode: 'include' })}
                  label={t('dashboard.routeModal.guestsSelection')}
                />
              </div>
              {draft.guestMode === 'all' ? (
                <span className="help">{t('dashboard.routeModal.guestsHelp')}</span>
              ) : (
                <div className="guest-list">
                  {sources.length === 0 && (
                    <div className="guest-empty">{t('dashboard.routeModal.guestsNoSource')}</div>
                  )}
                  {sources.map((pve) => {
                    const group = groups.find((g) => g.pve === pve)
                    const guests = group?.guests ?? []
                    const vmids = guests.map((g) => g.vmid)
                    return (
                      <div key={pve}>
                        {showsGuestGroups(draft.sourceIds) && (
                          <div className="guest-group">{pve}</div>
                        )}
                        {guests.length === 0 && (
                          // An unreachable PVE must not read as "this box has no guests" —
                          // the stored selection is still there and still saves correctly.
                          <div className="guest-empty">
                            {t(group?.error ? 'dashboard.guestsError' : 'dashboard.routeModal.guestsLoading')}
                          </div>
                        )}
                        {guests.map((g) => {
                          const on = isPicked(draft.selection, pve, g.vmid)
                          return (
                            <div className={`guest-row${on ? '' : ' off'}`} key={g.vmid}>
                              <Toggle
                                size="sm"
                                on={on}
                                label={t('dashboard.routeModal.guestIncludeLabel', {
                                  name: g.name,
                                  vmid: g.vmid,
                                })}
                                onClick={() =>
                                  patch({ selection: toggleGuest(draft.selection, pve, g.vmid, vmids) })
                                }
                              />
                              <span className={`gtype ${g.type === 'qemu' ? 'vm' : 'ct'}`}>
                                {guestTypeLabel(g.type)}
                              </span>
                              <span className="gid">{g.vmid}</span>
                              <span className="gname">{g.name}</span>
                            </div>
                          )
                        })}
                      </div>
                    )
                  })}
                </div>
              )}
              {errorsFor('guests').map((e, i) => (
                <span className="err" key={i}>
                  {text(e)}
                </span>
              ))}
            </div>
          )}

          {/* Sync direction */}
          {sections.sync && (
            <div className="field">
              <span className="lab">{t('dashboard.routeModal.syncOptions')}</span>
              <div className="radio-row">
                {(['pull', 'push'] as const).map((dir) => (
                  <label key={dir}>
                    <input
                      type="radio"
                      name="rm-sync"
                      checked={draft.syncDirection === dir}
                      onChange={() => patch({ syncDirection: dir })}
                    />
                    {t(`dashboard.routeModal.sync${dir === 'pull' ? 'Pull' : 'Push'}`)}
                  </label>
                ))}
              </div>
              <span className="help">{t('dashboard.routeModal.syncHelp')}</span>
            </div>
          )}

          {/* Retention */}
          {sections.retention && (
            <div className="field">
              <span className="lab">{t('dashboard.routeModal.retention')}</span>
              <div className="retention-row">
                {RETENTION_FIELDS.map(([key, labelKey]) => (
                  <div className="r" key={key}>
                    <span>{key.replace('_', '-')}</span>
                    <input
                      type="number"
                      min={0}
                      aria-label={t(labelKey)}
                      value={draft.retention[key]}
                      onChange={(e) =>
                        patch({ retention: { ...draft.retention, [key]: num(e.target.value) } })
                      }
                    />
                  </div>
                ))}
              </div>
              {overlaps.length > 0 && (
                <span className="form-warn">
                  {t('dashboard.routeModal.retentionOverlap', { routes: overlaps.join(', ') })}
                </span>
              )}
            </div>
          )}

          {/* Notifications */}
          <div className="field">
            <span className="lab">{t('dashboard.routeModal.notifications')}</span>
            <label className="tglrow">
              <Toggle size="sm" on={draft.notify} onClick={() => patch({ notify: !draft.notify })} />
              <span>{t('dashboard.routeModal.notifyLabel')}</span>
            </label>
            <span className="help">{t('dashboard.routeModal.notifyHelp')}</span>
          </div>

          {/* Advanced. Only the knobs this kind actually uses: mode/bwlimit/min-free are
              vzdump's, GC and quick-verify run on any route that leaves a PBS awake with new
              snapshots (M06: backup and sync), and reverify_days belongs to a Verify route. */}
          {kind !== 'external' && (
            <details className="adv">
              <summary>{t('dashboard.routeModal.advanced')}</summary>
              {kind === 'backup' && (
                <div className="adv-grid">
                  <div className="field">
                    <label htmlFor="rm-mode">{t('dashboard.routeModal.mode')}</label>
                    <select
                      id="rm-mode"
                      value={draft.options.mode}
                      onChange={(e) =>
                        patch({
                          options: { ...draft.options, mode: e.target.value as RouteDraft['options']['mode'] },
                        })
                      }
                    >
                      {MODE_OPTIONS.map(([value, labelKey]) => (
                        <option key={value} value={value}>
                          {t(labelKey)}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="rm-bw">{t('dashboard.routeModal.bwlimit')}</label>
                    <input
                      id="rm-bw"
                      type="number"
                      min={0}
                      className="mono"
                      value={draft.options.bwlimit}
                      onChange={(e) =>
                        patch({ options: { ...draft.options, bwlimit: num(e.target.value) } })
                      }
                    />
                    <span className="help">{t('dashboard.routeModal.bwlimitHelp')}</span>
                  </div>
                  <div className="field">
                    <label htmlFor="rm-minfree">{t('dashboard.routeModal.minFree')}</label>
                    <input
                      id="rm-minfree"
                      type="number"
                      min={0}
                      max={100}
                      className="mono"
                      value={draft.options.min_free_percent}
                      onChange={(e) =>
                        patch({
                          options: { ...draft.options, min_free_percent: num(e.target.value) },
                        })
                      }
                    />
                    <span className="help">{t('dashboard.routeModal.minFreeHelp')}</span>
                  </div>
                </div>
              )}
              {kind === 'verify' && (
                <div className="adv-grid">
                  <div className="field">
                    <label htmlFor="rm-reverify">{t('dashboard.routeModal.reverify')}</label>
                    <input
                      id="rm-reverify"
                      type="number"
                      min={0}
                      className="mono"
                      value={draft.options.reverify_days}
                      onChange={(e) =>
                        patch({ options: { ...draft.options, reverify_days: num(e.target.value) } })
                      }
                    />
                    <span className="help">{t('dashboard.routeModal.reverifyHelp')}</span>
                  </div>
                </div>
              )}
              {(kind === 'backup' || kind === 'sync') && (
                <div className="adv-toggles">
                  <label className="tglrow">
                    <Toggle
                      size="sm"
                      on={draft.options.gc}
                      onClick={() => patch({ options: { ...draft.options, gc: !draft.options.gc } })}
                    />
                    <span>{t('dashboard.routeModal.gc')}</span>
                  </label>
                  <label className="tglrow">
                    <Toggle
                      size="sm"
                      on={draft.options.verify_after}
                      onClick={() =>
                        patch({
                          options: { ...draft.options, verify_after: !draft.options.verify_after },
                        })
                      }
                    />
                    <span>{t('dashboard.routeModal.verifyAfter')}</span>
                  </label>
                </div>
              )}
            </details>
          )}
        </div>
      </Modal>
      <ConfirmModal state={confirm} onCancel={() => setConfirm(null)} />
    </>
  )
}

/** An empty number input reads as 0 rather than NaN, which the backend would 422. */
function num(value: string): number {
  const n = Number.parseInt(value, 10)
  return Number.isFinite(n) && n >= 0 ? n : 0
}

function SegButton({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return (
    <button type="button" className={`seg-btn${on ? ' on' : ''}`} aria-pressed={on} onClick={onClick}>
      {label}
    </button>
  )
}

function SourceChip({
  id,
  kindLabel,
  on,
  disabled,
  title,
  onClick,
}: {
  id: string
  kindLabel: string
  on: boolean
  disabled?: boolean
  title?: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={`chip${on ? ' on' : ''}`}
      aria-disabled={disabled ? 'true' : undefined}
      aria-pressed={on}
      title={title}
      onClick={disabled ? undefined : onClick}
    >
      <span className="k">{kindLabel}</span>
      {id}
    </button>
  )
}
