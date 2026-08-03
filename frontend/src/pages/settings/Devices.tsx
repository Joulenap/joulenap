import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, api } from '../../api/client'
import type { PbsDevice, PveDevice, StatusResponse } from '../../api/types'
import { ConfirmModal, type ConfirmState } from '../../components/ConfirmModal'
import { Modal } from '../../components/Modal'
import { useConfig } from '../../config/ConfigContext'
import { useGuestsAll } from '../../hooks/useGuestsAll'
import {
  type DeviceKind,
  type DeviceState,
  deviceState,
  guardRoutes,
  pbsCardMeta,
  pveCardMeta,
  routesUsing,
} from '../../utils/deviceForm'
import { DeviceModal } from './DeviceModal'

// The mockup's two card glyphs, inline so they follow `currentColor` and need no asset.
const PveIcon = () => (
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <rect x="1.5" y="2" width="13" height="5" rx="1.2" />
    <rect x="1.5" y="9" width="13" height="5" rx="1.2" />
    <circle cx="4.2" cy="4.5" r="0.9" fill="currentColor" stroke="none" />
    <circle cx="4.2" cy="11.5" r="0.9" fill="currentColor" stroke="none" />
  </svg>
)

const PbsIcon = () => (
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <rect x="2" y="1.5" width="12" height="13" rx="1.5" />
    <path d="M5 4.5h6M5 7h6" />
    <circle cx="8" cy="11" r="1.4" />
  </svg>
)

/** A finished connection test. It replaces the polled status row and stays: the user asked
 *  the question, and an answer that vanishes on the next 5s tick is unreadable. Leaving the
 *  tab clears it with the component. */
interface TestResult {
  ok: boolean
  detail: string
}

interface GuardState {
  id: string
  routes: string[]
}

export function Devices({ status }: { status: StatusResponse | null }) {
  const { t } = useTranslation()
  const { config, reload } = useConfig()
  const { groups } = useGuestsAll(config?.pves ?? [])

  const [editing, setEditing] = useState<{ kind: DeviceKind; device: PveDevice | PbsDevice } | null>(null)
  const [adding, setAdding] = useState<DeviceKind | null>(null)
  const [guard, setGuard] = useState<GuardState | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [tests, setTests] = useState<Record<string, TestResult>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const ns = 'settings.devices'
  if (!config) return null

  const key = (kind: DeviceKind, id: string) => `${kind}:${id}`

  async function onTest(kind: DeviceKind, id: string) {
    setBusy(key(kind, id))
    setErr(null)
    try {
      const r = await api.testDevice(kind, id)
      setTests((s) => ({ ...s, [key(kind, id)]: { ok: true, detail: r.detail } }))
    } catch (e) {
      // A failure is a 502 carrying the reason, not a 200 with ok:false.
      setTests((s) => ({
        ...s,
        [key(kind, id)]: { ok: false, detail: e instanceof ApiError ? e.message : String(e) },
      }))
    } finally {
      setBusy(null)
    }
  }

  async function doDelete(kind: DeviceKind, id: string) {
    setBusy(key(kind, id))
    setErr(null)
    try {
      await api.deleteDevice(kind, id)
      await reload()
    } catch (e) {
      // The local check below is a shortcut, not the authority: if the config changed under
      // us the backend still refuses, and its payload names the routes.
      if (e instanceof ApiError && e.status === 409) {
        const routes = guardRoutes(e)
        if (routes.length) {
          setGuard({ id, routes })
          return
        }
      }
      setErr(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  function onRemove(kind: DeviceKind, device: PveDevice | PbsDevice) {
    const using = routesUsing(config!.routes, kind, device.id)
    if (using.length) {
      setGuard({ id: device.id, routes: using })
      return
    }
    setConfirm({
      title: t(`${ns}.removeTitle`, { id: device.id }),
      message: t(`${ns}.removeBody`),
      confirmLabel: t(`${ns}.remove`),
      danger: true,
      icon: '⨯',
      onConfirm: () => void doDelete(kind, device.id),
    })
  }

  const stateRow = (kind: DeviceKind, id: string, state: DeviceState) => {
    const result = tests[key(kind, id)]
    if (result) {
      return (
        <div className="dev-state">
          <span className={`dot ${result.ok ? 'on' : 'off'}`} />
          <span className={result.ok ? 'state-on' : 'err-note'}>{result.detail}</span>
        </div>
      )
    }
    return (
      <div className="dev-state">
        <span className={`dot ${state === 'connected' ? 'on' : 'off'}`} />
        <span className={state === 'connected' ? 'state-on' : 'state-off'}>
          {t(`${ns}.state.${state}`)}
        </span>
      </div>
    )
  }

  const actions = (kind: DeviceKind, device: PveDevice | PbsDevice) => (
    <div className="dev-actions">
      <button
        type="button"
        className="btn btn-sm"
        disabled={busy === key(kind, device.id)}
        onClick={() => void onTest(kind, device.id)}
      >
        {t(`${ns}.test`)}
      </button>
      <button type="button" className="btn btn-sm" onClick={() => setEditing({ kind, device })}>
        {t('common.edit')}
      </button>
      <span className="spacer" />
      <button
        type="button"
        className="btn btn-sm btn-danger-ghost"
        disabled={busy === key(kind, device.id)}
        onClick={() => onRemove(kind, device)}
      >
        {t(`${ns}.remove`)}
      </button>
    </div>
  )

  const pveCard = (device: PveDevice) => {
    const meta = pveCardMeta(device, groups.find((g) => g.pve === device.id))
    const online = status?.pves.find((p) => p.id === device.id)?.online
    return (
      <div className="dev-card" key={device.id}>
        <div className="dev-top">
          <span className="dev-ico">
            <PveIcon />
          </span>
          <span className="dev-name">{device.id}</span>
          <span className="dev-badge">{t(`${ns}.badge.${meta.badge}`)}</span>
        </div>
        <div className="dev-host mono">{meta.host}</div>
        <div className="dev-meta">
          {!meta.unknown && (
            <>
              {meta.badge === 'cluster' && `${t(`${ns}.nodes`, { count: meta.nodes })} · `}
              {t(`${ns}.guests`, { count: meta.guests })} ·{' '}
            </>
          )}
          {t(`${ns}.token`)} <span className="mono">{device.api_token_id || '—'}</span>
        </div>
        {stateRow('pves', device.id, deviceState(online))}
        {actions('pves', device)}
      </div>
    )
  }

  const pbsCard = (device: PbsDevice) => {
    const meta = pbsCardMeta(device, config.routes)
    const live = status?.pbss.find((p) => p.id === device.id)
    return (
      <div className="dev-card" key={device.id}>
        <div className="dev-top">
          <span className="dev-ico">
            <PbsIcon />
          </span>
          <span className="dev-name">{device.id}</span>
          <span className="dev-badge">{t(`${ns}.badge.${meta.badge}`)}</span>
        </div>
        <div className="dev-host mono">{meta.host}</div>
        <div className="dev-meta">
          {t(`${ns}.datastoreMeta`)} <span className="mono">{meta.datastore || '—'}</span> ·{' '}
          {t(`${ns}.usedByRoutes`, { count: meta.routes })}
        </div>
        {stateRow('pbss', device.id, deviceState(live?.online, device.managed_power))}
        {actions('pbss', device)}
      </div>
    )
  }

  const section = (kind: DeviceKind, title: string, count: number, body: React.ReactNode) => (
    <section className="panel">
      <div className="panel-hd">
        <h2>
          {title} <span style={{ color: 'var(--jn-text-muted)', fontWeight: 400 }}>({count})</span>
        </h2>
        <button type="button" className="btn btn-accent" onClick={() => setAdding(kind)}>
          {t(kind === 'pves' ? `${ns}.addPve` : `${ns}.addPbs`)}
        </button>
      </div>
      {count === 0 ? <div className="panel-empty">{t(`${ns}.empty`)}</div> : <div className="panel-bd dev-grid">{body}</div>}
    </section>
  )

  return (
    <div>
      {err && <div className="form-banner">{err}</div>}

      {section('pves', t(`${ns}.pveSection`), config.pves.length, config.pves.map(pveCard))}
      {section('pbss', t(`${ns}.pbsSection`), config.pbss.length, config.pbss.map(pbsCard))}

      {editing && (
        <DeviceModal
          kind={editing.kind}
          device={editing.device}
          onClose={() => setEditing(null)}
          onSaved={reload}
        />
      )}

      {/* The guided add flows are M12. Saying so beats a half-built form, and the YAML editor
          is a real answer in the meantime. */}
      {adding && (
        <Modal
          title={t(adding === 'pves' ? `${ns}.addPve` : `${ns}.addPbs`)}
          onClose={() => setAdding(null)}
          footer={
            <button type="button" className="btn btn-accent" onClick={() => setAdding(null)}>
              {t(`${ns}.gotIt`)}
            </button>
          }
        >
          <div className="modal-bd">
            <p className="modal-note">{t(`${ns}.addSoon`)}</p>
          </div>
        </Modal>
      )}

      {guard && (
        <Modal
          title={t(`${ns}.guardTitle`)}
          onClose={() => setGuard(null)}
          footer={
            <button type="button" className="btn btn-accent" onClick={() => setGuard(null)}>
              {t(`${ns}.gotIt`)}
            </button>
          }
        >
          <div className="modal-bd">
            <p className="modal-note">
              {t(`${ns}.guardBody`, { id: guard.id, count: guard.routes.length })}
            </p>
            <div className="yaml">{guard.routes.join('\n')}</div>
          </div>
        </Modal>
      )}

      <ConfirmModal state={confirm} onCancel={() => setConfirm(null)} />
    </div>
  )
}
