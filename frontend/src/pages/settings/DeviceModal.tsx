import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, api } from '../../api/client'
import type { PbsDevice, PveDevice } from '../../api/types'
import { Modal } from '../../components/Modal'
import { Toggle } from '../../components/Toggle'
import {
  type DeviceError,
  type DeviceKind,
  deviceSaveErrors,
  validateDevice,
} from '../../utils/deviceForm'

interface DeviceModalProps {
  kind: DeviceKind
  /** The device being edited. This modal never creates — that is the wizard's job (M12). */
  device: PveDevice | PbsDevice
  onClose: () => void
  onSaved: () => Promise<void> | void
}

/**
 * The mockup's "Edit PBS · <id>" dialog, on the shared `Modal` shell so it inherits Escape,
 * the Tab trap and focus restoration from `useDialogKeys` (M10).
 *
 * The draft *is* the device: a `PveDevice`/`PbsDevice` clone edited in place and PUT back, so
 * there is no separate draft shape to keep in sync with the API type. Secrets arrive as
 * `***REDACTED***` and are echoed back untouched to keep the stored value — clearing a field
 * is how you clear the secret, which is why the help line says so.
 */
export function DeviceModal({ kind, device, onClose, onSaved }: DeviceModalProps) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState(() => structuredClone(device))
  const [busy, setBusy] = useState(false)
  // Errors only after the first Save attempt: complaining about a form the user has not tried
  // to submit is noise (same rule as the route editor).
  const [errors, setErrors] = useState<DeviceError[] | null>(null)

  const isPbs = kind === 'pbss'
  const pbs = draft as PbsDevice
  const pve = draft as PveDevice
  const ns = 'settings.devices'

  // Sync grants: root credentials that are used once and never stored, so they live outside
  // the draft entirely (see the section's own comment below).
  const [grantOpen, setGrantOpen] = useState(false)
  const [grantUser, setGrantUser] = useState('root@pam')
  const [grantPass, setGrantPass] = useState('')
  const [grantBusy, setGrantBusy] = useState(false)
  const [grantNote, setGrantNote] = useState<{ ok: boolean; text: string } | null>(null)

  const live = useMemo(() => validateDevice(draft), [draft])
  const shown = errors ?? []
  const errorFor = (field: string) => shown.find((e) => e.field === field)
  const banners = shown.filter((e) => !e.field)

  function patch(next: Partial<PveDevice & PbsDevice>) {
    setDraft((d) => ({ ...d, ...next }) as PveDevice | PbsDevice)
    // Server verdicts were about the body that was sent; the next edit invalidates them.
    setErrors((e) => (e === null ? null : validateDevice({ ...draft, ...next } as PbsDevice)))
  }

  async function onSave() {
    if (live.length) {
      setErrors(live)
      return
    }
    setBusy(true)
    try {
      await api.updateDevice(kind, device.id, draft as unknown as Record<string, unknown>)
      await onSaved()
      onClose()
    } catch (e) {
      setErrors(e instanceof ApiError ? deviceSaveErrors(e) : [{ message: String(e) }])
    } finally {
      setBusy(false)
    }
  }

  async function onGrantSync() {
    setGrantBusy(true)
    setGrantNote(null)
    try {
      const out = await api.wizardPbsGrantSync({
        host: draft.host,
        port: draft.port,
        username: grantUser,
        password: grantPass,
        api_token_id: draft.api_token_id,
        fingerprint: pbs.fingerprint,
      })
      setGrantPass('') // used once, then gone — same contract as the wizard's root step
      setGrantNote({ ok: true, text: t(`${ns}.syncGrantOk`, { roles: out.roles.join(', ') }) })
    } catch (e) {
      setGrantNote({ ok: false, text: e instanceof ApiError ? e.message : String(e) })
    } finally {
      setGrantBusy(false)
    }
  }

  const text = (
    field: string,
    label: string,
    value: string,
    onChange: (v: string) => void,
    opts: { type?: string; mono?: boolean; placeholder?: string; help?: string } = {},
  ) => {
    const err = errorFor(field)
    return (
      <div className={`field${err ? ' err' : ''}`} key={field}>
        <label htmlFor={`dev-${field}`}>{label}</label>
        <input
          id={`dev-${field}`}
          type={opts.type ?? 'text'}
          className={opts.mono === false ? undefined : 'in-mono'}
          value={value}
          placeholder={opts.placeholder}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => onChange(e.target.value)}
        />
        {err && <span className="err-note">{err.message ?? t(err.key!, err.params)}</span>}
        {!err && opts.help && <span className="help">{opts.help}</span>}
      </div>
    )
  }

  const num = (
    field: string,
    label: string,
    value: number,
    onChange: (n: number) => void,
    opts: { min?: number; help?: string } = {},
  ) => {
    const err = errorFor(field)
    const min = opts.min ?? 0
    return (
      <div className={`field${err ? ' err' : ''}`} key={field}>
        <label htmlFor={`dev-${field}`}>{label}</label>
        <input
          id={`dev-${field}`}
          type="number"
          className="in-mono"
          min={min}
          value={String(value)}
          onChange={(e) => onChange(Math.max(min, Math.floor(Number(e.target.value)) || 0))}
        />
        {err && <span className="err-note">{err.message ?? t(err.key!, err.params)}</span>}
        {!err && opts.help && <span className="help">{opts.help}</span>}
      </div>
    )
  }

  const secretHelp = t(`${ns}.secretHelp`)

  return (
    <Modal
      size="lg"
      title={t(`${ns}.editTitle`, {
        kind: isPbs ? t(`${ns}.pbs`) : t(`${ns}.pve`),
        id: device.id,
      })}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button
            type="button"
            className="btn btn-accent"
            disabled={busy}
            onClick={() => void onSave()}
          >
            {t('common.save')}
          </button>
        </>
      }
    >
      <div className="modal-bd">
        {banners.map((e, i) => (
          <div className="form-banner" key={i}>
            {e.message ?? t(e.key!, e.params)}
          </div>
        ))}

        <span className="msec">{t(`${ns}.secConnection`)}</span>
        <div className="frow">
          {text('host', t(`${ns}.host`), draft.host, (v) => patch({ host: v }))}
          {num('port', t(`${ns}.port`), draft.port, (n) => patch({ port: n }), { min: 1 })}
          {isPbs && text('datastore', t(`${ns}.datastore`), pbs.datastore, (v) => patch({ datastore: v }))}
          {isPbs &&
            text('fingerprint', t(`${ns}.fingerprint`), pbs.fingerprint, (v) => patch({ fingerprint: v }), {
              placeholder: 'aa:bb:cc:…',
            })}
          {text('api_token_id', t(`${ns}.tokenId`), draft.api_token_id, (v) => patch({ api_token_id: v }))}
          {text(
            'api_token_secret',
            t(`${ns}.tokenSecret`),
            draft.api_token_secret,
            (v) => patch({ api_token_secret: v }),
            { type: 'password', help: secretHelp },
          )}
        </div>

        {!isPbs && (
          <>
            <label className="tglrow">
              <Toggle on={pve.verify_tls} onClick={() => patch({ verify_tls: !pve.verify_tls })} size="sm" />
              <span>{t(`${ns}.verifyTls`)}</span>
            </label>

            <span className="msec">{t(`${ns}.secStorages`)}</span>
            {/* Read-only: which PVE storage points at which PBS is discovered from the PVE's
                own storage config, so editing it here would only desynchronise the two.
                The wizard (M12) is what fills it in. */}
            {Object.keys(pve.storages).length === 0 ? (
              <p className="modal-note">{t(`${ns}.storagesEmpty`)}</p>
            ) : (
              <div className="yaml">
                {Object.entries(pve.storages)
                  .map(([pbsId, storage]) => `${pbsId} → ${storage}`)
                  .join('\n')}
              </div>
            )}
            <span className="help">{t(`${ns}.storagesHint`)}</span>
          </>
        )}

        {isPbs && (
          <>
            <label className="tglrow">
              <Toggle
                on={pbs.managed_power}
                onClick={() => patch({ managed_power: !pbs.managed_power })}
                size="sm"
              />
              <span>{t(`${ns}.managedPower`)}</span>
            </label>
            <span className="help">{t(`${ns}.managedPowerHint`)}</span>

            {/* Sync grants. A token cannot ACL itself — PBS answers "Unprivileged API tokens
                can't set ACL items" to any token — so the only way to add the /remote roles a
                Sync route needs is a root login. The password is sent once and never stored,
                which is why it lives outside the draft and is wiped on success. */}
            <span className="msec">{t(`${ns}.secSync`)}</span>
            {!grantOpen ? (
              <>
                <button type="button" className="btn btn-sm" onClick={() => setGrantOpen(true)}>
                  {t(`${ns}.syncGrant`)}
                </button>
                <span className="help">{t(`${ns}.syncGrantHint`)}</span>
              </>
            ) : (
              <>
                <div className="frow">
                  <div className="field">
                    <label htmlFor="dev-grant-user">{t('wizard.field.user')}</label>
                    <input
                      id="dev-grant-user"
                      className="in-mono"
                      value={grantUser}
                      autoComplete="off"
                      spellCheck={false}
                      onChange={(e) => setGrantUser(e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="dev-grant-pass">{t('wizard.field.password')}</label>
                    <input
                      id="dev-grant-pass"
                      type="password"
                      className="in-mono"
                      value={grantPass}
                      autoComplete="off"
                      onChange={(e) => setGrantPass(e.target.value)}
                    />
                  </div>
                </div>
                <div className="inline-row">
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={grantBusy || !grantPass || !draft.api_token_id}
                    onClick={() => void onGrantSync()}
                  >
                    {t(`${ns}.syncGrantRun`)}
                  </button>
                  {grantNote && (
                    <span className={grantNote.ok ? 'ok-note' : 'err-note'}>{grantNote.text}</span>
                  )}
                </div>
                <span className="help">{t(`${ns}.syncGrantCreds`)}</span>
              </>
            )}

            {/* The three power sections describe how to wake and sleep the box; with managed
                power off Joulenap does neither, so they are omitted rather than shown inert. */}
            {pbs.managed_power && (
              <>
                <span className="msec">{t(`${ns}.secWake`)}</span>
                <div className="frow3">
                  {text('mac', t(`${ns}.mac`), pbs.mac, (v) => patch({ mac: v }), {
                    placeholder: '00:11:22:33:44:55',
                  })}
                  {text(
                    'wol_broadcast_iface',
                    t(`${ns}.wolIface`),
                    pbs.wol_broadcast_iface,
                    (v) => patch({ wol_broadcast_iface: v }),
                    { placeholder: t(`${ns}.auto`) },
                  )}
                  {num('wait_timeout', t(`${ns}.waitTimeout`), pbs.wait_timeout, (n) =>
                    patch({ wait_timeout: n }),
                  )}
                  {num('wol_retries', t(`${ns}.wolRetries`), pbs.wol_retries, (n) =>
                    patch({ wol_retries: n }),
                  )}
                </div>

                <span className="msec">{t(`${ns}.secPower`)}</span>
                <div className="frow3">
                  {text('ssh_user', t(`${ns}.sshUser`), pbs.ssh_user, (v) => patch({ ssh_user: v }))}
                  {text('ssh_key_path', t(`${ns}.sshKey`), pbs.ssh_key_path, (v) =>
                    patch({ ssh_key_path: v }),
                  )}
                  {num(
                    'poweroff_task_wait',
                    t(`${ns}.poweroffWait`),
                    pbs.poweroff_task_wait,
                    (n) => patch({ poweroff_task_wait: n }),
                    { help: t(`${ns}.poweroffWaitHint`) },
                  )}
                </div>

                <span className="msec">{t(`${ns}.secExternal`)}</span>
                <div className="frow">
                  {num(
                    'first_task_wait',
                    t(`${ns}.firstTaskWait`),
                    pbs.external.first_task_wait,
                    (n) => patch({ external: { ...pbs.external, first_task_wait: n } }),
                    { help: t(`${ns}.firstTaskWaitHint`) },
                  )}
                  {num(
                    'idle_wait',
                    t(`${ns}.idleWait`),
                    pbs.external.idle_wait,
                    (n) => patch({ external: { ...pbs.external, idle_wait: n } }),
                    { help: t(`${ns}.idleWaitHint`) },
                  )}
                </div>
              </>
            )}
          </>
        )}
      </div>
    </Modal>
  )
}
