import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, api } from '../api/client'
import { ConfirmModal, type ConfirmState } from '../components/ConfirmModal'
import type { PveConnectResult, WizardStorage } from '../api/types'
import { useConfig } from '../config/ConfigContext'
import type { DeviceError } from '../utils/deviceForm'
import {
  defaultDeviceId,
  freshPbsDraft,
  freshPveDraft,
  linkedStorages,
  matchStorage,
  newStorages,
  nextStep,
  pbsDeviceFrom,
  pveDeviceFrom,
  validateConnectStep,
  validateFinalDevice,
} from '../utils/wizardFlow'
import { WizardShell } from './WizardShell'
import { tokenConflictMessage } from './tokenConflict'
import { CredentialFields, ErrorText, NumField, TextField } from './fields'
import { ManagedPowerToggle, SshKeyFields, WakeFields, usePbsSetup } from './pbsSetup'

/**
 * Flow A — Add a Proxmox VE, and optionally the PBS it already backs up to
 * (`design/overhaul/src/wiz2.html`). Also the first-run flow.
 *
 * Connection → PBS discovery from the PVE's own storage config → configure that PBS (skipped
 * when nothing is toggled) → finish. **No route is created** — that is the user's job in the
 * route editor, and the last step says so.
 *
 * State is local and dies with the component, which is what discards the two root passwords it
 * may collect (the PVE's and the PBS's — different machines, so different credentials).
 */
export function AddPveWizard({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const { config, reload } = useConfig()
  const pveIds = (config?.pves ?? []).map((p) => p.id)
  const pbsIds = (config?.pbss ?? []).map((p) => p.id)
  const registered = config?.pbss ?? []

  const [step, setStep] = useState(0)
  const [pve, setPve] = useState(() => freshPveDraft(pveIds))
  const [pbs, setPbs] = useState(() => freshPbsDraft(pbsIds))
  const [connected, setConnected] = useState<PveConnectResult | null>(null)
  const [token, setToken] = useState<{ id: string; secret: string } | null>(null)
  /** Which discovered storage the user chose to configure now — at most one; the rest are a
   *  trip through Settings → Devices → + Add, which is a whole flow of its own. */
  const [chosen, setChosen] = useState<WizardStorage | null>(null)
  const [errors, setErrors] = useState<DeviceError[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const [report, setReport] = useState<string[]>([])
  const [saved, setSaved] = useState(false)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  // The user's answer to "that token name is taken", for both boxes this flow provisions.
  // A ref, not state: the provisioning calls read it in the same tick the confirm sets it.
  const replaceToken = useRef(false)
  // What already exists on the far side, so a retry after a partial failure resumes instead
  // of repeating. A ref, not state: `save` reads it in the same tick it writes it.
  const landed = useRef<{ pbsToken: { id: string; secret: string } | null; pbsId: string | null }>({
    pbsToken: null,
    pbsId: null,
  })

  const setup = usePbsSetup(pbs, setPbs)
  const { loadKey } = setup
  const errorFor = (field: string) => (errors ?? []).find((e) => e.field === field)

  const storages = connected?.storages ?? []
  const linked = linkedStorages(storages, registered)
  const fresh = newStorages(storages, registered)
  const skipped = chosen ? [] : [2]

  useEffect(() => {
    if (step === 2) void loadKey()
    // `loadKey`, not the whole `setup`: usePbsSetup returns a fresh object literal on every
    // render, so depending on it re-ran this effect on every render. Harmless while the
    // request is cached — but a *failed* keygen clears that cache so the user can retry, and
    // the retry then fired from the next render instead of from a click. The result was an
    // unbounded POST loop behind the error banner, on exactly the failures that happen in
    // the field: a read-only data dir, a full volume, no ssh-keygen.
  }, [step, loadKey])

  /** Step 1 → 2: connect, and in root mode mint the scoped PVE token on the way. */
  async function connect() {
    const result = await api.wizardPveConnect({
      host: pve.host.trim(),
      port: pve.port,
      mode: pve.cred,
      api_token_id: pve.tokenId.trim(),
      api_token_secret: pve.tokenSecret,
      username: pve.user,
      password: pve.password,
      replace_token: replaceToken.current,
    })
    setConnected(result)
    setToken(
      result.token ?? { id: pve.tokenId.trim(), secret: pve.tokenSecret },
    )
    // Pre-select the single new storage, as the mockup's "Configure now" toggle is drawn on.
    const first = newStorages(result.storages, registered)[0] ?? null
    setChosen(first)
    if (first) seedPbsFrom(first)
  }

  /** Fill the PBS draft from a discovered storage: everything but its credentials is known,
   *  including the fingerprint PVE already pinned. */
  function seedPbsFrom(storage: WizardStorage) {
    setPbs((d) => ({
      ...d,
      id: defaultDeviceId('pbs', pbsIds, storage.storage),
      host: storage.host,
      port: storage.port,
      datastore: storage.datastore,
      fingerprint: storage.fingerprint,
    }))
  }

  function toggleStorage(storage: WizardStorage) {
    if (chosen?.storage === storage.storage) {
      setChosen(null)
      return
    }
    setChosen(storage)
    seedPbsFrom(storage)
  }

  /**
   * Step 3 → 4: mint the PBS token and install the key while the root password is still here.
   *
   * Returns the credentials the device will be saved with rather than parking them in the
   * draft: `save` runs in this same tick, so a `setPbs` here would not have landed and the
   * device would be created with an empty token — a 200 OK that quietly stores nothing usable.
   */
  async function provisionPbs(): Promise<{ id: string; secret: string } | null> {
    const problems = validateConnectStep(pbs, pbsIds, registered)
    if (problems.length) {
      setErrors(problems)
      return null
    }
    // A retry after a half-failed save must not mint a second token on the PBS: this step is
    // reached again whenever the *PVE* create was the thing that failed, and each pass would
    // otherwise leave another orphan `joulenap` token behind on the box.
    if (landed.current.pbsToken) return landed.current.pbsToken
    let token = { id: pbs.tokenId.trim(), secret: pbs.tokenSecret }
    if (pbs.cred === 'root') {
      token = await api.wizardPbsProvision({
        host: pbs.host.trim(),
        port: pbs.port,
        username: pbs.user,
        password: pbs.password,
        datastore: pbs.datastore.trim(),
        fingerprint: pbs.fingerprint.trim(),
        replace_token: replaceToken.current,
      })
    }
    if (pbs.managedPower && pbs.autoInstall) {
      if (pbs.cred !== 'root') {
        setErrors([{ key: 'wizard.err.installNeedsRoot' }])
        return null
      }
      if (!setup.hostTrusted) {
        setErrors([{ key: 'wizard.err.hostKeyNotTrusted' }])
        return null
      }
      const key = await setup.loadKey()
      if (!key) return null
      await api.wizardSshInstall({
        host: pbs.host.trim(),
        user: pbs.sshUser || 'root',
        password: pbs.password,
        public_key: key.public_key,
      })
    }
    landed.current.pbsToken = token
    return token
  }

  /**
   * Entering the last step: create the PBS first (if one was configured), then the PVE with the
   * storages map that points at it. That order matters — the map has to name a device that
   * already exists for the config to make sense on the very next read.
   */
  async function save(pbsToken: { id: string; secret: string } | null) {
    const lines: string[] = []
    const map = { ...linked }

    if (chosen && pbsToken) {
      const device = pbsDeviceFrom(pbs, pbsToken)
      const problems = validateFinalDevice(device, pbsIds)
      if (problems.length) {
        setErrors(problems)
        setStep(2)
        return
      }
      // Skip the half that already landed. The PBS is created before the PVE (the storages
      // map has to name a device that exists), so a PVE failure used to strand the pair: the
      // retry re-posted the same PBS and got a 409 "already exists" that said nothing about
      // half the work having succeeded, and the only escape was to cancel and start over.
      if (landed.current.pbsId === null) {
        await api.createDevice('pbss', device as unknown as Record<string, unknown>)
        landed.current.pbsId = device.id
      }
      map[device.id] = chosen.storage
      lines.push(t('wizard.report.pbsConfigured', { id: device.id }))
    }

    const device = pveDeviceFrom(pve, token ?? { id: '', secret: '' }, map)
    const problems = validateFinalDevice(device, pveIds)
    if (problems.length) {
      setErrors(problems)
      setStep(0)
      return
    }
    await api.createDevice('pves', device as unknown as Record<string, unknown>)
    lines.unshift(t('wizard.report.pveApi', { id: device.id }))
    if (Object.keys(map).length) {
      lines.push(t('wizard.report.linked', { list: Object.keys(map).join(', ') }))
    }
    await reload()
    setSaved(true)
    setReport(lines)
  }

  async function advance(dir: 1 | -1) {
    setFailed(null)
    if (dir === 1) {
      if (step === 0) {
        const problems = validateConnectStep(pve, pveIds, config?.pves ?? [])
        if (problems.length) {
          setErrors(problems)
          return
        }
      }
      if (step === 3) {
        onClose()
        return
      }
    }
    const target = nextStep('pve', step, dir, { noPbsToConfigure: !chosen })
    if (target === -1) {
      onClose()
      return
    }

    setErrors(null)
    setBusy(true)
    try {
      if (dir === 1 && step === 0) {
        // The discovery step is built from the response, so it decides its own target.
        await connect()
        setStep(1)
        return
      }
      let pbsToken: { id: string; secret: string } | null = null
      if (dir === 1 && step === 2) {
        pbsToken = await provisionPbs()
        if (!pbsToken) return
      }
      // `!saved` cannot mask a changed decision: Back is disabled once the devices exist
      // (`done` below), so there is exactly one way into the last step. It is here only so a
      // re-render mid-flight can never post the same device twice.
      if (dir === 1 && target === 3 && !saved) await save(pbsToken)
      setStep(target)
    } catch (e) {
      // 409 = the token name is taken on whichever box we were provisioning. Only the user
      // can say whether replacing it — and breaking whatever else holds that secret, very
      // likely this PVE's own PBS storage entry — is what they want.
      if (e instanceof ApiError && e.status === 409) {
        const host = (step === 0 ? pve.host : pbs.host).trim()
        const message = await tokenConflictMessage(host, t, config?.pves ?? [], registered)
        setConfirm({
          title: t('wizard.tokenExists.title'),
          message,
          confirmLabel: t('wizard.tokenExists.confirm'),
          danger: true,
          icon: '⚠',
          onConfirm: () => {
            setConfirm(null)
            replaceToken.current = true
            void advance(1)
          },
        })
        return
      }
      setFailed(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const banners = (errors ?? []).filter((e) => !e.field)
  const nodes = connected?.nodes ?? []

  return (
    <WizardShell
      flow="pve"
      title={t('wizard.pve.title')}
      step={step}
      skipped={skipped}
      busy={busy}
      done={saved}
      onBack={() => void advance(-1)}
      onNext={() => void advance(1)}
      onClose={onClose}
    >
      <div className="modal-bd">
        {failed && <div className="form-banner">{failed}</div>}
        {setup.error && <div className="form-banner">{setup.error}</div>}
        {banners.map((e, i) => (
          <div className="form-banner" key={i}>
            <ErrorText error={e} />
          </div>
        ))}

        {step === 0 && (
          <>
            <div className="frow">
              <TextField
                id="wiz-pve-id"
                label={t('wizard.field.name')}
                value={pve.id}
                onChange={(v) => setPve({ ...pve, id: v })}
                error={errorFor('id')}
                help={t('wizard.field.nameHelp')}
              />
              <TextField
                id="wiz-pve-host"
                label={t('settings.devices.host')}
                value={pve.host}
                onChange={(v) => setPve({ ...pve, host: v })}
                error={errorFor('host')}
              />
              <NumField
                id="wiz-pve-port"
                label={t('settings.devices.port')}
                value={pve.port}
                onChange={(n) => setPve({ ...pve, port: n })}
                error={errorFor('port')}
              />
            </div>
            <CredentialFields
              scope="pve"
              label={t('wizard.cred.pveLabel')}
              help={t('wizard.cred.pveHelp')}
              tokenPlaceholder="joulenap@pve!routes"
              draft={pve}
              patch={(next) => setPve({ ...pve, ...next })}
              errorFor={errorFor}
            />
          </>
        )}

        {step === 1 && (
          <>
            <div className="okline">
              ✓ {t('wizard.pve.connected')}
              {connected?.token && (
                <span className="mono">· {t('wizard.pve.tokenCreated', { id: connected.token.id })}</span>
              )}
            </div>
            <div className="detected">
              <span className="t">{pve.id}</span>
              {/* No guest count: /wizard/pve/connect returns nodes, and /api/guests reads from
                  the saved config — this device does not exist yet. */}
              <span className="m">
                {t(nodes.length > 1 ? 'wizard.pve.cluster' : 'wizard.pve.standalone', {
                  count: nodes.length,
                  nodes: nodes.map((n) => n.node).join(', '),
                })}
              </span>
            </div>

            <div className="field">
              <span className="lab">{t('wizard.pve.storagesFound')}</span>
              {storages.length === 0 && <p className="modal-note">{t('wizard.pve.noStorages')}</p>}
              {storages.map((s) => {
                const match = matchStorage(s, registered)
                return (
                  <div className="found" key={s.storage}>
                    <div className="fi">
                      <div className="t">
                        {match ? match.id : s.storage}{' '}
                        <span className="mono">
                          {s.host} · {t('wizard.pve.datastore', { datastore: s.datastore })}
                        </span>
                      </div>
                      <div className="m">
                        {match ? t('wizard.pve.alreadyRegistered') : t('wizard.pve.newStorage')}
                      </div>
                    </div>
                    {match ? (
                      <span className="linked">✓ {t('wizard.pve.willLink')}</span>
                    ) : (
                      <label className="tglrow">
                        <input
                          type="checkbox"
                          checked={chosen?.storage === s.storage}
                          onChange={() => toggleStorage(s)}
                        />
                        <span>{t('wizard.pve.configureNow')}</span>
                      </label>
                    )}
                  </div>
                )
              })}
              <span className="help">
                {fresh.length > 1 ? t('wizard.pve.oneAtATime') : t('wizard.pve.storagesHelp')}
              </span>
            </div>
          </>
        )}

        {step === 2 && chosen && (
          <>
            <div className="okline">
              ✓ {t('wizard.pve.pbsReachable', { host: pbs.host })}
            </div>
            <div className="frow">
              <TextField
                id="wiz-a-pbs-id"
                label={t('wizard.field.name')}
                value={pbs.id}
                onChange={(v) => setPbs({ ...pbs, id: v })}
                error={errorFor('id')}
                help={t('wizard.field.nameHelp')}
              />
              <TextField
                id="wiz-a-pbs-datastore"
                label={t('settings.devices.datastore')}
                value={pbs.datastore}
                onChange={(v) => setPbs({ ...pbs, datastore: v })}
                error={errorFor('datastore')}
              />
            </div>
            <CredentialFields
              scope="a-pbs"
              label={t('wizard.cred.pbsLabel')}
              help={t('wizard.cred.pbsInPveHelp')}
              tokenPlaceholder="joulenap@pbs!routes"
              draft={pbs}
              patch={(next) => setPbs({ ...pbs, ...next })}
              errorFor={errorFor}
            />
            <ManagedPowerToggle setup={setup} />
            {pbs.managedPower && (
              <>
                <WakeFields setup={setup} errorFor={errorFor} />
                <SshKeyFields setup={setup} />
              </>
            )}
          </>
        )}

        {step === 3 && (
          <>
            {report.map((line) => (
              <div className="testline" key={line}>
                <span className="ok">✓</span>
                <span>{line}</span>
              </div>
            ))}
            {saved && (
              <div className="detected" style={{ marginTop: 4 }}>
                <span className="t">{t('wizard.pve.readyTitle')}</span>
                {/* The wizard creates no route, deliberately (NOTES). This line is where the
                    user is told what to do next. */}
                <span className="m">{t('wizard.pve.readyBody')}</span>
              </div>
            )}
          </>
        )}
      </div>
      <ConfirmModal state={confirm} onCancel={() => setConfirm(null)} />
    </WizardShell>
  )
}
