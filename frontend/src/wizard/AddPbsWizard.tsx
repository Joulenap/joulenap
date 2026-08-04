import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, api } from '../api/client'
import { useConfig } from '../config/ConfigContext'
import type { DeviceError } from '../utils/deviceForm'
import {
  freshPbsDraft,
  isOrphanPbs,
  nextStep,
  pbsDeviceFrom,
  validateConnectStep,
  validateFinalDevice,
} from '../utils/wizardFlow'
import { WizardShell } from './WizardShell'
import { CredentialFields, ErrorText, NumField, TextField } from './fields'
import { ManagedPowerToggle, SshKeyFields, WakeFields, usePbsSetup } from './pbsSetup'

/**
 * Flow B — Add a Proxmox Backup Server on its own (`design/overhaul/src/wiz2.html`).
 *
 * Connection → wake-up → power-off → verification, with the two power steps skipped entirely
 * when Joulenap does not manage the box (an always-on VM or a cloud-hosted PBS).
 *
 * All state is local and dies with the component, which is how the root password and the token
 * secret it collects are discarded — the same guarantee the 0.9 `WizardProvider` gave, without
 * the provider, because a modal cannot be navigated away from.
 */
export function AddPbsWizard({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const { config, reload } = useConfig()
  const existing = (config?.pbss ?? []).map((p) => p.id)

  const [step, setStep] = useState(0)
  const [draft, setDraft] = useState(() => freshPbsDraft(existing))
  const [errors, setErrors] = useState<DeviceError[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const [token, setToken] = useState<{ id: string; secret: string } | null>(null)
  const [report, setReport] = useState<string[]>([])
  const [saved, setSaved] = useState(false)

  const setup = usePbsSetup(draft, setDraft)
  const errorFor = (field: string) => (errors ?? []).find((e) => e.field === field)
  const skipped = draft.managedPower ? [] : [1, 2]

  // The key is what step 3 is about, so fetch it as the step opens rather than behind a button
  // the user has no reason not to press.
  useEffect(() => {
    if (step === 2) void setup.loadKey()
  }, [step, setup])

  /** Step 1: reach the box, pin its fingerprint, and mint a token if we were given root. */
  async function connect(): Promise<boolean> {
    const check = await api.wizardPbsCheck(draft.host.trim(), draft.port)
    if (!check.reachable) {
      setErrors([{ key: 'wizard.err.pbsUnreachable' }])
      return false
    }
    // A fingerprint the user typed wins: they may be pinning one from the PVE storage config.
    const fingerprint = draft.fingerprint.trim() || check.fingerprint || ''
    if (draft.cred === 'root') {
      const minted = await api.wizardPbsProvision({
        host: draft.host.trim(),
        port: draft.port,
        username: draft.user,
        password: draft.password,
        datastore: draft.datastore.trim(),
        fingerprint,
      })
      setToken(minted)
    } else {
      setToken({ id: draft.tokenId.trim(), secret: draft.tokenSecret })
    }
    setDraft({ ...draft, fingerprint })
    return true
  }

  /** Leaving the power-off step: install the key while we still hold the root password. */
  async function installKey(): Promise<boolean> {
    if (!draft.managedPower || !draft.autoInstall) return true
    if (draft.cred !== 'root') {
      setErrors([{ key: 'wizard.err.installNeedsRoot' }])
      return false
    }
    if (!setup.hostTrusted) {
      setErrors([{ key: 'wizard.err.hostKeyNotTrusted' }])
      return false
    }
    const key = await setup.loadKey()
    if (!key) return false
    await api.wizardSshInstall({
      host: draft.host.trim(),
      user: draft.sshUser || 'root',
      password: draft.password,
      public_key: key.public_key,
    })
    return true
  }

  /** Entering the last step: create the device and report what happened. */
  async function save() {
    const device = pbsDeviceFrom(draft, token ?? { id: '', secret: '' })
    const problems = validateFinalDevice(device, existing)
    if (problems.length) {
      setErrors(problems)
      setStep(0)
      return
    }
    await api.createDevice('pbss', device as unknown as Record<string, unknown>)
    await reload()
    setSaved(true)
    setReport([
      t('wizard.report.pbsApi', { datastore: device.datastore }),
      ...(device.managed_power
        ? [
            t('wizard.report.wake', { mac: device.mac }),
            draft.autoInstall ? t('wizard.report.sshInstalled') : t('wizard.report.sshManual'),
          ]
        : [t('wizard.report.unmanaged')]),
    ])
  }

  async function advance(dir: 1 | -1) {
    setFailed(null)
    if (dir === 1) {
      if (step === 0) {
        const problems = validateConnectStep(draft, existing)
        if (problems.length) {
          setErrors(problems)
          return
        }
      }
      // The last step's button just closes: the write already happened on the way in.
      if (step === 3) {
        onClose()
        return
      }
    }
    const target = nextStep('pbs', step, dir, { unmanagedPbs: !draft.managedPower })
    if (target === -1) {
      onClose()
      return
    }

    setErrors(null)
    setBusy(true)
    try {
      if (dir === 1 && step === 0 && !(await connect())) return
      if (dir === 1 && step === 2 && !(await installKey())) return
      // See AddPveWizard: Back is disabled once the device exists, so this guard only stops a
      // re-render mid-flight from posting twice — it can never swallow a changed decision.
      if (dir === 1 && target === 3 && !saved) await save()
      setStep(target)
    } catch (e) {
      setFailed(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const banners = (errors ?? []).filter((e) => !e.field)

  return (
    <WizardShell
      flow="pbs"
      title={t('wizard.pbs.title')}
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
                id="wiz-pbs-id"
                label={t('wizard.field.name')}
                value={draft.id}
                onChange={(v) => setDraft({ ...draft, id: v })}
                error={errorFor('id')}
                help={t('wizard.field.nameHelp')}
              />
              <TextField
                id="wiz-pbs-host"
                label={t('settings.devices.host')}
                value={draft.host}
                onChange={(v) => setup.patch({ host: v })}
                error={errorFor('host')}
              />
              <NumField
                id="wiz-pbs-port"
                label={t('settings.devices.port')}
                value={draft.port}
                onChange={(n) => setDraft({ ...draft, port: n })}
                error={errorFor('port')}
              />
              <TextField
                id="wiz-pbs-datastore"
                label={t('settings.devices.datastore')}
                value={draft.datastore}
                onChange={(v) => setDraft({ ...draft, datastore: v })}
                error={errorFor('datastore')}
              />
              <TextField
                id="wiz-pbs-fingerprint"
                label={t('settings.devices.fingerprint')}
                value={draft.fingerprint}
                onChange={(v) => setDraft({ ...draft, fingerprint: v })}
                placeholder={t('wizard.field.fingerprintPlaceholder')}
                help={t('wizard.cred.fingerprintPinned')}
              />
            </div>
            <CredentialFields
              scope="pbs"
              label={t('wizard.cred.pbsLabel')}
              help={t('wizard.cred.pbsHelp')}
              tokenPlaceholder="joulenap@pbs!routes"
              draft={draft}
              patch={(next) => setDraft({ ...draft, ...next })}
              errorFor={errorFor}
            />
            <ManagedPowerToggle setup={setup} />
          </>
        )}

        {step === 1 && (
          <>
            <div className="okline">
              ✓ {t('wizard.pbs.connected')}
              {token && <span className="mono">· {token.id}</span>}
            </div>
            <WakeFields setup={setup} errorFor={errorFor} />
          </>
        )}

        {step === 2 && <SshKeyFields setup={setup} />}

        {step === 3 && (
          <>
            {report.map((line) => (
              <div className="testline" key={line}>
                <span className="ok">✓</span>
                <span>{line}</span>
              </div>
            ))}
            {/* Only flow A's discovery ever fills pves[].storages, so today this fires for
                every PBS added here — which is exactly the honest answer: nothing can back up
                to it until the storage exists on a PVE. */}
            {saved && isOrphanPbs(draft.id, config?.pves ?? []) && (
              <div className="warn">
                <span className="wi">⚠</span>
                <span>{t('wizard.pbs.orphan', { id: draft.id })}</span>
              </div>
            )}
          </>
        )}
      </div>
    </WizardShell>
  )
}
