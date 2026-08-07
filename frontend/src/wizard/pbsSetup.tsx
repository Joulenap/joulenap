import { useCallback, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, api } from '../api/client'
import { Toggle } from '../components/Toggle'
import { useWolInterfaces } from '../hooks/useWolInterfaces'
import type { PbsDraft } from '../utils/wizardFlow'
import { Field, type ErrorFor, TextField } from './fields'

/**
 * The PBS half of the wizard, shared by both flows.
 *
 * Flow A's "Configure PBS" step and flow B's Wake-up + Power-off steps collect exactly the same
 * things — MAC, WoL interface, the app's SSH key, the host key — over exactly the same calls;
 * flow B just spreads them over two steps. Keeping one copy here means a fix to the host-key
 * dance or the MAC detection lands in both flows at once.
 */

export interface KeyInfo {
  public_key: string
  authorized_keys_line: string
  key_path: string
  /** False when the wizard reused the key that was already on disk — which is what adding a
   *  second PBS does, and what keeps the first one's power-off working. */
  created: boolean
}

interface HostKey {
  key_type: string
  key_base64: string
  fingerprint: string
}

export interface PbsSetup {
  draft: PbsDraft
  patch: (next: Partial<PbsDraft>) => void
  key: KeyInfo | null
  hostKey: HostKey | null
  hostTrusted: boolean
  /** A transient one-liner under the control that produced it (MAC found, packet sent, …). */
  note: string | null
  /** Which async action is in flight, so only its own button spins. */
  busy: string | null
  error: string | null
  detectMac: () => Promise<void>
  testWol: () => Promise<void>
  /** Fetch (or generate) the app's keypair — idempotent, so entering the step twice is free. */
  loadKey: () => Promise<KeyInfo | null>
  scanHostKey: () => Promise<void>
  trustHostKey: () => Promise<void>
  clearError: () => void
}

export function usePbsSetup(
  draft: PbsDraft,
  setDraft: (next: PbsDraft) => void,
): PbsSetup {
  const { t } = useTranslation()
  const [key, setKey] = useState<KeyInfo | null>(null)
  const [hostKey, setHostKey] = useState<HostKey | null>(null)
  const [hostTrusted, setHostTrusted] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The keygen callback outlives the render that created it, so it must not close over a stale
  // draft — it would drop whatever the user typed while the request was in flight.
  const draftRef = useRef(draft)
  draftRef.current = draft

  const patch = useCallback(
    (next: Partial<PbsDraft>) => {
      setDraft({ ...draft, ...next })
      // A host change invalidates a host key that was scanned from the old one.
      if (next.host !== undefined && next.host !== draft.host) {
        setHostKey(null)
        setHostTrusted(false)
      }
    },
    [draft, setDraft],
  )

  const run = useCallback(async function run<T>(tag: string, fn: () => Promise<T>) {
    setBusy(tag)
    setError(null)
    try {
      return await fn()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      return null
    } finally {
      setBusy(null)
    }
  }, [])

  const detectMac = useCallback(async () => {
    setNote(null)
    const r = await run('mac', () => api.wizardDetectMac(draft.host))
    if (!r) return
    if (r.mac) {
      setDraft({ ...draft, mac: r.mac })
      setNote(t('wizard.wake.macDetected'))
    } else {
      // Not an error: the box being off is the normal case, and the field is typeable.
      setNote(t('wizard.err.macNotDetected'))
    }
  }, [draft, run, setDraft, t])

  const testWol = useCallback(async () => {
    setNote(null)
    const r = await run('wol', () => api.wizardWolTest(draft.mac, draft.host, draft.wolIface))
    if (r) setNote(t('wizard.wake.wolSent', { broadcast: r.broadcast }))
  }, [draft.host, draft.mac, draft.wolIface, run, t])

  // One in-flight request, ever: the step that needs the key fetches it on open *and* the
  // install path asks for it again, and React may render either twice. Held in a ref rather
  // than in state because the guard has to be true before the next render, not after it.
  const keyRequest = useRef<Promise<KeyInfo | null> | null>(null)
  const loadKey = useCallback(() => {
    if (!keyRequest.current) {
      keyRequest.current = run('key', () => api.wizardKeygen()).then((r) => {
        if (r) {
          setKey(r)
          // The device stores the path, not the key — one keypair serves every PBS.
          setDraft({ ...draftRef.current, sshKeyPath: r.key_path })
        } else {
          keyRequest.current = null // let the user retry a failed keygen
        }
        return r
      })
    }
    return keyRequest.current
  }, [run, setDraft])

  const scanHostKey = useCallback(async () => {
    const r = await run('hostkey', () => api.wizardSshHostkey(draft.host))
    if (r) {
      setHostKey(r)
      setHostTrusted(false)
    }
  }, [draft.host, run])

  const trustHostKey = useCallback(async () => {
    if (!hostKey) return
    const r = await run('trust', () =>
      api.wizardSshTrust({
        host: draft.host,
        key_type: hostKey.key_type,
        key_base64: hostKey.key_base64,
      }),
    )
    if (r) setHostTrusted(true)
  }, [draft.host, hostKey, run])

  return {
    draft,
    patch,
    key,
    hostKey,
    hostTrusted,
    note,
    busy,
    error,
    detectMac,
    testWol,
    loadKey,
    scanHostKey,
    trustHostKey,
    clearError: () => setError(null),
  }
}

/** Wake-on-LAN: the MAC (auto-detected when the box is up), the interface, and the reminder
 *  that the BIOS/NIC half of this is not something Joulenap can do remotely. */
export function WakeFields({ setup, errorFor }: { setup: PbsSetup; errorFor: ErrorFor }) {
  const { t } = useTranslation()
  const { draft, patch, busy, note } = setup
  const ifaces = useWolInterfaces(draft.wolIface)
  return (
    <>
      <div className="frow">
        <TextField
          id="wiz-mac"
          label={t('wizard.field.mac')}
          value={draft.mac}
          onChange={(v) => patch({ mac: v })}
          placeholder="00:11:22:33:44:55"
          error={errorFor('mac')}
          help={note ?? t('wizard.wake.macHelp')}
          trailing={
            <>
              <button
                type="button"
                className="btn"
                disabled={!draft.host || busy !== null}
                onClick={() => void setup.detectMac()}
              >
                {t(busy === 'mac' ? 'wizard.wake.detecting' : 'wizard.wake.detect')}
              </button>
              {/* Not in the mockup. Wake-on-LAN is the setting most often left un-armed in the
                  BIOS, and finding that out at 04:00 from a failed run is the worst way. */}
              <button
                type="button"
                className="btn"
                disabled={!draft.mac || busy !== null}
                onClick={() => void setup.testWol()}
              >
                {t('wizard.wake.test')}
              </button>
            </>
          }
        />
        {/* A list, not free text: a mistyped NIC name was accepted and then silently fell
            back to auto-detection, with nothing on screen to say so. */}
        <Field
          id="wiz-wol-iface"
          label={t('wizard.field.wolIface')}
          help={t('wizard.wake.ifaceHint')}
        >
          <select
            id="wiz-wol-iface"
            className="in-mono"
            value={draft.wolIface}
            onChange={(e) => patch({ wolIface: e.target.value })}
          >
            <option value="">{t('settings.devices.auto')}</option>
            {ifaces.map((i) => (
              <option key={i.value} value={i.value}>
                {i.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="warn">
        <span className="wi">⚠</span>
        <span>{t('wizard.wake.biosWarning')}</span>
      </div>
    </>
  )
}

/** The power-off key: what will be installed, how, and the host key that gets pinned first. */
export function SshKeyFields({ setup }: { setup: PbsSetup }) {
  const { t } = useTranslation()
  const { draft, patch, key, hostKey, hostTrusted, busy } = setup

  return (
    <div className="field">
      <span className="lab">{t('wizard.ssh.title')}</span>
      {/* Only in auto-install mode. In manual mode the block below shows the *restricted*
          authorized_keys line, and rendering the bare public key above it invited copying
          the wrong one — which installs this key as an unrestricted root key. One keypair
          serves every managed box, so that single mistake plus a leaked private key is root
          on that PBS, exactly what the forced-command line exists to prevent. */}
      {draft.autoInstall && (
        <div className="keyblock">{key ? key.public_key : t('common.loading')}</div>
      )}
      {/* Reusing the existing key is the *correct* outcome for a second PBS — say so, or it
          looks like the wizard failed to make a fresh one. */}
      {key && !key.created && <span className="help">{t('wizard.ssh.reused')}</span>}

      <label className="tglrow" style={{ marginTop: 4 }}>
        <Toggle
          on={draft.autoInstall}
          onClick={() => patch({ autoInstall: !draft.autoInstall })}
          size="sm"
        />
        <span>{t('wizard.ssh.autoInstall')}</span>
      </label>

      {draft.autoInstall ? (
        <>
          <span className="help">{t('wizard.ssh.hostKeyWhy')}</span>
          <div className="inline-row" style={{ alignItems: 'center', gap: 10 }}>
            <button
              type="button"
              className="btn"
              disabled={!draft.host || busy !== null}
              onClick={() => void setup.scanHostKey()}
            >
              {t(
                busy === 'hostkey'
                  ? 'wizard.ssh.scanning'
                  : 'wizard.ssh.scanHostKey',
              )}
            </button>
            {hostKey && !hostTrusted && (
              <button
                type="button"
                className="btn btn-accent"
                disabled={busy !== null}
                onClick={() => void setup.trustHostKey()}
              >
                {t('wizard.ssh.confirmHostKey')}
              </button>
            )}
            {hostTrusted && <span className="okline">✓ {t('wizard.ssh.hostKeyTrusted')}</span>}
          </div>
          {hostKey && (
            <div className="keyblock">
              {hostKey.key_type} · {hostKey.fingerprint}
            </div>
          )}
        </>
      ) : (
        <>
          <span className="help">{t('wizard.ssh.manualHint')}</span>
          <div className="keyblock">{key?.authorized_keys_line ?? ''}</div>
        </>
      )}
    </div>
  )
}

/** The `managed_power` toggle and its explanation. Off hides everything it governs rather than
 *  showing it inert — same call `DeviceModal` makes, and the backend validator agrees. */
export function ManagedPowerToggle({ setup }: { setup: PbsSetup }) {
  const { t } = useTranslation()
  return (
    <>
      <label className="tglrow">
        <Toggle
          on={setup.draft.managedPower}
          onClick={() => setup.patch({ managedPower: !setup.draft.managedPower })}
          size="sm"
        />
        <span>{t('wizard.power.managed')}</span>
      </label>
      <span className="help" style={{ marginTop: -8 }}>
        {t('wizard.power.managedHint')}
      </span>
    </>
  )
}
